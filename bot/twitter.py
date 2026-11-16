"""Thin Tweepy wrapper with retries, rate-limit handling and dry-run support."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import tweepy

from bot.config import TwitterCredentials
from bot.formatting import MAX_TWEET_WEIGHT, twitter_weight

logger = logging.getLogger(__name__)

_MAX_RATE_LIMIT_WAIT = 120
_MAX_ATTEMPTS = 3


class TwitterError(RuntimeError):
    """Raised when a tweet could not be published."""


@dataclass(frozen=True, slots=True)
class PublishResult:
    text: str
    tweet_id: str | None
    dry_run: bool


class TwitterPublisher:
    def __init__(
        self,
        credentials: TwitterCredentials,
        *,
        dry_run: bool,
    ) -> None:
        self._dry_run = dry_run
        self._lock = asyncio.Lock()
        self._client: tweepy.Client | None = None

        if not dry_run:
            if not credentials.complete:
                raise TwitterError("Twitter credentials are incomplete.")
            self._client = tweepy.Client(
                consumer_key=credentials.api_key,
                consumer_secret=credentials.api_secret,
                access_token=credentials.access_token,
                access_token_secret=credentials.access_token_secret,
                wait_on_rate_limit=False,
            )

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    async def publish(self, text: str) -> PublishResult:
        weight = twitter_weight(text)
        if weight > MAX_TWEET_WEIGHT:
            raise TwitterError(
                f"Refusing to post: tweet is {weight}/{MAX_TWEET_WEIGHT} "
                "weighted characters."
            )

        async with self._lock:
            if self._dry_run or self._client is None:
                logger.info(
                    "[DRY RUN] Would post (%d/%d):\n%s",
                    weight,
                    MAX_TWEET_WEIGHT,
                    text,
                )
                return PublishResult(text=text, tweet_id=None, dry_run=True)

            tweet_text = text
            last_error: Exception | None = None

            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    tweet_id = await asyncio.to_thread(
                        self._create_tweet, tweet_text
                    )
                except tweepy.Forbidden as exc:
                    if attempt == 1 and _is_duplicate_content(exc):
                        candidate = _with_timestamp_suffix(text)
                        if candidate != text:
                            logger.warning(
                                "X rejected duplicate text; retrying with a "
                                "timestamp suffix."
                            )
                            tweet_text = candidate
                            continue
                    raise TwitterError(
                        f"X refused the post (check app permissions and "
                        f"account status): {exc}"
                    ) from exc
                except tweepy.Unauthorized as exc:
                    raise TwitterError(
                        "X authentication failed. Check TWITTER_* credentials "
                        "and make sure the access token was generated with "
                        "'Read and write' permission."
                    ) from exc
                except tweepy.TooManyRequests as exc:
                    last_error = exc
                    wait = _rate_limit_wait(exc)
                    if attempt == _MAX_ATTEMPTS or wait > _MAX_RATE_LIMIT_WAIT:
                        break
                    logger.warning(
                        "X rate limit hit; retrying in %ss (attempt %d/%d).",
                        wait,
                        attempt,
                        _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(wait)
                except tweepy.TweepyException as exc:
                    last_error = exc
                    if attempt == _MAX_ATTEMPTS:
                        break
                    wait = min(2**attempt, 10)
                    logger.warning(
                        "X request failed (%s); retrying in %ss "
                        "(attempt %d/%d).",
                        exc,
                        wait,
                        attempt,
                        _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.info(
                        "Tweet published: id=%s (%d/%d characters).",
                        tweet_id,
                        twitter_weight(tweet_text),
                        MAX_TWEET_WEIGHT,
                    )
                    return PublishResult(
                        text=tweet_text, tweet_id=tweet_id, dry_run=False
                    )

            raise TwitterError(
                f"Could not publish tweet after {_MAX_ATTEMPTS} attempts: "
                f"{last_error}"
            )

    def _create_tweet(self, text: str) -> str:
        assert self._client is not None
        response = self._client.create_tweet(text=text)
        data = response.data if hasattr(response, "data") else response
        if not isinstance(data, dict):
            raise TwitterError("X returned an unexpected response.")
        tweet_id = data.get("id")
        if not tweet_id:
            raise TwitterError("X returned no tweet ID.")
        return str(tweet_id)


def _is_duplicate_content(exc: tweepy.Forbidden) -> bool:
    return "duplicate" in str(exc).lower()


def _with_timestamp_suffix(text: str) -> str:
    suffix = " · " + datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    first_line, separator, rest = text.partition("\n")
    candidate = f"{first_line}{suffix}{separator}{rest}"
    if twitter_weight(candidate) <= MAX_TWEET_WEIGHT:
        return candidate

    candidate = text + suffix
    if twitter_weight(candidate) <= MAX_TWEET_WEIGHT:
        return candidate
    return text


def _rate_limit_wait(exc: tweepy.TooManyRequests) -> int:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}

    reset = headers.get("x-rate-limit-reset")
    if reset:
        try:
            return max(1, int(float(reset)) - int(time.time()) + 2)
        except (TypeError, ValueError):
            pass

    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except (TypeError, ValueError):
            pass

    return 30
