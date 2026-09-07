"""The WhatsApp integration, exercised with no account, no network and no cost.

Every test here runs the real :class:`WhatsAppCloudAdapter` against the local
:class:`FakeGraphApi` through httpx's in-process ASGI transport, so the HTTP layer, the
auth header, the request body and the error handling are all genuinely executed and no
socket is ever opened.

The one thing these tests cannot prove is that the fake matches Meta. That is why the
fake's contract is taken from the published spec and the divergences are asserted
explicitly - ``recipient_type`` being required is the clearest example, since almost every
published example omits it and a fake written from those examples would accept a request
Meta rejects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from pitchbot.adapters.errors import ExternalNetworkDisabledError, PermanentAdapterError
from pitchbot.adapters.network import NetworkPolicy
from pitchbot.adapters.whatsapp_cloud import WhatsAppCloudAdapter
from pitchbot.whatsapp import (
    FREE_ENTRY_POINT_WINDOW,
    RATES_CURRENCY,
    RATES_EFFECTIVE,
    RATES_READ_ON,
    SERVICE_WINDOW,
    CostPosture,
    FakeGraphApi,
    MessageCategory,
    WebhookReceiver,
    decide,
    india_rate,
    parse_inbound,
    valid_signature,
    window_closes_at,
)

NUMBER = "919876543210"


def _adapter(
    fake: FakeGraphApi,
    *,
    posture: CostPosture = CostPosture.FREE_ONLY,
    network: bool = True,
) -> WhatsAppCloudAdapter:
    return WhatsAppCloudAdapter(
        phone_number_id=fake.phone_number_id,
        access_token=fake.expected_token,
        network_policy=NetworkPolicy(external_network_enabled=network),
        posture=posture,
        base_url="http://fake.invalid",
        api_version=fake.api_version,
        transport=httpx.ASGITransport(app=fake.app()),
    )


@pytest.mark.asyncio
async def test_nothing_is_sent_before_the_customer_has_written() -> None:
    """The default posture cannot start a conversation, because starting one costs money.

    This is the whole cost model in one assertion: a business may reply for free and may
    not speak first for free, and the difference is invisible at the call site. The adapter
    refuses rather than sending, and the API is never called at all.
    """

    fake = FakeGraphApi()
    result = await _adapter(fake).send_message(NUMBER, "hello", "k1", now=datetime.now(UTC))

    assert result.status == "refused-would-be-charged"
    assert "never messaged or called us" in result.detail
    assert fake.sent == []


@pytest.mark.asyncio
async def test_a_reply_inside_the_window_is_sent_for_free() -> None:
    fake = FakeGraphApi()
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    result = await adapter.send_message(NUMBER, "here is the summary", "k1", now=now)

    assert result.status == "sent"
    assert result.provider_reference == "wamid.FAKE000000"
    assert len(fake.sent) == 1
    assert fake.sent[0].body == "here is the summary"


@pytest.mark.asyncio
async def test_the_window_closes_after_twenty_four_hours() -> None:
    fake = FakeGraphApi()
    adapter = _adapter(fake)
    opened = datetime.now(UTC)
    adapter.record_inbound(NUMBER, opened)

    inside = await adapter.send_message(
        NUMBER, "still free", "k1", now=opened + timedelta(hours=23, minutes=59)
    )
    outside = await adapter.send_message(
        NUMBER, "no longer free", "k2", now=opened + timedelta(hours=24, minutes=1)
    )

    assert inside.status == "sent"
    assert outside.status == "refused-would-be-charged"
    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_a_marketing_template_is_chargeable_even_inside_an_open_window() -> None:
    """Opening a window does not make a *marketing* template free.

    Easy to get backwards, and expensive at INR 0.8631 a message: the window makes
    free-form messages free, and makes *utility* templates free. Marketing and
    authentication are charged whenever they are sent, window or no window - the sole
    exception being an open Free Entry Point window.
    """

    fake = FakeGraphApi()
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    for category in (MessageCategory.MARKETING, MessageCategory.AUTHENTICATION):
        result = await adapter.send_message(
            NUMBER, "offer", f"k-{category.value}", now=now, template_category=category
        )
        assert result.status == "refused-would-be-charged", category
    assert fake.sent == []


@pytest.mark.asyncio
async def test_paying_is_possible_but_has_to_be_asked_for() -> None:
    """The gate is a posture, not a prohibition - it just cannot be crossed by accident."""

    fake = FakeGraphApi()
    adapter = _adapter(fake, posture=CostPosture.ALLOW_PAID)

    result = await adapter.send_message(
        NUMBER, "offer", "k1", now=datetime.now(UTC), template_category=MessageCategory.MARKETING
    )

    assert result.status == "sent"
    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_the_network_policy_is_checked_before_anything_else() -> None:
    """Deny-by-default outranks every other consideration, including a free message."""

    fake = FakeGraphApi()
    adapter = _adapter(fake, network=False)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    with pytest.raises(ExternalNetworkDisabledError):
        await adapter.send_message(NUMBER, "free reply", "k1", now=now)
    assert fake.sent == []


@pytest.mark.asyncio
async def test_a_retried_send_does_not_deliver_twice() -> None:
    """Graph has no idempotency key, so the adapter has to hold one.

    ADR-0003 requires provider retries to be idempotent. Without this the retry that
    follows a timeout sends the buyer the same message again.
    """

    fake = FakeGraphApi()
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    first = await adapter.send_message(NUMBER, "summary", "same-key", now=now)
    second = await adapter.send_message(NUMBER, "summary", "same-key", now=now)

    assert first.provider_reference == second.provider_reference
    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_the_request_carries_what_the_published_spec_requires() -> None:
    """`recipient_type` is required by the spec and omitted by most published examples.

    Asserted here rather than left to the fake alone, because a fake and a client written
    from the same recollection agree with each other and prove nothing.
    """

    fake = FakeGraphApi()
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    await adapter.send_message(NUMBER, "body text", "k1", now=now)

    payload = fake.sent[0].payload
    assert payload["messaging_product"] == "whatsapp"
    assert payload["recipient_type"] == "individual"
    assert payload["to"] == NUMBER
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "body text"
    assert fake.sent[0].token == fake.expected_token


@pytest.mark.asyncio
async def test_a_bad_token_is_a_permanent_failure_not_a_retry() -> None:
    """Retrying a rejected request against a provider with no idempotency key is harmful.

    A wrong token will be wrong on the retry too, and repeated rejected sends damage a
    number's quality rating.
    """

    fake = FakeGraphApi()
    adapter = WhatsAppCloudAdapter(
        phone_number_id=fake.phone_number_id,
        access_token="wrong-token",
        network_policy=NetworkPolicy(external_network_enabled=True),
        base_url="http://fake.invalid",
        api_version=fake.api_version,
        transport=httpx.ASGITransport(app=fake.app()),
    )
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    with pytest.raises(PermanentAdapterError, match="190"):
        await adapter.send_message(NUMBER, "hello", "k1", now=now)


@pytest.mark.asyncio
async def test_an_unverified_recipient_is_refused_the_way_a_test_number_refuses_it() -> None:
    """The first wall anyone hits: a free test number only reaches verified recipients."""

    fake = FakeGraphApi(allowed_recipients=frozenset({"911111111111"}))
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    with pytest.raises(PermanentAdapterError, match="131030"):
        await adapter.send_message(NUMBER, "hello", "k1", now=now)


def test_an_inbound_message_is_parsed_from_the_documented_payload_shape() -> None:
    """The shape is Meta's published example, including `timestamp` as a *string*."""

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": "106540352242922",
                            },
                            "contacts": [{"profile": {"name": "Sheena"}, "wa_id": NUMBER}],
                            "messages": [
                                {
                                    "from": NUMBER,
                                    "id": "wamid.ONE",
                                    "timestamp": "1749416383",
                                    "type": "text",
                                    "text": {"body": "Does it come in another color?"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    (message,) = parse_inbound(payload)

    assert message.contact_ref == NUMBER
    assert message.profile_name == "Sheena"
    assert message.text == "Does it come in another color?"
    assert message.sent_at == datetime.fromtimestamp(1_749_416_383, tz=UTC)
    assert window_closes_at(message.sent_at) == message.sent_at + timedelta(hours=24)


def test_a_payload_that_is_not_a_message_is_ignored_rather_than_failing() -> None:
    """Status updates and quality alerts arrive on the same webhook as messages.

    Treating them as errors would make the endpoint return non-200 and buy seven days of
    retries - for the delivery receipts *and* for the real messages behind them.
    """

    assert parse_inbound({"object": "whatsapp_business_account", "entry": []}) == ()
    assert parse_inbound({"object": "page", "entry": []}) == ()
    assert parse_inbound("not a payload") == ()
    assert (
        parse_inbound(
            {
                "object": "whatsapp_business_account",
                "entry": [{"changes": [{"field": "message_template_status_update", "value": {}}]}],
            }
        )
        == ()
    )


def test_a_redelivered_message_is_not_processed_twice() -> None:
    """Meta retries a failed delivery for 7 days and says to deduplicate.

    Without this, one webhook outage replays every message in the backlog as though the
    customer had sent them again - which would also reopen free-send windows that had
    closed.
    """

    receiver = WebhookReceiver(verify_token="t", app_secret="s", require_signature=False)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {"from": NUMBER, "id": "wamid.DUP", "timestamp": "1749416383"}
                            ]
                        },
                    }
                ]
            }
        ],
    }
    messages = parse_inbound(payload)

    assert receiver.deliver(messages) == 1
    assert receiver.deliver(messages) == 0
    assert len(receiver.received) == 1


