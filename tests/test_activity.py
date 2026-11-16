from decimal import Decimal

from bot.models import ngn_value
from bot.whale_detector.activity import parse_activity


def buy_message(**overrides):
    message = {
        "type": "buy_order",
        "data": {
            "user": {
                "id": "usr_8b5c3c3a",
                "tag": "prediction_pro",
                "imageUrl": "https://cdn.bayse.markets/users/8b5c3c3a.png",
            },
            "order": {
                "id": "ord_7f5e2a1c",
                "amount": 100.00,
                "quantity": 150.0,
                "price": 0.65,
                "status": "FILLED",
                "type": "BUY",
                "outcome": "YES",
                "outcomeLabel": "Yes",
                "currency": "USD",
                "createdAt": "2026-02-17T10:30:00Z",
                "updatedAt": "2026-02-17T10:30:01Z",
            },
            "event": {
                "id": "evt_123",
                "slug": "us-fed-rate-cut-2026",
                "title": "Will the Fed cut rates in 2026?",
                "type": "SINGLE_MARKET",
            },
            "market": {"id": "mkt_456", "title": "Yes or No"},
        },
        "timestamp": 1700000000000,
    }
    data = overrides.pop("data", None)
    if data:
        message["data"].update(data)
    message.update(overrides)
    return message


def test_parses_buy_order():
    activity = parse_activity(buy_message())

    assert activity is not None
    assert activity.side == "BUY"
    assert activity.status == "FILLED"
    assert activity.order_id == "ord_7f5e2a1c"
    assert activity.amount == Decimal("100.0")
    assert activity.currency == "USD"
    assert activity.quantity == Decimal("150.0")
    assert activity.price == Decimal("0.65")
    assert activity.outcome == "YES"
    assert activity.outcome_label == "Yes"
    assert activity.event_id == "evt_123"
    assert activity.event_slug == "us-fed-rate-cut-2026"
    assert activity.event_title == "Will the Fed cut rates in 2026?"
    assert activity.market_id == "mkt_456"
    assert activity.market_title == "Yes or No"
    assert activity.user_tag == "prediction_pro"


def test_parses_nested_user_trades_payload():
    message = buy_message()
    message["type"] = "sell_order"
    message["data"] = {"payload": message["data"]}

    activity = parse_activity(message)

    assert activity is not None
    assert activity.side == "SELL"
    assert activity.order_id == "ord_7f5e2a1c"
    assert activity.event_title == "Will the Fed cut rates in 2026?"


def test_derives_amount_from_quantity_and_price():
    message = buy_message()
    del message["data"]["order"]["amount"]
    message["data"]["order"]["quantity"] = 200
    message["data"]["order"]["price"] = 0.5

    activity = parse_activity(message)

    assert activity is not None
    assert activity.amount == Decimal("100.00")


# Captured verbatim from the live global activity feed on 2026-11-16.
REAL_GLOBAL_FRAME = {
    "type": "buy_order",
    "data": {
        "event": {
            "category": "CRYPTO",
            "createdAt": "2026-11-16T11:45:10.721779Z",
            "hashtags": ["15 Min"],
            "id": "a9ff96de-5b89-4b84-8f82-13f9cfc81ee3",
            "imageUrl": "https://assets.bayse.markets/event-images/automated/BTC.png",
            "slug": "btc-up-or-down-15m-1794829500000",
            "title": "Bitcoin Up or Down - 15 minutes?",
            "type": "SINGLE_MARKET",
        },
        "market": {
            "id": "af28e361-afdd-48d7-bf30-a52eb8b3e228",
            "imageUrl": "https://assets.bayse.markets/event-images/automated/BTC.png",
            "title": "Bitcoin Up or Down - 15 minutes?",
        },
        "order": {
            "amount": 499.9999,
            "costOfSharesInActualCurrency": 499.9999,
            "createdAt": "2026-11-16T11:55:12.976825854Z",
            "currency": "NGN",
            "engine": "CLOB",
            "id": "ae76c83e-0fb4-4c24-a07f-f9f6791d1365",
            "orderType": "MARKET",
            "outcome": "NO",
            "outcomeLabel": "Down",
            "price": 0.68,
            "quantity": 7,
            "status": "filled",
            "type": "BUY",
            "updatedAt": "2026-11-16T11:55:12.976825914Z",
        },
        "user": {
            "id": "5f09622d-a711-4808-9af4-bfa1f3a6e633",
            "imageUrl": None,
            "tag": "user0j2UVV",
        },
    },
    "timestamp": 1794830112999,
}


def test_parses_real_global_activity_frame():
    activity = parse_activity(REAL_GLOBAL_FRAME)

    assert activity is not None
    assert activity.side == "BUY"
    assert activity.status == "FILLED"
    assert activity.order_id == "ae76c83e-0fb4-4c24-a07f-f9f6791d1365"
    assert activity.amount == Decimal("499.9999")
    assert activity.currency == "NGN"
    assert activity.quantity == Decimal("7")
    assert activity.price == Decimal("0.68")
    assert activity.outcome_label == "Down"
    assert activity.event_id == "a9ff96de-5b89-4b84-8f82-13f9cfc81ee3"
    assert activity.event_slug == "btc-up-or-down-15m-1794829500000"
    assert activity.market_title == "Bitcoin Up or Down - 15 minutes?"
    assert activity.user_tag == "user0j2UVV"
    assert ngn_value(activity, Decimal("1500")) == Decimal("499.9999")


def test_ignores_non_order_messages():
    assert parse_activity({"type": "connected", "data": {}}) is None
    assert parse_activity({"type": "price_update", "data": {}}) is None
    assert parse_activity("not a dict") is None


def test_ngn_value_conversion():
    activity = parse_activity(buy_message())
    assert activity is not None
    assert ngn_value(activity, Decimal("1352.36")) == Decimal("135236.00")

    ngn_activity = parse_activity(
        buy_message(
            data={
                **buy_message()["data"],
                "order": {
                    **buy_message()["data"]["order"],
                    "currency": "NGN",
                    "amount": 150000,
                },
            }
        )
    )
    assert ngn_activity is not None
    assert ngn_value(ngn_activity, Decimal("1500")) == Decimal("150000")

    eur_activity = parse_activity(
        buy_message(
            data={
                **buy_message()["data"],
                "order": {
                    **buy_message()["data"]["order"],
                    "currency": "EUR",
                },
            }
        )
    )
    assert eur_activity is not None
    assert ngn_value(eur_activity, Decimal("1500")) is None
