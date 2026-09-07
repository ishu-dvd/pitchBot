"""Talking to WhatsApp, and finding out what that would cost.

Three pieces, deliberately separable:

* :mod:`pitchbot.whatsapp.pricing` - which messages are free and which are not. Free
  replying is possible for 24 hours after a customer writes; starting a conversation is
  never free. That distinction is invisible at the call site, so it is enforced here.
* :mod:`pitchbot.whatsapp.fake_graph` - a local stand-in for Meta's Graph API, so the whole
  path can be exercised with no Meta app, no business account, no phone number and no
  network.
* :mod:`pitchbot.whatsapp.webhook` - the inbound endpoint. Not optional: it is what opens
  the free-send window in the first place.

The outbound client lives with the other adapters, as
:class:`pitchbot.adapters.whatsapp_cloud.WhatsAppCloudAdapter`, because it implements the
existing ``WhatsAppAdapter`` protocol and everything already written against the mock works
against it unchanged.

See ``docs/WHATSAPP.md`` for how the platform works and what a zero-cost path looks like.
"""

from __future__ import annotations

from pitchbot.whatsapp.fake_graph import FakeGraphApi, SentMessage
from pitchbot.whatsapp.pricing import (
    FREE_ENTRY_POINT_WINDOW,
    INDIA_CALL_RATE_PER_MINUTE,
    INDIA_RATES,
    RATES_CURRENCY,
    RATES_EFFECTIVE,
    RATES_READ_ON,
    SERVICE_WINDOW,
    CostPosture,
    MessageCategory,
    SendDecision,
    classify,
    decide,
    free_entry_point_closes_at,
    india_rate,
    window_closes_at,
)
from pitchbot.whatsapp.webhook import (
    InboundMessage,
    WebhookReceiver,
    parse_inbound,
    valid_signature,
)

__all__ = [
    "FREE_ENTRY_POINT_WINDOW",
    "INDIA_CALL_RATE_PER_MINUTE",
    "INDIA_RATES",
    "RATES_CURRENCY",
    "RATES_EFFECTIVE",
    "RATES_READ_ON",
    "SERVICE_WINDOW",
    "CostPosture",
    "FakeGraphApi",
    "InboundMessage",
    "MessageCategory",
    "SendDecision",
    "SentMessage",
    "WebhookReceiver",
    "classify",
    "decide",
    "free_entry_point_closes_at",
    "india_rate",
    "parse_inbound",
    "valid_signature",
    "window_closes_at",
]