def test_a_signature_is_checked_over_the_raw_bytes() -> None:
    """Re-serialising the parsed JSON would fail on any whitespace difference."""

    secret = "app-secret"
    raw = b'{"object": "whatsapp_business_account",  "entry": []}'
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    assert valid_signature(raw_body=raw, header=f"sha256={digest}", app_secret=secret)
    assert not valid_signature(raw_body=raw, header=f"sha256={digest}", app_secret="other")
    assert not valid_signature(raw_body=raw + b" ", header=f"sha256={digest}", app_secret=secret)
    assert not valid_signature(raw_body=raw, header=None, app_secret=secret)
    assert not valid_signature(raw_body=raw, header=digest, app_secret=secret)


def test_an_unsigned_payload_is_refused_by_default() -> None:
    """Anyone who learns the URL could otherwise open a free-send window they do not own."""

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    receiver = WebhookReceiver(verify_token="verify-me", app_secret="app-secret")
    app = FastAPI()
    app.include_router(receiver.router())
    client = TestClient(app)

    body = json.dumps({"object": "whatsapp_business_account", "entry": []})
    unsigned = client.post("/webhook", content=body)
    signed = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256="
            + hmac.new(b"app-secret", body.encode(), hashlib.sha256).hexdigest()
        },
    )

    assert unsigned.status_code == 403
    assert signed.status_code == 200


