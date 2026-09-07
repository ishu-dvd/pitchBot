"""A real WhatsApp Cloud API client, which refuses to cost money unless told otherwise.

Implements :class:`~pitchbot.adapters.contracts.WhatsAppAdapter` against Meta's Graph API,
so the same `preview_whatsapp` path that has only ever driven a mock can drive the real
thing - or, far more usefully during development, the local
:class:`~pitchbot.whatsapp.fake_graph.FakeGraphApi` at a ``base_url`` of your choosing.

Three gates stand between this object and a charge on someone's account, and they are
independent on purpose:

1. **The network policy.** Unless ``enable_external_network`` is set, every call raises
   before a socket is opened. This is the repository-wide default and it is checked first.
2. **The cost posture.** :data:`~pitchbot.whatsapp.pricing.CostPosture.FREE_ONLY` is the
   default, and in that posture a message that would be billed is refused with the reason,
   not sent. Free replying is possible for 24 hours after a customer writes; starting a
   conversation is not free, and the difference is invisible in the code that calls this.
3. **Idempotency.** Graph has no idempotency key, so a retried send would deliver twice.
   ADR-0003 requires provider retries to be idempotent, so the key is held here and a
   repeat returns the first result rather than sending again.

``httpx`` is an optional extra. This module imports cleanly without it and callers can
probe :data:`HTTPX_AVAILABLE`; only actually sending needs the package, and that failure
names the extra to install.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from typing import Any, Final

from pitchbot.adapters.contracts import ActionResult, WhatsAppAdapter
from pitchbot.adapters.errors import PermanentAdapterError, TransientAdapterError
from pitchbot.adapters.network import NetworkPolicy
from pitchbot.whatsapp.pricing import CostPosture, MessageCategory, decide

HTTPX_AVAILABLE: Final[bool] = importlib.util.find_spec("httpx") is not None

DEFAULT_BASE_URL: Final = "https://graph.facebook.com"
DEFAULT_API_VERSION: Final = "v22.0"

DESTINATION_PATTERN: Final = re.compile(r"^\+[1-9]\d{6,14}$")
"""What this client insists a recipient looks like, because Meta insists on nothing.

There is **no** published regex, character class or length bound for ``to``: the field is a
bare ``string`` in the schema, and the term "E.164" does not appear in the Cloud API
documentation at all. What is documented is the failure mode, and it is not an error:

    "If the plus sign is omitted, your business phone number's country calling code is
    prepended to the customer's phone number. This can result in undelivered or
    misdelivered messages."

