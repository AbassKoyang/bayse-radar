"""USD/NGN exchange-rate provider.

The bot's threshold is denominated in naira, so USD orders have to be
converted. A free no-key endpoint is used, with a configurable fallback for
offline starts and transient failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_RATE_URL = "https://open.er-api.com/v6/latest/USD"
_USER_AGENT = "BayseWhaleRadar/1.0"


class FxRateProvider:
    def __init__(
        self,
        fallback_rate: Decimal,
        *,
        refresh_seconds: int = 6 * 60 * 60,
        url: str = _RATE_URL,
    ) -> None:
        self._rate = fallback_rate
        self._fallback = fallback_rate
        self._refresh_seconds = refresh_seconds
        self._url = url
        self._updated_at: datetime | None = None
        self._source = "fallback"
        self._lock = asyncio.Lock()

    @property
    def rate(self) -> Decimal:
        return self._rate

    @property
    def source(self) -> str:
        return self._source

    async def refresh(self) -> bool:
        """Fetch a fresh rate. Returns ``True`` when the update succeeded."""
        async with self._lock:
            try:
                rate = await asyncio.to_thread(self._fetch_sync)
            except Exception as exc:  # network errors must never stop the bot
                logger.warning(
                    "Could not refresh USD/NGN rate (%s); continuing with "
                    "₦%s/$ (source: %s).",
                    exc,
                    f"{self._rate:,.2f}",
                    self._source,
                )
                return False

            self._rate = rate
            self._source = "open.er-api.com"
            self._updated_at = datetime.now(timezone.utc)
            logger.info(
                "USD/NGN rate updated: ₦%s/$ (source: %s).",
                f"{rate:,.2f}",
                self._source,
            )
            return True

    async def run(self, stop_event: asyncio.Event) -> None:
        """Refresh periodically until shutdown is requested."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._refresh_seconds
                )
            except asyncio.TimeoutError:
                await self.refresh()

    def _fetch_sync(self) -> Decimal:
        request = urllib.request.Request(
            self._url, headers={"User-Agent": _USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)

        if payload.get("result") not in (None, "success"):
            raise ValueError(
                f"provider returned result={payload.get('result')!r}"
            )

        try:
            rate = Decimal(str(payload["rates"]["NGN"]))
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError("provider response did not include rates.NGN") from exc

        if not rate.is_finite() or rate <= 0:
            raise ValueError(f"invalid NGN rate: {rate!r}")
        return rate
