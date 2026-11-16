"""Resilient Bayse market WebSocket stream.

Important: Bayse resets a connection when it receives a client-originated ping
frame. Do not re-enable ``ping_interval`` and do not send ``{"type": "ping"}``;
the server sends its own WebSocket pings and the ``websockets`` library replies
to them automatically.

The bot subscribes to the global ``all_activity`` feed (which carries orders
from every market) and, additionally, to each configured event's ``activity``
room. Duplicate deliveries are removed by the SQLite idempotency store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

_ACTIVITY_TYPES = {"buy_order", "sell_order"}
_IGNORED_TYPES = {"connected", "pong", "subscribed", "unsubscribed", "ping"}

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class BayseMarketStream:
    def __init__(
        self,
        *,
        url: str,
        event_ids: tuple[str, ...],
        on_message: MessageHandler,
        stop_event: asyncio.Event,
        subscribe_all_activity: bool = True,
        reconnect_base_seconds: float = 5.0,
        reconnect_max_seconds: float = 60.0,
        max_reconnect_attempts: int = 0,
        stable_after_seconds: float = 60.0,
        open_timeout: float = 20.0,
        connected_timeout: float = 30.0,
    ) -> None:
        self._url = url
        self._event_ids = event_ids
        self._on_message = on_message
        self._stop = stop_event
        self._subscribe_all_activity = subscribe_all_activity
        self._reconnect_base = reconnect_base_seconds
        self._reconnect_max = reconnect_max_seconds
        self._max_attempts = max_reconnect_attempts
        self._stable_after = stable_after_seconds
        self._open_timeout = open_timeout
        self._connected_timeout = connected_timeout

    async def run(self) -> None:
        failures = 0

        while not self._stop.is_set():
            connected_at: float | None = None
            try:
                async with websockets.connect(
                    self._url,
                    # NEVER set this: client ping frames make Bayse RST the
                    # socket. Server pings are answered by the library.
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    open_timeout=self._open_timeout,
                    max_size=4 * 1024 * 1024,
                ) as websocket:
                    connected_at = monotonic()
                    logger.info("Connected to Bayse: %s", self._url)
                    await self._consume(websocket)

            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.warning(
                    "Bayse closed the connection%s.",
                    f" ({exc})" if str(exc) else "",
                )
            except (OSError, TimeoutError) as exc:
                logger.warning(
                    "Network error while connected to Bayse: %s", exc
                )
            except WebSocketException as exc:
                logger.warning("WebSocket error: %s", exc)
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Unexpected WebSocket error.")

            if self._stop.is_set():
                break

            lifetime = monotonic() - connected_at if connected_at else 0.0
            if lifetime >= self._stable_after:
                failures = 0

            if self._max_attempts and failures >= self._max_attempts:
                logger.error(
                    "Giving up after %d consecutive reconnect attempt(s).",
                    failures,
                )
                return

            delay = self._backoff(failures)
            logger.info(
                "Reconnecting in %.1fs (attempt %d%s)...",
                delay,
                failures + 1,
                "" if self._max_attempts == 0 else f"/{self._max_attempts}",
            )
            await self._sleep_or_stop(delay)
            failures += 1

        logger.info("Bayse stream stopped.")

    async def _consume(self, websocket) -> None:
        subscribed = False
        try:
            async with asyncio.timeout(self._connected_timeout):
                while not subscribed:
                    raw = await websocket.recv()
                    for message in self._decode(raw):
                        if not subscribed and self._is_connected(message):
                            await self._subscribe(websocket)
                            subscribed = True
                        await self._dispatch(message)
        except TimeoutError:
            logger.warning(
                "Bayse did not send a connected frame within %.0fs; "
                "subscribing anyway.",
                self._connected_timeout,
            )

        if not subscribed:
            await self._subscribe(websocket)

        async for raw in websocket:
            for message in self._decode(raw):
                await self._dispatch(message)

    async def _subscribe(self, websocket) -> None:
        if self._subscribe_all_activity:
            await websocket.send(
                json.dumps({"type": "subscribe", "channel": "all_activity"})
            )
            logger.info("Subscribed to the global Bayse activity feed.")

        for event_id in self._event_ids:
            await websocket.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "channel": "activity",
                        "eventId": event_id,
                    }
                )
            )
        if self._event_ids:
            logger.info(
                "Subscribed to activity for %d event(s): %s",
                len(self._event_ids),
                ", ".join(self._event_ids),
            )

    def _decode(self, raw: Any) -> list[Any]:
        text = (
            raw
            if isinstance(raw, str)
            else raw.decode("utf-8", errors="replace")
        )
        messages: list[Any] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed frame: %.200s", line)
        return messages

    @staticmethod
    def _is_connected(message: Any) -> bool:
        return isinstance(message, dict) and (
            message.get("type") == "connected"
            or message.get("status") == "connected"
        )

    async def _dispatch(self, message: Any) -> None:
        if isinstance(message, list):
            for item in message:
                await self._dispatch(item)
            return
        if not isinstance(message, dict):
            return

        kind = str(message.get("type") or "").lower()
        if kind in _ACTIVITY_TYPES:
            try:
                await self._on_message(message)
            except Exception:
                logger.exception("Activity handler failed.")
            return

        if kind == "error":
            data = message.get("data")
            detail = data.get("message") if isinstance(data, dict) else data
            logger.error("Bayse WebSocket error: %s", detail or message)
            return

        if kind in _IGNORED_TYPES:
            logger.debug("Bayse message: %s", kind)
            return

        logger.debug("Ignoring Bayse message type %r.", kind)

    def _backoff(self, failures: int) -> float:
        exponent = min(failures, 10)
        delay = min(self._reconnect_max, self._reconnect_base * (2**exponent))
        return delay * random.uniform(0.8, 1.2)

    async def _sleep_or_stop(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