A malformed destination therefore returns **200** and is delivered to somebody else. That
is the one failure a caller cannot detect after the fact, so it is refused before the
request is built. Checked 2026-09-07 against
https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages
and the schema-backed message API reference.
"""

# Graph error codes worth distinguishing. Everything else is treated as permanent, because
# retrying a request the server has already rejected on content is how a number gets rate
# limited.
_RATE_LIMITED: Final = frozenset({4, 80_007, 130_429, 131_048})
_TRANSIENT: Final = frozenset({1, 2, 131_000, 131_026})


class WhatsAppCloudAdapter(WhatsAppAdapter):
    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        network_policy: NetworkPolicy | None = None,
        posture: CostPosture = CostPosture.FREE_ONLY,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout_seconds: float = 10.0,
        transport: Any | None = None,
    ) -> None:
        """``transport`` accepts an ``httpx`` transport, which is how the local fake is
        driven in-process: tests pass ``httpx.ASGITransport(app=fake.app())`` and no socket
        is ever opened. It is the seam that makes this class testable without an account.
        """

        if not phone_number_id.strip():
            raise ValueError("phone_number_id must not be empty")
        if not access_token.strip():
            raise ValueError("access_token must not be empty")
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._policy = network_policy or NetworkPolicy()
        self._posture = posture
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._timeout = timeout_seconds
        self._transport = transport
        self._results: dict[str, ActionResult] = {}
        # Last time each contact wrote to us, which is the only thing that decides whether
        # a reply is free. Fed by the webhook receiver; empty means no window is open for
        # anyone, so in the default posture nothing can be sent at all - which is correct.
        self._last_inbound: dict[str, datetime] = {}
        # When we answered a contact who arrived from a click-to-WhatsApp ad or a Page
        # button. That opens a 72-hour window in which *every* message type is free, which
        # is the only way a marketing template is ever free.
        self._free_entry_point: dict[str, datetime] = {}

    def record_inbound(self, contact_ref: str, at: datetime) -> None:
        """Note that a contact wrote to us or called, opening a 24-hour free-reply window.

        A call counts: *"the customer service window now also starts or refreshes for
        calls"*. That is worth knowing, because inbound calls are free and therefore a
        no-cost way to reopen the window.
        """

        previous = self._last_inbound.get(contact_ref)
        if previous is None or at > previous:
            self._last_inbound[contact_ref] = at

    def record_free_entry_point(self, contact_ref: str, answered_at: datetime) -> None:
        """Note that we answered a contact who arrived from an ad or a Page button."""

        self._free_entry_point[contact_ref] = answered_at

    def window_open_for(self, contact_ref: str) -> datetime | None:
        return self._last_inbound.get(contact_ref)

    async def send_message(
        self,
        contact_ref: str,
        message: str,
        idempotency_key: str,
        *,
        now: datetime | None = None,
        template_category: MessageCategory | None = None,
        free_entry_point_at: datetime | None = None,
    ) -> ActionResult:
        previous = self._results.get(idempotency_key)
        if previous is not None:
            return previous

        if not DESTINATION_PATTERN.match(contact_ref):
            # Refused rather than raised: the caller asked for something this client will
            # not do, which is the same shape as the cost refusal below. Sending anyway is
            # the dangerous option - Graph would accept it, rewrite it and deliver it to a
            # stranger, and report success while doing so.
            result = ActionResult(
                idempotency_key=idempotency_key,
                status="refused-invalid-destination",
                detail=(
                    f"{contact_ref!r} is not a full international number. WhatsApp does not "
                    "reject a malformed recipient - it prepends this business's country "
                    "calling code and delivers to whoever that produces."
                ),
            )
            self._results[idempotency_key] = result
            return result

        self._policy.require_external_network("whatsapp.send_message")

        decision = decide(
            posture=self._posture,
            last_inbound_at=self._last_inbound.get(contact_ref),
            now=now or datetime.now().astimezone(),
            template_category=template_category,
            free_entry_point_at=free_entry_point_at or self._free_entry_point.get(contact_ref),
        )
        if not decision.allowed:
            # Not an error: the caller asked for something that costs money in a posture
            # that forbids it. Reported as a refusal carrying the reason, so an operator
            # reading the log learns what would have to change.
            result = ActionResult(
                idempotency_key=idempotency_key,
                status="refused-would-be-charged",
                detail=decision.reason,
            )
            self._results[idempotency_key] = result
            return result

        payload = {
            "messaging_product": "whatsapp",
            # Required by the spec and omitted by most published examples.
            "recipient_type": "individual",
            "to": contact_ref,
            "type": "text",
            "text": {"body": message, "preview_url": False},
        }
        body = await self._post(payload)
        messages = body.get("messages") or [{}]
        result = ActionResult(
            idempotency_key=idempotency_key,
            status="sent",
            provider_reference=messages[0].get("id"),
            detail=_delivery_detail(contact_ref, body, messages[0].get("message_status", "")),
        )
        self._results[idempotency_key] = result
        return result

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not HTTPX_AVAILABLE:  # pragma: no cover - exercised by the availability test
            raise PermanentAdapterError(
                "Sending a WhatsApp message needs httpx. Install it with "
                '`pip install "pitchbot[whatsapp]"`.'
            )
        import httpx

        url = f"{self._base_url}/{self._api_version}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as error:
            raise TransientAdapterError(f"WhatsApp send timed out: {error}") from error
        except httpx.HTTPError as error:
            raise TransientAdapterError(f"WhatsApp send failed: {error}") from error

        if response.status_code == 200:
            decoded = response.json()
            return decoded if isinstance(decoded, dict) else {}
        raise _graph_error(response.status_code, response.text)


def _delivery_detail(contact_ref: str, body: dict[str, Any], status: str) -> str:
    """Report the number Meta says it delivered to, when it is not the one we asked for.

    Meta documents both halves of this explicitly - that ``wa_id`` "may not match `input`
    value", and that for **Brazil and Mexico** "the extra added prefix of the phone number
    may be modified by the Cloud API. This is a standard behavior of the system and is not
    considered a bug." So a mismatch is information, not an error, and is reported rather
    than raised.

    It is worth reporting because comparing the returned ``wa_id`` against the intended
    recipient is the only documented way to notice at send time that a number was rewritten
    - the request itself succeeds either way.
    """

    contacts = body.get("contacts") or []
    delivered = str(contacts[0].get("wa_id", "")) if contacts else ""
    intended = contact_ref.lstrip("+")
    if delivered and delivered != intended:
        return f"{status} (delivered to {delivered}, not {intended})".strip()
    return status


def _graph_error(status: int, text: str) -> Exception:
    """Turn Graph's error envelope into the right kind of adapter error.

    The distinction matters: a transient error is retried against a provider that has no
    idempotency key, so classifying a content rejection as transient would send the same
    rejected message repeatedly and damage the number's quality rating.
    """

    try:
        payload = json.loads(text)
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        code = int(error.get("code", 0))
        message = str(error.get("message", text))
    except (json.JSONDecodeError, TypeError, ValueError):
        code, message = 0, text

    label = f"WhatsApp Graph error {status} (code {code}): {message}"
    if code in _RATE_LIMITED or status == 429:
        return TransientAdapterError(f"{label} - rate limited")
    if code in _TRANSIENT or status >= 500:
        return TransientAdapterError(label)
    return PermanentAdapterError(label)


__all__ = [
    "DEFAULT_API_VERSION",
    "DEFAULT_BASE_URL",
    "DESTINATION_PATTERN",
    "HTTPX_AVAILABLE",
    "WhatsAppCloudAdapter",
]
