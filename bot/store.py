"""SQLite-backed idempotency store.

The WebSocket feed can replay messages after a reconnect, and the bot may be
restarted while an order is in flight. Persisting order IDs prevents duplicate
posts.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ClaimOutcome(str, Enum):
    CLAIMED = "claimed"
    ALREADY_HANDLED = "already_handled"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL DEFAULT '',
    side        TEXT NOT NULL DEFAULT '',
    currency    TEXT NOT NULL DEFAULT '',
    amount      TEXT NOT NULL DEFAULT '',
    amount_ngn  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 1,
    tweet_id    TEXT,
    tweet_text  TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderStore:
    def __init__(self, path: Path, *, max_attempts: int = 3) -> None:
        self._path = path
        self._max_attempts = max_attempts
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(path), check_same_thread=False, timeout=10
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.executescript(_SCHEMA)

    def claim(
        self,
        order_id: str,
        *,
        event_id: str,
        side: str,
        currency: str,
        amount: Decimal,
        amount_ngn: Decimal,
    ) -> ClaimOutcome:
        """Atomically claim an order for posting.

        A failed order can be retried up to ``max_attempts`` times; a posted or
        dry-run order is never posted again.
        """
        now = _utc_now().isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO orders
                    (order_id, event_id, side, currency, amount, amount_ngn,
                     status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)
                """,
                (
                    order_id,
                    event_id,
                    side,
                    currency,
                    str(amount),
                    str(amount_ngn),
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                return ClaimOutcome.CLAIMED

            row = self._connection.execute(
                "SELECT status, attempts FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                return ClaimOutcome.FAILED

            status = row["status"]
            attempts = int(row["attempts"])

            if status in {"posted", "dry_run"}:
                return ClaimOutcome.ALREADY_HANDLED
            if status == "pending":
                return ClaimOutcome.IN_PROGRESS
            if status == "failed" and attempts < self._max_attempts:
                self._connection.execute(
                    """
                    UPDATE orders
                       SET status = 'pending', attempts = attempts + 1,
                           error = NULL, updated_at = ?
                     WHERE order_id = ?
                    """,
                    (now, order_id),
                )
                return ClaimOutcome.CLAIMED
            return ClaimOutcome.FAILED

    def mark_posted(self, order_id: str, tweet_id: str, text: str) -> None:
        self._update(
            order_id,
            "UPDATE orders SET status = 'posted', tweet_id = ?, tweet_text = ?, "
            "error = NULL, updated_at = ? WHERE order_id = ?",
            (tweet_id, text, _utc_now().isoformat(), order_id),
        )

    def mark_dry_run(self, order_id: str, text: str) -> None:
        self._update(
            order_id,
            "UPDATE orders SET status = 'dry_run', tweet_text = ?, "
            "error = NULL, updated_at = ? WHERE order_id = ?",
            (text, _utc_now().isoformat(), order_id),
        )

    def mark_failed(self, order_id: str, error: str) -> None:
        self._update(
            order_id,
            "UPDATE orders SET status = 'failed', error = ?, updated_at = ? "
            "WHERE order_id = ?",
            (error[:500], _utc_now().isoformat(), order_id),
        )

    def fail_stale_pending(self, *, max_age_seconds: int = 15 * 60) -> int:
        """Mark pending rows left behind by a crash as failed (retryable)."""
        cutoff = (
            _utc_now() - timedelta(seconds=max_age_seconds)
        ).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE orders
                   SET status = 'failed', error = 'stale pending claim',
                       updated_at = ?
                 WHERE status = 'pending' AND updated_at < ?
                """,
                (_utc_now().isoformat(), cutoff),
            )
        return cursor.rowcount

    def status(self, order_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        return row["status"] if row else None

    def _update(self, order_id: str, sql: str, params: tuple) -> None:
        with self._lock, self._connection:
            self._connection.execute(sql, params)
        logger.debug("Updated order %s in state store %s", order_id, self._path)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
