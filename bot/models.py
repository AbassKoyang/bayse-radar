"""Domain models shared by the parser, detector and formatter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class OrderActivity:
    """A single filled buy/sell order from Bayse's activity feed."""

    order_id: str
    side: str  # "BUY" or "SELL"
    status: str
    amount: Decimal
    currency: str
    quantity: Decimal | None
    price: Decimal | None
    outcome: str
    outcome_label: str
    event_id: str
    event_slug: str
    event_title: str
    market_id: str
    market_title: str
    user_id: str
    user_tag: str
    timestamp: datetime


def ngn_value(activity: OrderActivity, usd_ngn_rate: Decimal) -> Decimal | None:
    """Return the order's value in naira, or ``None`` for unsupported currency."""
    currency = activity.currency.upper()
    if currency == "NGN":
        return activity.amount
    if currency == "USD":
        return (activity.amount * usd_ngn_rate).quantize(Decimal("0.01"))
    return None
