"""Whale detection pipeline: parse, threshold, de-duplicate, post."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from bot.formatting import format_tweet
from bot.models import ngn_value
from bot.store import ClaimOutcome, OrderStore
from bot.twitter import TwitterError, TwitterPublisher
from bot.whale_detector.activity import parse_activity

logger = logging.getLogger(__name__)


class WhaleDetector:
    def __init__(
        self,
        *,
        min_order_ngn: Decimal,
        market_base_url: str,
        fx,
        store: OrderStore,
        publisher: TwitterPublisher,
        event_ids: tuple[str, ...] = (),
        all_markets: bool = True,
    ) -> None:
        self._min_order_ngn = min_order_ngn
        self._market_base_url = market_base_url
        self._fx = fx
        self._store = store
        self._publisher = publisher
        self._event_ids = frozenset(event_ids)
        self._all_markets = all_markets

    async def handle_raw(self, message: Any) -> None:
        activity = parse_activity(message)
        if activity is None:
            return

        if (
            not self._all_markets
            and self._event_ids
            and activity.event_id not in self._event_ids
        ):
            logger.debug(
                "Ignoring order %s from unconfigured event %r.",
                activity.order_id,
                activity.event_id,
            )
            return

        if activity.status != "FILLED":
            logger.debug(
                "Ignoring order %s with status %r.",
                activity.order_id,
                activity.status,
            )
            return

        amount_ngn = ngn_value(activity, self._fx.rate)
        if amount_ngn is None:
            logger.warning(
                "Ignoring order %s: unsupported currency %r.",
                activity.order_id,
                activity.currency,
            )
            return

        if amount_ngn <= self._min_order_ngn:
            logger.debug(
                "Order %s is ₦%s, below the ₦%s threshold.",
                activity.order_id,
                f"{amount_ngn:,.2f}",
                f"{self._min_order_ngn:,.0f}",
            )
            return

        claim = self._store.claim(
            activity.order_id,
            event_id=activity.event_id,
            side=activity.side,
            currency=activity.currency,
            amount=activity.amount,
            amount_ngn=amount_ngn,
        )
        if claim is not ClaimOutcome.CLAIMED:
            logger.info(
                "Skipping order %s: already handled (%s).",
                activity.order_id,
                claim.value,
            )
            return

        logger.info(
            "Whale detected: %s %s %s | %s | %s | ₦%s | order=%s",
            activity.side,
            activity.currency,
            f"{activity.amount:,.2f}",
            activity.event_title or activity.event_id,
            activity.market_title or activity.outcome,
            f"{amount_ngn:,.0f}",
            activity.order_id,
        )

        text = format_tweet(
            activity, amount_ngn, self._market_base_url
        )

        try:
            result = await self._publisher.publish(text)
        except TwitterError as exc:
            self._store.mark_failed(activity.order_id, str(exc))
            logger.error(
                "Could not post whale alert for order %s: %s",
                activity.order_id,
                exc,
            )
            return
        except Exception as exc:  # pragma: no cover - defensive guard
            self._store.mark_failed(activity.order_id, repr(exc))
            logger.exception(
                "Unexpected error while posting order %s.", activity.order_id
            )
            return

        if result.dry_run:
            self._store.mark_dry_run(activity.order_id, result.text)
            logger.info(
                "Dry-run alert recorded for order %s (no tweet sent).",
                activity.order_id,
            )
        else:
            self._store.mark_posted(
                activity.order_id, result.tweet_id or "", result.text
            )
            logger.info(
                "Tweeted whale alert for order %s: "
                "https://x.com/i/status/%s",
                activity.order_id,
                result.tweet_id,
            )
