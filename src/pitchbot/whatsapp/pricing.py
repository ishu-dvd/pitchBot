"""What a WhatsApp message costs, and how to send only the ones that cost nothing.

PitchBot is a zero-cost project (ADR-0001), so "can this be done for free" is a
correctness question here, not a budgeting one.

Everything in this module is quoted from Meta's own pricing documentation rather than
recalled, because two of the rules are the opposite of the obvious guess and both were
wrong in the first draft of this file:

* **Not every template is chargeable.** *"Utility templates delivered within an open
  customer service window are free."* A utility template inside the window costs nothing;
  a marketing template inside the same window is charged.
* **There is a second, wider free window.** If the customer arrived through a
  click-to-WhatsApp ad or a Page call-to-action button and the business answers within 24
  hours, a **Free Entry Point** window opens: *"FEP windows remain open for 72 hours. While
  open, you can send any type of message to the user at no charge."*

The rest of the model:

* Per-message pricing replaced conversation pricing on 1 July 2025: *"You are only charged
  when a template message is delivered."*
* *"All non-template messages are free... Non-template messages can only be sent within an
  open customer service window."*
* Service is free with no cap - *"Effective November 1, 2024 - Service conversations are
  now free for all businesses"* - so there is no monthly free allowance to ration, only a
  free category.
* The window is opened or refreshed by a message **or a call**: *"the customer service
  window now also starts or refreshes for calls."*

Sources, read 2026-09-07:
https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing,
.../whatsapp/messages/send-messages, .../whatsapp/calling/pricing

Rates live in :data:`INDIA_RATES` carrying the date they were read, because Meta changes
them quarterly - *"only on the 1st day of each quarter"* - and an undated number is one
nobody can check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

#: How long a customer-service window stays open after the customer's last message or call.
SERVICE_WINDOW = timedelta(hours=24)

#: How long a Free Entry Point window stays open once the business has answered.
FREE_ENTRY_POINT_WINDOW = timedelta(hours=72)


class MessageCategory(StrEnum):
    """Meta's billing categories."""

    SERVICE = "service"
    UTILITY = "utility"
    MARKETING = "marketing"
    AUTHENTICATION = "authentication"


class CostPosture(StrEnum):
    """How much a caller is willing to spend.

    ``FREE_ONLY`` is the default everywhere in PitchBot. It is not advisory: a sender in
    this posture cannot emit a chargeable message even when asked to, which is the only
    arrangement in which "this project costs nothing to run" survives the next feature.
    """

    FREE_ONLY = "free-only"
    ALLOW_PAID = "allow-paid"


#: Cost per message in India, read from Meta's INR rate card on 2026-09-07, whose header
#: reads "effective July 1, 2026". Service is free and so has no rate.
#:
#: Recorded so a report can say what a refused send *would* have cost. Never used to decide
#: anything: the decision is structural, and these numbers move every quarter.
INDIA_RATES: Final[dict[MessageCategory, float | None]] = {
    MessageCategory.MARKETING: 0.8631,
    MessageCategory.UTILITY: 0.1150,
    MessageCategory.AUTHENTICATION: 0.1150,
    MessageCategory.SERVICE: None,
}
RATES_CURRENCY: Final = "INR"
RATES_READ_ON: Final = "2026-09-07"
RATES_EFFECTIVE: Final = "2026-07-01"

#: Business-initiated calling, from Meta's INR calling rate card, same reading date. Listed
#: for completeness and never used: outbound calling requires a payment method, so it can
#: never be part of a zero-cost path. *"All user-initiated calls are free."*
INDIA_CALL_RATE_PER_MINUTE: Final = 0.3885


@dataclass(frozen=True, slots=True)
class SendDecision:
    """Whether a message may be sent, why, and whether it would be charged."""

    allowed: bool
    category: MessageCategory
    would_be_charged: bool
    reason: str = ""
    #: Which rule made it free, so a log records "free because" rather than just "free".
    free_because: str = ""


