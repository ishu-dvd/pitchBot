"""Receiving WhatsApp messages, which is what makes replying free.

This is not an optional half of the integration. WhatsApp only lets a business send a
free-form message inside the 24 hours after the customer wrote, so **the inbound webhook is
what creates the ability to send anything for free at all**. A build with only the outbound
half can do nothing that does not cost money.

Every structural detail here comes from the published contracts rather than recollection,
because each one is silently wrong in a way that still passes a test written from the same
memory:

* The payload shape is the one at
  https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks -
  ``entry[].changes[].value.messages[]``, and ``timestamp`` is a **string** of unix
  seconds, not an integer.
* Payloads are signed with HMAC-SHA256 over the **raw** body and delivered in
  ``X-Hub-Signature-256`` as ``sha256=<hex>``, per
  https://developers.facebook.com/docs/graph-api/webhooks/getting-started. Re-serialising
  the parsed JSON to check the signature would fail on any whitespace difference, so the
  raw bytes are used.
* That same page states failed deliveries are retried, and the WhatsApp-specific webhook
  guide is more precise than the generic Graph one: *"delivery is retried immediately, then
  a few more times with decreasing frequency over the next 7 days"* and *"your server
  should handle deduplication"*. So message ids are remembered and repeats are acknowledged
  without being processed twice. Getting this wrong is not cosmetic - a replayed message
  would reopen a free-send window that had closed.
* Endpoints must answer ``200 OK``. This one answers 200 even for a payload it does not
  understand, because a non-200 buys seven days of retries for a message that will never
  parse any better the second time.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

#: How many delivered message ids to remember for deduplication. Meta retries for 7 days
#: and batches up to 1000 updates, so this is sized to survive a burst rather than a day.
DEDUPE_CAPACITY: Final = 4096


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One message a customer sent us."""

    message_id: str
    contact_ref: str
    profile_name: str | None
    text: str | None
    message_type: str
    sent_at: datetime


def parse_inbound(payload: Any) -> tuple[InboundMessage, ...]:
    """Pull every customer message out of a webhook payload, ignoring the rest.

    A single payload carries status updates, template quality changes and account alerts
    alongside actual messages, and may batch several accounts together. Anything that is
    not a message is skipped rather than treated as an error, because a webhook that 500s
    on a status update stops delivering messages too.
    """

    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return ()
    found: list[InboundMessage] = []
    for entry in _items(payload.get("entry")):
        for change in _items(entry.get("changes")):
            if change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            names = _profile_names(value)
            for raw in _items(value.get("messages")):
                message = _one_message(raw, names)
                if message is not None:
                    found.append(message)
    return tuple(found)


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _profile_names(value: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in _items(value.get("contacts")):
        wa_id = contact.get("wa_id")
        profile = contact.get("profile")
        if isinstance(wa_id, str) and isinstance(profile, dict):
            name = profile.get("name")
            if isinstance(name, str):
                names[wa_id] = name
    return names


def _one_message(raw: dict[str, Any], names: dict[str, str]) -> InboundMessage | None:
    message_id = raw.get("id")
    sender = raw.get("from")
    if not isinstance(message_id, str) or not isinstance(sender, str):
        return None
    text = None
    body = raw.get("text")
    if isinstance(body, dict) and isinstance(body.get("body"), str):
        text = body["body"]
    return InboundMessage(
        message_id=message_id,
        contact_ref=sender,
        profile_name=names.get(sender),
        text=text,
        message_type=str(raw.get("type", "unknown")),
        # Documented as a string of unix seconds. Anything unparseable is treated as
        # "now", because the timestamp only decides when the free window closes and
        # assuming it opened now is the conservative reading.
        sent_at=_timestamp(raw.get("timestamp")),
    )


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def valid_signature(*, raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """Whether a payload really came from Meta.

    Computed over the raw bytes, because the signature covers what was sent and not what a
    JSON round-trip produces.
    """

    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])


class WebhookReceiver:
    """The endpoint Meta calls, and the memory of who has written to us.

    ``on_message`` is where an application does its work. It is called once per message,
    after deduplication, and never for a replay.
    """

    def __init__(
        self,
        *,
        verify_token: str,
        app_secret: str,
        on_message: Callable[[InboundMessage], None] | None = None,
        require_signature: bool = True,
    ) -> None:
        if not verify_token.strip():
            raise ValueError("verify_token must not be empty")
        self._verify_token = verify_token
        self._app_secret = app_secret
        self._on_message = on_message
        # Only ever disabled for the local fake, where there is no app secret to sign with.
        # Off by default would mean anyone who learns the URL can inject a customer message
        # and thereby open a free-send window against a number they do not own.
        self._require_signature = require_signature
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.received: list[InboundMessage] = []

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/webhook")
        async def verify(request: Request) -> Response:
            params = request.query_params
            if params.get("hub.mode") == "subscribe" and hmac.compare_digest(
                params.get("hub.verify_token", ""), self._verify_token
            ):
                # Echoed verbatim as text/plain: Meta compares the body byte for byte, and
                # a JSON-quoted challenge fails verification.
                return PlainTextResponse(params.get("hub.challenge", ""))
            return PlainTextResponse("Verification failed", status_code=403)

        @router.post("/webhook")
        async def receive(request: Request) -> Response:
            raw = await request.body()
            if self._require_signature and not valid_signature(
                raw_body=raw,
                header=request.headers.get("x-hub-signature-256"),
                app_secret=self._app_secret,
            ):
                return JSONResponse({"status": "invalid signature"}, status_code=403)
            import json

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                # Still 200: a body that is not JSON will not become JSON on the retry, and
                # refusing it buys seven days of redelivery for nothing.
                return JSONResponse({"status": "ignored"})
            delivered = self.deliver(parse_inbound(payload))
            return JSONResponse({"status": "ok", "delivered": delivered})

        return router

    def deliver(self, messages: tuple[InboundMessage, ...]) -> int:
        delivered = 0
        for message in messages:
            if message.message_id in self._seen:
                continue
            self._seen[message.message_id] = None
            if len(self._seen) > DEDUPE_CAPACITY:
                self._seen.popitem(last=False)
            self.received.append(message)
            delivered += 1
            if self._on_message is not None:
                self._on_message(message)
        return delivered


__all__ = [
    "DEDUPE_CAPACITY",
    "InboundMessage",
    "WebhookReceiver",
    "parse_inbound",
    "valid_signature",
]
