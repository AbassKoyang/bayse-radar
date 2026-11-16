from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from bot.formatting import format_tweet, twitter_weight
from bot.models import OrderActivity

BASE_URL = "https://www.bayse.markets/market"

ACTIVITY = OrderActivity(
    order_id="ord_123",
    side="BUY",
    status="FILLED",
    amount=Decimal("100.00"),
    currency="USD",
    quantity=Decimal("150"),
    price=Decimal("0.65"),
    outcome="YES",
    outcome_label="Yes",
    event_id="evt_123",
    event_slug="who-will-win-the-2027-presidential-election",
    event_title="Who will win the 2027 Presidential Election?",
    market_id="mkt_456",
    market_title="Bola Tinubu",
    user_id="usr_123",
    user_tag="prediction_pro",
    timestamp=datetime(2026, 11, 14, tzinfo=timezone.utc),
)


def test_formats_ngn_whale_alert():
    activity = replace(
        ACTIVITY,
        currency="NGN",
        amount=Decimal("150000"),
    )

    text = format_tweet(activity, Decimal("150000"), BASE_URL)

    assert "🐋 WHALE ALERT" in text
    assert "Who will win the 2027 Presidential Election?" in text
    assert "Bola Tinubu · YES" in text
    assert "🟢 BUY · 150 shares @ 65%" in text
    assert "💵 ₦150,000" in text
    assert "👤 @prediction_pro" in text
    assert (
        "https://www.bayse.markets/market/"
        "who-will-win-the-2027-presidential-election" in text
    )
    assert "#Bayse #PredictionMarkets" in text
    assert twitter_weight(text) <= 280


def test_formats_usd_order_with_ngn_equivalent():
    text = format_tweet(ACTIVITY, Decimal("135236.00"), BASE_URL)

    assert "💵 $100.00 (≈₦135,236)" in text
    assert twitter_weight(text) <= 280


def test_formats_sell_order():
    text = format_tweet(
        replace(ACTIVITY, side="SELL"), Decimal("150000"), BASE_URL
    )

    assert "🔴 SELL" in text


def test_hides_generic_market_title():
    activity = replace(ACTIVITY, market_title="Yes or No")
    text = format_tweet(activity, Decimal("150000"), BASE_URL)

    assert "Yes or No" not in text
    assert "Outcome: YES" in text


def test_long_titles_still_fit():
    activity = replace(
        ACTIVITY,
        event_title="A " + "very long event title " * 20,
        market_title="An extremely long market title " * 10,
        user_tag="a_very_long_twitter_handle_name",
    )
    text = format_tweet(activity, Decimal("150000"), BASE_URL)

    assert twitter_weight(text) <= 280
    assert "https://www.bayse.markets/market/" in text