def _fep_open(free_entry_point_at: datetime | None, now: datetime) -> bool:
    return free_entry_point_at is not None and now - free_entry_point_at < FREE_ENTRY_POINT_WINDOW


def _csw_open(last_inbound_at: datetime | None, now: datetime) -> bool:
    return last_inbound_at is not None and now - last_inbound_at < SERVICE_WINDOW


def classify(
    *,
    last_inbound_at: datetime | None,
    now: datetime,
    template_category: MessageCategory | None = None,
) -> MessageCategory:
    """Which category a message falls into, given the state of the conversation."""

    if template_category is not None:
        return template_category
    if _csw_open(last_inbound_at, now):
        return MessageCategory.SERVICE
    # No window, so a free-form message cannot be delivered at all. Reported as the
    # cheapest template that *could* carry it, so the gate refuses rather than pretending.
    return MessageCategory.UTILITY


def decide(
    *,
    posture: CostPosture,
    last_inbound_at: datetime | None,
    now: datetime,
    template_category: MessageCategory | None = None,
    free_entry_point_at: datetime | None = None,
) -> SendDecision:
    """Refuse anything chargeable unless the caller has explicitly opted into paying.

    ``free_entry_point_at`` is when the business answered a customer who arrived from a
    click-to-WhatsApp ad or a Page button. While that window is open every message is free,
    including marketing templates - the only circumstance in which that is true.
    """

    category = classify(
        last_inbound_at=last_inbound_at, now=now, template_category=template_category
    )
    csw = _csw_open(last_inbound_at, now)

    if _fep_open(free_entry_point_at, now):
        return SendDecision(
            allowed=True,
            category=category,
            would_be_charged=False,
            free_because=(
                "a Free Entry Point window is open, and every message type is free inside it"
            ),
        )
    if template_category is None and csw:
        return SendDecision(
            allowed=True,
            category=MessageCategory.SERVICE,
            would_be_charged=False,
            free_because="a non-template message inside an open customer service window",
        )
    if template_category is MessageCategory.UTILITY and csw:
        return SendDecision(
            allowed=True,
            category=category,
            would_be_charged=False,
            free_because="a utility template delivered inside an open customer service window",
        )

    if posture is CostPosture.ALLOW_PAID:
        return SendDecision(allowed=True, category=category, would_be_charged=True)

    if template_category is not None and not csw:
        reason = (
            f"A {category.value} template is chargeable: no customer service window is "
            "open, and only a Free Entry Point window would make it free."
        )
    elif template_category is not None:
        reason = (
            f"A {category.value} template is charged even inside an open window. Only "
            "utility templates are free there."
        )
    elif last_inbound_at is None:
        reason = (
            "No customer service window is open: this number has never messaged or called "
            "us, so only a paid template could reach them."
        )
    else:
        overdue = (now - last_inbound_at) - SERVICE_WINDOW
        reason = (
            f"The customer service window closed {overdue} ago; a free-form message is "
            "only free within 24 hours of the customer's last message or call."
        )
    return SendDecision(allowed=False, category=category, would_be_charged=True, reason=reason)


def window_closes_at(last_inbound_at: datetime) -> datetime:
    """When free replying stops being possible, so a caller can act before it does."""

    return last_inbound_at + SERVICE_WINDOW


def free_entry_point_closes_at(answered_at: datetime) -> datetime:
    return answered_at + FREE_ENTRY_POINT_WINDOW


def india_rate(category: MessageCategory) -> float | None:
    """What one message of this category would cost in India, or ``None`` if free."""

    return INDIA_RATES.get(category)


__all__ = [
    "FREE_ENTRY_POINT_WINDOW",
    "INDIA_CALL_RATE_PER_MINUTE",
    "INDIA_RATES",
    "RATES_CURRENCY",
    "RATES_EFFECTIVE",
    "RATES_READ_ON",
    "SERVICE_WINDOW",
    "CostPosture",
    "MessageCategory",
    "SendDecision",
    "classify",
    "decide",
    "free_entry_point_closes_at",
    "india_rate",
    "window_closes_at",
]
