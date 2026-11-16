"""Environment-driven settings for WhaleRadar."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_WS_URL = "wss://socket.bayse.markets/ws/v1/markets"
DEFAULT_EVENT_ID = "c03ea595-a209-4fe9-a0e1-bd77f3154927"
DEFAULT_MARKET_BASE_URL = "https://www.bayse.markets/market"
DEFAULT_STATE_DB = "data/whale_radar.sqlite3"

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or malformed."""


@dataclass(frozen=True, slots=True)
class TwitterCredentials:
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str

    @property
    def complete(self) -> bool:
        return all(
            (
                self.api_key,
                self.api_secret,
                self.access_token,
                self.access_token_secret,
            )
        )


@dataclass(frozen=True, slots=True)
class Settings:
    ws_url: str
    event_ids: tuple[str, ...]
    min_order_ngn: Decimal
    usd_ngn_fallback: Decimal
    fx_refresh_seconds: int
    market_base_url: str
    state_db: Path
    log_level: str
    dry_run: bool
    all_markets: bool
    twitter: TwitterCredentials
    reconnect_base_seconds: float
    reconnect_max_seconds: float
    max_reconnect_attempts: int
    queue_size: int

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from ``.env`` plus the process environment."""
        load_dotenv()

        credentials = TwitterCredentials(
            api_key=_text("TWITTER_API_KEY") or "",
            api_secret=_text("TWITTER_API_SECRET") or "",
            access_token=_text("TWITTER_ACCESS_TOKEN") or "",
            access_token_secret=_text("TWITTER_ACCESS_TOKEN_SECRET") or "",
        )

        dry_run_override = _optional_bool("TWITTER_DRY_RUN")
        if dry_run_override is None:
            dry_run = not credentials.complete
        else:
            dry_run = dry_run_override
            if not dry_run and not credentials.complete:
                raise ConfigError(
                    "TWITTER_DRY_RUN=false requires TWITTER_API_KEY, "
                    "TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN and "
                    "TWITTER_ACCESS_TOKEN_SECRET."
                )

        log_level = (_text("LOG_LEVEL") or "INFO").upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigError(
                f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}."
            )

        min_order = _decimal("MIN_ORDER_NGN", Decimal("100000"))
        if min_order <= 0:
            raise ConfigError("MIN_ORDER_NGN must be greater than zero.")

        fallback_rate = _decimal("USD_NGN_FALLBACK", Decimal("1500"))
        if fallback_rate <= 0:
            raise ConfigError("USD_NGN_FALLBACK must be greater than zero.")

        base_delay = _float("RECONNECT_BASE_SECONDS", 5.0, minimum=0.5)
        max_delay = _float("RECONNECT_MAX_SECONDS", 60.0, minimum=1.0)
        if max_delay < base_delay:
            raise ConfigError(
                "RECONNECT_MAX_SECONDS must be greater than or equal to "
                "RECONNECT_BASE_SECONDS."
            )

        event_ids = _event_ids()
        if not event_ids:
            raise ConfigError("At least one event ID is required.")

        return cls(
            ws_url=_text("BAYSE_WS_URL") or DEFAULT_WS_URL,
            event_ids=event_ids,
            min_order_ngn=min_order,
            usd_ngn_fallback=fallback_rate,
            fx_refresh_seconds=_integer(
                "FX_REFRESH_SECONDS", 6 * 60 * 60, minimum=60
            ),
            market_base_url=(
                _text("BAYSE_MARKET_BASE_URL") or DEFAULT_MARKET_BASE_URL
            ).rstrip("/"),
            state_db=Path(
                _text("STATE_DB") or DEFAULT_STATE_DB
            ).expanduser(),
            log_level=log_level,
            dry_run=dry_run,
            all_markets=_boolean("BAYSE_ALL_MARKETS", False),
            twitter=credentials,
            reconnect_base_seconds=base_delay,
            reconnect_max_seconds=max_delay,
            max_reconnect_attempts=_integer(
                "RECONNECT_MAX_ATTEMPTS", 0, minimum=0
            ),
            queue_size=_integer("QUEUE_SIZE", 1000, minimum=10),
        )


def _text(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _integer(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _text(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    return value


def _float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = _text(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    return value


def _decimal(name: str, default: Decimal) -> Decimal:
    raw = _text(name)
    if raw is None:
        return default
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from exc
    if not value.is_finite():
        raise ConfigError(f"{name} must be a finite number.")
    return value


def _boolean(name: str, default: bool) -> bool:
    value = _optional_bool(name)
    return default if value is None else value


def _optional_bool(name: str) -> bool | None:
    raw = _text(name)
    if raw is None:
        return None
    value = raw.lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}.")


def _event_ids() -> tuple[str, ...]:
    raw = _text("BAYSE_EVENT_IDS") or DEFAULT_EVENT_ID
    # dict.fromkeys keeps the original order while removing duplicates.
    return tuple(
        dict.fromkeys(part.strip() for part in raw.split(",") if part.strip())
    )
