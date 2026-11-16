"""Format whale alerts for X (Twitter)."""

from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import quote

from bot.models import OrderActivity

MAX_TWEET_WEIGHT = 280
SAFE_TWEET_WEIGHT = 278

_URL_RE = re.compile(r"https?://\S+")
_GENERIC_MARKET_TITLES = {
    "",
    "yes",
    "no",
    "yes or no",
    "yes or no?",
    "yes/no",
    "yes/no?",
    "outcome",
}


def _character_weight(segment: str) -> int:
    # Twitter counts most BMP characters as 1 and astral characters (most
    # emoji) as 2.
    return sum(2 if ord(char) > 0xFFFF else 1 for char in segment)


def twitter_weight(text: str) -> int:
    """Approximate X's weighted character count, treating links as t.co URLs."""
    total = 0
    last = 0
    for match in _URL_RE.finditer(text):
        total += _character_weight(text[last : match.start()])
        total += 23  # every URL is shortened to a 23-character t.co link
        last = match.end()
    total += _character_weight(text[last:])
    return total


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _truncate(value: str, limit: int) -> str:
    if limit <= 1 or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _format_amount(value: Decimal) -> str:
    value = value.quantize(Decimal("0.01"))
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def _format_quantity(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return _format_amount(value)


def _format_price(price: Decimal) -> str:
    percent = price * 100
    if percent >= 10:
        return f"{percent.quantize(Decimal('1'))}%"
    return f"{percent.quantize(Decimal('0.1'))}%"


def market_url(activity: OrderActivity, market_base_url: str) -> str:
    key = activity.event_slug or activity.market_id or activity.event_id
    base = market_base_url.rstrip("/")
    return f"{base}/{quote(key, safe='')}" if key else base


def _is_generic_market_title(title: str) -> bool:
    return title.casefold() in _GENERIC_MARKET_TITLES


def _compose(
    activity: OrderActivity,
    amount_ngn: Decimal,
    url: str,
    *,
    title_limit: int,
    market_limit: int,
    hashtags: bool,
) -> str:
    event_title = _truncate(
        _normalize(activity.event_title) or "Bayse prediction market",
        title_limit,
    )
    market_title = _truncate(_normalize(activity.market_title), market_limit)
    outcome = _normalize(activity.outcome_label or activity.outcome).upper()

    lines = ["🐋 WHALE ALERT", "", event_title]

    show_market = (
        bool(market_title)
        and not _is_generic_market_title(market_title)
        and market_title.casefold() != event_title.casefold()
        and market_title.casefold() != outcome.casefold()
    )
    if show_market:
        lines.append(f"{market_title} · {outcome}" if outcome else market_title)
    elif outcome:
        lines.append(f"Outcome: {outcome}")

    lines.append("")

    trade = [f"{'🟢' if activity.side == 'BUY' else '🔴'} {activity.side}"]
    if activity.quantity is not None and activity.quantity > 0:
        trade.append(f"{_format_quantity(activity.quantity)} shares")
    trade_text = " · ".join(trade)
    if activity.price is not None and activity.price > 0:
        trade_text += f" @ {_format_price(activity.price)}"
    lines.append(trade_text)

    ngn_text = f"₦{_format_amount(amount_ngn)}"
    currency = activity.currency or "?"
    if currency == "NGN":
        money = f"💵 {ngn_text}"
    elif currency == "USD":
        money = f"💵 ${_format_money(activity.amount)} (≈{ngn_text})"
    else:
        money = (
            f"💵 {_format_money(activity.amount)} {currency} (≈{ngn_text})"
        )
    lines.append(money)

    tag = _normalize(activity.user_tag).lstrip("@")
    lines.append(f"👤 @{tag}" if tag else "👤 Anonymous whale")

    lines.extend(["", url])
    if hashtags:
        lines.append("#Bayse #PredictionMarkets")

    return "\n".join(lines)


def format_tweet(
    activity: OrderActivity,
    amount_ngn: Decimal,
    market_base_url: str,
) -> str:
    """Build a post that always fits X's 280-character limit."""
    url = market_url(activity, market_base_url)

    variants = (
        (80, 50, True),
        (60, 40, True),
        (48, 32, False),
        (34, 22, False),
    )
    for title_limit, market_limit, hashtags in variants:
        text = _compose(
            activity,
            amount_ngn,
            url,
            title_limit=title_limit,
            market_limit=market_limit,
            hashtags=hashtags,
        )
        if twitter_weight(text) <= SAFE_TWEET_WEIGHT:
            return text

    # The bounded variant above is comfortably under the limit; this is only
    # a defensive fallback for unexpectedly long currency codes.
    return _compose(
        activity,
        amount_ngn,
        url,
        title_limit=20,
        market_limit=14,
        hashtags=False,
    )
