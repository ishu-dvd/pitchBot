"""`pitchbot-whatsapp` - see what WhatsApp would do, and what it would cost, without paying.

Three subcommands, each answering a question that otherwise takes an afternoon and a Meta
Business account to answer:

``status``   what is configured, which mode a send would take, and whether it would be free
``demo``     drive a full send through the local fake - no account, no network, no cost
``inbound``  feed a webhook payload in and watch the free-reply window open

Everything defaults to the local fake. Reaching the real Graph API needs
``PITCHBOT_ENABLE_EXTERNAL_NETWORK=true`` *and* ``PITCHBOT_ENABLE_WHATSAPP=true`` *and*
credentials, which is three deliberate acts rather than one forgotten flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

from pitchbot.adapters.network import NetworkPolicy
from pitchbot.adapters.whatsapp_cloud import (
    DEFAULT_API_VERSION,
    HTTPX_AVAILABLE,
    WhatsAppCloudAdapter,
)
from pitchbot.config import Settings
from pitchbot.whatsapp.fake_graph import FakeGraphApi
from pitchbot.whatsapp.pricing import (
    INDIA_CALL_RATE_PER_MINUTE,
    RATES_CURRENCY,
    RATES_EFFECTIVE,
    RATES_READ_ON,
    CostPosture,
    MessageCategory,
    decide,
    india_rate,
    window_closes_at,
)
from pitchbot.whatsapp.webhook import WebhookReceiver, parse_inbound

_FAKE_NUMBER = "919876543210"


def _status(settings: Settings) -> int:
    network = settings.enable_external_network
    whatsapp = settings.enable_whatsapp
    print("PitchBot WhatsApp status")
    print("=" * 60)
    print(f"  enable_whatsapp .......... {whatsapp}")
    print(f"  enable_external_network .. {network}")
    print(f"  httpx installed .......... {HTTPX_AVAILABLE}")
    print(f"  graph api version ........ {DEFAULT_API_VERSION}")
    print()
    if not (network and whatsapp):
        print("  MODE: local fake only. Nothing can reach Meta, and nothing can be billed.")
        print("        Both flags must be true, and credentials supplied, to change that.")
    elif not HTTPX_AVAILABLE:
        print("  MODE: blocked. Flags are on but httpx is absent.")
        print('        Install it with: pip install "pitchbot[whatsapp]"')
    else:
        print("  MODE: live. Sends would reach Meta and a chargeable message would be billed.")
    print()
    print("What costs money")
    print("-" * 60)
    print("  Free, always:")
    print("    - any non-template message inside an open 24h customer service window")
    print("    - a UTILITY template inside that same window")
    print("    - ANY message inside a 72h Free Entry Point window (ad / Page button)")
    print("    - every user-initiated call, with no payment method at all")
    print()
    print(f"  Charged in India ({RATES_CURRENCY}, effective {RATES_EFFECTIVE},")
    print(f"  read {RATES_READ_ON} - Meta may change these each quarter):")
    for category in (
        MessageCategory.MARKETING,
        MessageCategory.UTILITY,
        MessageCategory.AUTHENTICATION,
    ):
        rate = india_rate(category)
        print(f"    - {category.value:<16} {rate:.4f} per message")
    print(f"    - business-initiated call  {INDIA_CALL_RATE_PER_MINUTE:.4f} per minute")
    print("      (needs a payment method, so it can never be part of a free path)")
    print()
    print("  The default posture is FREE_ONLY: a chargeable send is refused with the")
    print("  reason rather than sent. See docs/WHATSAPP.md for the zero-cost path.")
    return 0


async def _demo(text: str) -> int:
    """A full outbound send against the local fake, including the free-window gate."""

    fake = FakeGraphApi(allowed_recipients=frozenset({_FAKE_NUMBER}))
    import httpx

    adapter = WhatsAppCloudAdapter(
        phone_number_id=fake.phone_number_id,
        access_token=fake.expected_token,
        # The fake is not the external network; nothing leaves the process.
        network_policy=NetworkPolicy(external_network_enabled=True),
        base_url="http://fake.invalid",
        api_version=fake.api_version,
        transport=httpx.ASGITransport(app=fake.app()),
    )
    now = datetime.now(UTC)

    print("1. Sending before the customer has ever written to us")
    print("-" * 60)
    first = await adapter.send_message(_FAKE_NUMBER, text, "demo-1", now=now)
    print(f"   status: {first.status}")
    print(f"   reason: {first.detail}")
    print(f"   messages actually sent to the API: {len(fake.sent)}")
    print()

    print("2. The customer writes, which opens a 24-hour free-reply window")
    print("-" * 60)
    adapter.record_inbound(_FAKE_NUMBER, now)
    closes = window_closes_at(now)
    print(f"   window open until: {closes.isoformat()}")
    second = await adapter.send_message(_FAKE_NUMBER, text, "demo-2", now=now)
    print(f"   status: {second.status}")
    print(f"   provider reference: {second.provider_reference}")
    print(f"   messages actually sent to the API: {len(fake.sent)}")
    print()

    print("3. A marketing template, inside the same open window")
    print("-" * 60)
    third = await adapter.send_message(
        _FAKE_NUMBER,
        text,
        "demo-3",
        now=now,
        template_category=MessageCategory.MARKETING,
    )
    print(f"   status: {third.status}")
    print(f"   reason: {third.detail}")
    print()

    print("4. The same send retried with the key it already used")
    print("-" * 60)
    repeat = await adapter.send_message(_FAKE_NUMBER, text, "demo-2", now=now)
    print(f"   status: {repeat.status}  reference: {repeat.provider_reference}")
    print(f"   messages actually sent to the API: {len(fake.sent)} (unchanged)")
    print()

    print("5. Twenty-five hours later, the window has closed")
    print("-" * 60)
    later = await adapter.send_message(_FAKE_NUMBER, text, "demo-5", now=now + timedelta(hours=25))
    print(f"   status: {later.status}")
    print(f"   reason: {later.detail}")
    print()
    print(f"   Total delivered by the fake: {len(fake.sent)}")
    for sent in fake.sent:
        print(f"     {sent.message_id} -> {sent.to}: {sent.body!r}")
    return 0


def _inbound(path: str | None) -> int:
    """Feed a webhook payload in and show what it changes."""

    if path:
        payload = json.loads(open(path, encoding="utf-8").read())
    else:
        # The example from Meta's own webhook documentation.
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
                                "contacts": [
                                    {"profile": {"name": "Sheena Nelson"}, "wa_id": _FAKE_NUMBER}
                                ],
                                "messages": [
                                    {
                                        "from": _FAKE_NUMBER,
                                        "id": "wamid.EXAMPLE001",
                                        "timestamp": str(int(datetime.now(UTC).timestamp())),
                                        "type": "text",
                                        "text": {"body": "Does it come in another colour?"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

    receiver = WebhookReceiver(verify_token="demo", app_secret="demo", require_signature=False)
    messages = parse_inbound(payload)
    delivered = receiver.deliver(messages)
    replayed = receiver.deliver(messages)

    print("Inbound webhook")
    print("=" * 60)
    print(f"  parsed .... {len(messages)}")
    print(f"  delivered . {delivered}")
    print(f"  on replay . {replayed}  (Meta retries for 7 days, so repeats must be dropped)")
    print()
    for message in receiver.received:
        closes = window_closes_at(message.sent_at)
        print(f"  from {message.contact_ref} ({message.profile_name}): {message.text!r}")
        print(f"    sent at .............. {message.sent_at.isoformat()}")
        print(f"    free to reply until .. {closes.isoformat()}")
        verdict = decide(
            posture=CostPosture.FREE_ONLY,
            last_inbound_at=message.sent_at,
            now=datetime.now(UTC),
        )
        print(f"    replying now is ...... {'FREE' if verdict.allowed else 'CHARGEABLE'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pitchbot-whatsapp",
        description="Inspect and exercise the WhatsApp integration without spending money.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="what is configured and what it would cost")
    demo = sub.add_parser("demo", help="drive a send through the local fake")
    demo.add_argument("--text", default="Here is the summary from our call.")
    inbound = sub.add_parser("inbound", help="feed a webhook payload in")
    inbound.add_argument("--file", default=None, help="a saved webhook payload; omit for a sample")

    args = parser.parse_args(argv)
    if args.command == "demo":
        if not HTTPX_AVAILABLE:
            print('The demo needs httpx: pip install "pitchbot[whatsapp]"', file=sys.stderr)
            return 2
        return asyncio.run(_demo(args.text))
    if args.command == "inbound":
        return _inbound(args.file)
    return _status(Settings())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
