
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from bot import __version__
from bot.config import ConfigError, Settings
from bot.detector import WhaleDetector
from bot.fx import FxRateProvider
from bot.logging_setup import configure_logging
from bot.store import OrderStore
from bot.twitter import TwitterPublisher
from bot.websocket.client import BayseMarketStream

logger = logging.getLogger(__name__)


async def run(stop_event: asyncio.Event | None = None) -> None:
    """Run the bot until ``stop_event`` is set (or Ctrl+C is pressed)."""
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    stop = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    restore_signals = _install_signal_handlers(loop, stop)

    store: OrderStore | None = None
    tasks: list[asyncio.Task[Any]] = []

    try:
        store = OrderStore(settings.state_db)
        stale = store.fail_stale_pending()
        if stale:
            logger.info(
                "Released %d stale pending order(s) from the state store.",
                stale,
            )

        _log_startup(settings)

        fx = FxRateProvider(
            settings.usd_ngn_fallback,
            refresh_seconds=settings.fx_refresh_seconds,
        )
        await fx.refresh()

        publisher = TwitterPublisher(settings.twitter, dry_run=settings.dry_run)
        detector = WhaleDetector(
            min_order_ngn=settings.min_order_ngn,
            market_base_url=settings.market_base_url,
            fx=fx,
            store=store,
            publisher=publisher,
            event_ids=settings.event_ids,
            all_markets=settings.all_markets,
        )

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=settings.queue_size
        )
        stream = BayseMarketStream(
            url=settings.ws_url,
            event_ids=settings.event_ids,
            on_message=_make_enqueuer(queue),
            stop_event=stop,
            reconnect_base_seconds=settings.reconnect_base_seconds,
            reconnect_max_seconds=settings.reconnect_max_seconds,
            max_reconnect_attempts=settings.max_reconnect_attempts,
        )

        tasks = [
            asyncio.create_task(stream.run(), name="bayse-stream"),
            asyncio.create_task(
                _process_queue(queue, detector, stop), name="whale-worker"
            ),
            asyncio.create_task(fx.run(stop), name="fx-refresh"),
        ]
        await _wait_for_exit(stop, tasks)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if store is not None:
            store.close()
        restore_signals()
        logger.info("WhaleRadar stopped. WebSocket connection closed.")


def _log_startup(settings: Settings) -> None:
    logger.info("WhaleRadar v%s starting.", __version__)
    if settings.all_markets:
        logger.info("Market scope: ALL Bayse markets.")
    else:
        logger.info(
            "Market scope: %d event(s): %s",
            len(settings.event_ids),
            ", ".join(settings.event_ids),
        )
    logger.info(
        "Alert threshold: orders above ₦%s.",
        f"{settings.min_order_ngn:,.0f}",
    )
    logger.info(
        "X posting: %s",
        "DRY RUN (no tweets will be sent)"
        if settings.dry_run
        else "LIVE",
    )
    logger.info("State database: %s", settings.state_db)


def _make_enqueuer(
    queue: asyncio.Queue[dict[str, Any]],
):
    async def enqueue(message: dict[str, Any]) -> None:
        if queue.full():
            # Prefer recent alerts over stale buffered ones if X is slow.
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                pass
            logger.warning("Activity queue is full; dropped the oldest frame.")
        queue.put_nowait(message)

    return enqueue


async def _process_queue(
    queue: asyncio.Queue[dict[str, Any]],
    detector: WhaleDetector,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            message = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue

        try:
            await detector.handle_raw(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("Unhandled error while processing activity.")
        finally:
            queue.task_done()


async def _wait_for_exit(
    stop: asyncio.Event, tasks: list[asyncio.Task[Any]]
) -> None:
    """Wait for shutdown, or for any background task to stop on its own."""
    stop_task = asyncio.create_task(stop.wait(), name="shutdown-waiter")
    try:
        done, _ = await asyncio.wait(
            [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if task is stop_task:
                continue
            if task.cancelled():
                logger.warning("Background task %r was cancelled.", task.get_name())
                continue
            error = task.exception()
            if error is not None:
                logger.error(
                    "Background task %r failed: %s",
                    task.get_name(),
                    error,
                    exc_info=error,
                )
            else:
                logger.info(
                    "Background task %r finished.", task.get_name()
                )
        stop.set()
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event
):
    """Turn SIGINT/SIGTERM into a graceful stop instead of a traceback."""
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: Any) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        logger.info("Received %s; shutting down...", name)
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[int(signum)] = signal.signal(signum, handler)
        except (ValueError, OSError):
            logger.debug("Could not install %s handler.", name)

    def restore() -> None:
        for signum, old_handler in previous.items():
            try:
                signal.signal(signum, old_handler)
            except (ValueError, OSError):
                pass

    return restore


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        logger.info("Interrupted. WhaleRadar stopped.")
