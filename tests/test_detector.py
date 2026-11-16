import asyncio
from decimal import Decimal

from bot.detector import WhaleDetector
from bot.store import OrderStore
from bot.twitter import PublishResult


class FakeFx:
    rate = Decimal("1500")


class FakePublisher:
    def __init__(self):
        self.texts: list[str] = []

    async def publish(self, text: str) -> PublishResult:
        self.texts.append(text)
        return PublishResult(text=text, tweet_id="tweet_123", dry_run=False)


def message(
    *,
    amount=150000,
    currency="NGN",
    status="FILLED",
    order_id="ord_whale",
    event_id="evt_1",
):
    return {
        "type": "buy_order",
        "data": {
            "user": {"id": "usr_1", "tag": "whale"},
            "order": {
                "id": order_id,
                "amount": amount,
                "quantity": 100,
                "price": 0.65,
                "status": status,
                "type": "BUY",
                "outcome": "YES",
                "outcomeLabel": "Yes",
                "currency": currency,
            },
            "event": {
                "id": event_id,
                "slug": "who-will-win-the-2027-presidential-election",
                "title": "Who will win the 2027 Presidential Election?",
            },
            "market": {"id": "mkt_1", "title": "Bola Tinubu"},
        },
        "timestamp": 1700000000000,
    }


def make_detector(
    tmp_path,
    min_order=Decimal("100000"),
    *,
    event_ids=(),
    all_markets=True,
):
    store = OrderStore(tmp_path / "state.sqlite3")
    publisher = FakePublisher()
    detector = WhaleDetector(
        min_order_ngn=min_order,
        market_base_url="https://www.bayse.markets/market",
        fx=FakeFx(),
        store=store,
        publisher=publisher,
        event_ids=event_ids,
        all_markets=all_markets,
    )
    return detector, publisher, store


def test_posts_whale_once(tmp_path):
    detector, publisher, store = make_detector(tmp_path)

    async def scenario():
        await detector.handle_raw(message())
        await detector.handle_raw(message())

    asyncio.run(scenario())

    assert len(publisher.texts) == 1
    assert "₦150,000" in publisher.texts[0]
    assert store.status("ord_whale") == "posted"
    store.close()


def test_ignores_orders_at_or_below_threshold(tmp_path):
    detector, publisher, store = make_detector(tmp_path)

    async def scenario():
        await detector.handle_raw(message(amount=100000))
        await detector.handle_raw(message(amount=50000))

    asyncio.run(scenario())

    assert publisher.texts == []
    assert store.status("ord_whale") is None
    store.close()


def test_ignores_unfilled_orders(tmp_path):
    detector, publisher, store = make_detector(tmp_path)

    asyncio.run(detector.handle_raw(message(status="OPEN")))

    assert publisher.texts == []
    assert store.status("ord_whale") is None
    store.close()


def test_event_filter_skips_other_events(tmp_path):
    detector, publisher, store = make_detector(
        tmp_path, event_ids=("evt_1",), all_markets=False
    )

    async def scenario():
        await detector.handle_raw(
            message(order_id="ord_other", amount=500000, event_id="evt_2")
        )
        await detector.handle_raw(message(order_id="ord_allowed", amount=500000))

    asyncio.run(scenario())

    assert len(publisher.texts) == 1
    assert store.status("ord_other") is None
    assert store.status("ord_allowed") == "posted"
    store.close()


def test_all_markets_mode_posts_orders_from_any_event(tmp_path):
    detector, publisher, store = make_detector(
        tmp_path, event_ids=("evt_1",), all_markets=True
    )

    asyncio.run(
        detector.handle_raw(
            message(order_id="ord_other", amount=500000, event_id="evt_2")
        )
    )

    assert len(publisher.texts) == 1
    assert store.status("ord_other") == "posted"
    store.close()
