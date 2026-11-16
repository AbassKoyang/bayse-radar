"""Read-only Bayse WebSocket diagnostic.

Usage:
    pipenv run python scripts/probe_feed.py
    pipenv run python scripts/probe_feed.py --seconds 120
    pipenv run python scripts/probe_feed.py --event <event-uuid>

This tool deliberately does NOT send client ping frames. Bayse resets
connections that receive them, which is why the old bot disconnected every
20 seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import websockets
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import DEFAULT_EVENT_ID, DEFAULT_WS_URL  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds",
        type=int,
        default=120,
        help="How long to listen (0 = until Ctrl+C). Default: 120.",
    )
    parser.add_argument(
        "--event",
        action="append",
        dest="events",
        help="Event UUID to watch. Can be repeated. Defaults to .env or the built-in event.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also subscribe to the global all_activity feed used by the bot.",
    )
    return parser.parse_args()


async def wait_for_connected(websocket) -> None:
    """Wait for Bayse's readiness frame before subscribing.

    Subscriptions sent before this frame are not always honoured.
    """
    try:
        async with asyncio.timeout(30):
            while True:
                raw = await websocket.recv()
                print(raw)
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(message, dict)
                    and message.get("type") == "connected"
                ):
                    return
    except TimeoutError:
        print("No connected frame received; subscribing anyway.")


async def probe(
    url: str, events: list[str], seconds: int, all_activity: bool
) -> None:
    async with websockets.connect(
        url,
        ping_interval=None,  # never send client pings to Bayse
        ping_timeout=None,
        close_timeout=5,
        open_timeout=20,
    ) as websocket:
        print(f"Connecting to {url}")
        await wait_for_connected(websocket)

        if all_activity:
            await websocket.send(
                json.dumps({"type": "subscribe", "channel": "all_activity"})
            )
        for event_id in events:
            await websocket.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "channel": "activity",
                        "eventId": event_id,
                    }
                )
            )
        scopes = ([f"{len(events)} event(s)"] if events else []) + (
            ["global all_activity"] if all_activity else []
        )
        print(f"Subscribed to: {', '.join(scopes) or 'nothing'}")
        print("Waiting for messages (Ctrl+C to stop)...\n")

        try:
            if seconds > 0:
                async with asyncio.timeout(seconds):
                    async for raw in websocket:
                        print(raw)
            else:
                async for raw in websocket:
                    print(raw)
        except TimeoutError:
            print(f"\n{seconds}s elapsed. Connection was still open.")


def main() -> None:
    load_dotenv()
    args = parse_args()
    url = os.getenv("BAYSE_WS_URL") or DEFAULT_WS_URL
    raw_events = os.getenv("BAYSE_EVENT_IDS") or DEFAULT_EVENT_ID
    events = args.events or [
        part.strip() for part in raw_events.split(",") if part.strip()
    ]

    try:
        asyncio.run(probe(url, events, args.seconds, args.all))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
