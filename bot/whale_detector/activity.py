"""Parse Bayse activity feed messages into :class:`OrderActivity` objects."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bot.models import OrderActivity

logger = logging.getLogger(__name__)

_ORDER_TYPES = {"buy_order": "BUY", "sell_order": "SELL"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _timestamp(message: dict[str, Any], order: dict[str, Any]) -> datetime:
    raw = message.get("timestamp")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(float(raw) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass

    for key in ("updatedAt", "createdAt"):
        raw = order.get(key)
        if isinstance(raw, str) and raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

    return datetime.now(timezone.utc)


def _derived_order_id(
    event: dict[str, Any],
    market: dict[str, Any],
    user: dict[str, Any],
    side: str,
    amount: Decimal,
    order: dict[str, Any],
) -> str:
    seed = "|".join(
        (
            _text(event.get("id")),
            _text(market.get("id")),
            _text(user.get("id")),
            side,
            str(amount),
            _text(order.get("createdAt")),
        )
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]
    return f"derived-{digest}"


def parse_activity(message: Any) -> OrderActivity | None:
    """Return an :class:`OrderActivity` for buy/sell messages, else ``None``.

    Handles both the public ``activity`` channel shape and the
    ``data.payload`` shape used by the authenticated ``user_trades`` channel.
    """
    if not isinstance(message, dict):
        return None

    kind = _text(message.get("type")).lower()
    side = _ORDER_TYPES.get(kind)
    if side is None:
        return None

    data = _dict(message.get("data"))
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = payload

    order = _dict(data.get("order"))
    user = _dict(data.get("user"))
    event = _dict(data.get("event"))
    market = _dict(data.get("market"))

    amount = _decimal(order.get("amount"))
    quantity = _decimal(order.get("quantity"))
    price = _decimal(order.get("price"))
    if amount is None and quantity is not None and price is not None:
        amount = (quantity * price).quantize(Decimal("0.01"))
    if amount is None:
        logger.debug("Ignoring %s activity without an amount: %s", kind, order)
        return None

    order_id = _text(order.get("id"))
    if not order_id:
        order_id = _derived_order_id(event, market, user, side, amount, order)

    outcome = _text(order.get("outcome"))
    outcome_label = _text(order.get("outcomeLabel")) or outcome

    return OrderActivity(
        order_id=order_id,
        side=side,
        status=_text(order.get("status")).upper(),
        amount=amount,
        currency=_text(order.get("currency")).upper(),
        quantity=quantity,
        price=price,
        outcome=outcome,
        outcome_label=outcome_label,
        event_id=_text(event.get("id")),
        event_slug=_text(event.get("slug")),
        event_title=_text(event.get("title")),
        market_id=_text(market.get("id")),
        market_title=_text(market.get("title")),
        user_id=_text(user.get("id")),
        user_tag=_text(user.get("tag")),
        timestamp=_timestamp(message, order),
    )


def dump_for_debug(message: Any) -> str:
    """Compact JSON rendering used in debug logs and tests."""
    return json.dumps(message, ensure_ascii=False, default=str)