def test_the_verification_handshake_echoes_the_challenge_verbatim() -> None:
    """Meta compares the body byte for byte, so a JSON-quoted challenge fails."""

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    receiver = WebhookReceiver(verify_token="verify-me", app_secret="s")
    app = FastAPI()
    app.include_router(receiver.router())
    client = TestClient(app)

    good = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "1158201444",
        },
    )
    bad = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
    )

    assert good.status_code == 200
    assert good.text == "1158201444"
    assert bad.status_code == 403


@pytest.mark.parametrize(
    ("hours_since_inbound", "template", "expected_free"),
    [
        (0, None, True),
        (23, None, True),
        (25, None, False),
        # A utility template inside the window is free; outside it, it is not.
        (0, MessageCategory.UTILITY, True),
        (25, MessageCategory.UTILITY, False),
        (0, MessageCategory.MARKETING, False),
        (0, MessageCategory.AUTHENTICATION, False),
    ],
)
def test_what_is_free_is_decided_by_the_window_and_the_category_together(
    hours_since_inbound: int, template: MessageCategory | None, expected_free: bool
) -> None:
    now = datetime.now(UTC)
    verdict = decide(
        posture=CostPosture.FREE_ONLY,
        last_inbound_at=now - timedelta(hours=hours_since_inbound),
        now=now,
        template_category=template,
    )

    assert verdict.allowed is expected_free
    assert verdict.would_be_charged is not expected_free


