import asyncio
from decimal import Decimal

import pytest

from bot.store import ClaimOutcome, OrderStore
from bot.twitter import TwitterCredentials, TwitterError, TwitterPublisher


def claim(store: OrderStore, order_id: str = "ord_1") -> ClaimOutcome:
    return store.claim(
        order_id,
        event_id="evt_1",
        side="BUY",
        currency="NGN",
        amount=Decimal("150000"),
        amount_ngn=Decimal("150000"),
    )


def test_claim_is_idempotent_and_retries_failures(tmp_path):
    store = OrderStore(tmp_path / "state.sqlite3")

    assert claim(store) is ClaimOutcome.CLAIMED
    assert claim(store) is ClaimOutcome.IN_PROGRESS

    store.mark_failed("ord_1", "temporary failure")
    assert store.status("ord_1") == "failed"
    assert claim(store) is ClaimOutcome.CLAIMED

    store.mark_posted("ord_1", "tweet_1", "hello")
    assert store.status("ord_1") == "posted"
    assert claim(store) is ClaimOutcome.ALREADY_HANDLED

    store.close()


def test_dry_run_does_not_require_credentials():
    publisher = TwitterPublisher(
        TwitterCredentials("", "", "", ""), dry_run=True
    )

    result = asyncio.run(publisher.publish("Whale alert"))

    assert result.dry_run is True
    assert result.tweet_id is None


def test_rejects_overlong_tweets():
    publisher = TwitterPublisher(
        TwitterCredentials("", "", "", ""), dry_run=True
    )

    with pytest.raises(TwitterError):
        asyncio.run(publisher.publish("x" * 300))