def test_a_number_that_never_wrote_has_no_free_path_at_all() -> None:
    verdict = decide(posture=CostPosture.FREE_ONLY, last_inbound_at=None, now=datetime.now(UTC))

    assert not verdict.allowed
    assert verdict.would_be_charged


# --- The two rules that were wrong in the first draft of the pricing model ------------
#
# Both are quoted from Meta's pricing page. Both are the opposite of the obvious guess,
# which is exactly why they are pinned here: a model built on "templates cost money,
# non-templates do not" passes every other test in this file.


@pytest.mark.asyncio
async def test_a_utility_template_inside_the_window_is_free() -> None:
    """ "Utility templates delivered within an open customer service window are free."

    The first draft of this model charged for every template, which would have refused a
    send that costs nothing - the cheap direction to be wrong in, but still wrong, and it
    would have pushed the product towards paid marketing templates instead.
    """

    fake = FakeGraphApi()
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)

    utility = await adapter.send_message(
        NUMBER, "your order shipped", "k1", now=now, template_category=MessageCategory.UTILITY
    )
    marketing = await adapter.send_message(
        NUMBER, "20% off", "k2", now=now, template_category=MessageCategory.MARKETING
    )

    assert utility.status == "sent", "a utility template in an open window costs nothing"
    assert marketing.status == "refused-would-be-charged", "marketing is charged in the same window"
    assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_a_free_entry_point_window_makes_even_marketing_free() -> None:
    """ "FEP windows remain open for 72 hours. While open, you can send any type of message
    to the user at no charge."

    The only circumstance in which a marketing template is free. It is reachable: the
    customer arrived from a click-to-WhatsApp ad or a Page button and we answered within 24
    hours. Missing it means paying INR 0.8631 for something that is free.
    """

    fake = FakeGraphApi()
    adapter = _adapter(fake)
    now = datetime.now(UTC)
    adapter.record_inbound(NUMBER, now)
    adapter.record_free_entry_point(NUMBER, now)

    inside = await adapter.send_message(
        NUMBER, "20% off", "k1", now=now, template_category=MessageCategory.MARKETING
    )
    after = await adapter.send_message(
        NUMBER,
        "20% off",
        "k2",
        now=now + timedelta(hours=73),
        template_category=MessageCategory.MARKETING,
    )

    assert inside.status == "sent"
    assert after.status == "refused-would-be-charged"
    assert len(fake.sent) == 1


def test_the_free_entry_point_window_is_longer_than_the_service_window() -> None:
    """72 hours against 24. They are independent, and confusing them is a real cost."""

    assert FREE_ENTRY_POINT_WINDOW == timedelta(hours=72)
    assert SERVICE_WINDOW == timedelta(hours=24)
    assert FREE_ENTRY_POINT_WINDOW > SERVICE_WINDOW


def test_every_decision_that_is_free_says_which_rule_made_it_free() -> None:
    """Four different rules can make a message free, and they are not interchangeable.

    A log saying only "free" cannot be audited later, and this project's cost claim rests
    on being able to audit it.
    """

    now = datetime.now(UTC)
    cases = (
        {"last_inbound_at": now, "template_category": None},
        {"last_inbound_at": now, "template_category": MessageCategory.UTILITY},
        {
            "last_inbound_at": now,
            "template_category": MessageCategory.MARKETING,
            "free_entry_point_at": now,
        },
    )
    for case in cases:
        verdict = decide(posture=CostPosture.FREE_ONLY, now=now, **case)
        assert verdict.allowed, case
        assert not verdict.would_be_charged, case
        assert verdict.free_because, case


def test_the_india_rate_card_is_dated_because_meta_changes_it_quarterly() -> None:
    """Meta may change pricing "only on the 1st day of each quarter".

    An undated rate is one nobody can check, so the read date and the effective date ship
    with the numbers. Service carries no rate at all, which is the whole zero-cost story.
    """

    assert india_rate(MessageCategory.SERVICE) is None
    assert india_rate(MessageCategory.MARKETING) == 0.8631
    assert india_rate(MessageCategory.UTILITY) == 0.1150
    assert RATES_CURRENCY == "INR"
    assert RATES_READ_ON and RATES_EFFECTIVE
