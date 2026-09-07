"""A local stand-in for Meta's Graph API, so WhatsApp can be exercised with no account.

Nothing about WhatsApp can be developed against the real thing for free without first
creating a Meta app, a Business account and a test number, and even then the test number
only reaches five hand-verified recipients. That is a poor loop to develop in and an
impossible one to run in CI, so this reproduces the parts of the wire protocol PitchBot
actually uses.

**The shape is copied from the published spec, not from memory.** A fake written from the
same recollection as the client it serves would agree with that client and prove nothing -
both would be wrong together and every test would pass. So the contract asserted here is
the one at
https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages:
``messaging_product``, ``recipient_type``, ``to`` and ``type`` are **required**, auth is a
bearer token, and the reply is a ``MessageResponsePayload`` of ``messaging_product``,
``contacts[].{input,wa_id}`` and ``messages[].{id,message_status}``.

``recipient_type`` in particular is required by the spec and omitted by most published
examples, which is exactly the kind of divergence a fake written from memory would miss.

It runs as an ASGI application, so tests drive it in-process through
``httpx.ASGITransport`` without binding a port, and a developer can equally serve it with
uvicorn and point a real :class:`~pitchbot.adapters.whatsapp_cloud.WhatsAppCloudAdapter`
at ``http://127.0.0.1:8080``.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MESSAGING_PRODUCT: Final = "whatsapp"
_REQUIRED_FIELDS: Final = ("messaging_product", "recipient_type", "to", "type")
_RECIPIENT_TYPES: Final = frozenset({"individual", "group"})

_SEPARATORS: Final = re.compile(r"[+\-() ]")
"""Exactly the characters Meta documents as supported and stripped in a ``to`` value."""


@dataclass(slots=True)
class SentMessage:
    """One message the fake accepted, kept so a test can assert on what was sent."""

    message_id: str
    to: str
    message_type: str
    body: str | None
    token: str
    payload: dict[str, Any]


@dataclass(slots=True)
class FakeGraphApi:
    """An in-memory Meta Graph API that records rather than delivers.

    The token, the phone-number id and the API version are all checked, because getting
    any of them wrong is a routine real-world failure and a fake that accepted anything
    would hide it. Error bodies mirror Graph's ``{"error": {...}}`` envelope so client-side
    error handling is exercised rather than imagined.
    """

    phone_number_id: str = "0000000000"
    expected_token: str = "test-token"
    api_version: str = "v22.0"
    sent: list[SentMessage] = field(default_factory=list)
    # The country calling code Meta prepends when a ``to`` arrives without a plus sign.
    # India, because that is the market this product sells into and therefore the code a
    # misdelivered message here would actually be sent with.
    business_country_code: str = "91"
    # An optional rewrite applied to the resolved recipient, modelling Meta's statement
    # that for Brazil and Mexico "the extra added prefix of the phone number may be
    # modified by the Cloud API. This is a standard behavior of the system and is not
    # considered a bug." The docs do not say which digit, in which direction, or under what
    # conditions - so the shape is supplied by the caller rather than guessed here.
    recipient_rewrite: Callable[[str], str] | None = None
    # Numbers the fake will accept, mirroring the real test number's allowlist of verified
    # recipients. Empty means "accept anyone", which the real thing never does.
    allowed_recipients: frozenset[str] = frozenset()
    fail_next: str | None = None

    def app(self) -> FastAPI:
        api = FastAPI(title="Fake WhatsApp Graph API")

        @api.post("/{version}/{phone_number_id}/messages")
        async def send(version: str, phone_number_id: str, request: Request) -> JSONResponse:
            return await self._send(version, phone_number_id, request)

        return api

    async def _send(self, version: str, phone_number_id: str, request: Request) -> JSONResponse:
        if self.fail_next is not None:
            detail, self.fail_next = self.fail_next, None
            return _error(500, detail, code=131_000)

        token = _bearer(request.headers.get("authorization", ""))
        if not hmac.compare_digest(token, self.expected_token):
            return _error(401, "Invalid OAuth access token.", code=190)
        if phone_number_id != self.phone_number_id:
            return _error(404, f"Unknown phone number id {phone_number_id}.", code=100)
        if version != self.api_version:
            return _error(400, f"Unsupported version {version}.", code=100)

        try:
            payload = json.loads(await request.body())
        except json.JSONDecodeError:
            return _error(400, "Malformed request body.", code=100)
        if not isinstance(payload, dict):
            return _error(400, "Request body must be an object.", code=100)

        for name in _REQUIRED_FIELDS:
            if not payload.get(name):
                return _error(400, f"(#100) The parameter {name} is required.", code=100)
        if payload["messaging_product"] != MESSAGING_PRODUCT:
            return _error(400, "messaging_product must be 'whatsapp'.", code=100)
        if payload["recipient_type"] not in _RECIPIENT_TYPES:
            return _error(400, "recipient_type must be 'individual' or 'group'.", code=100)

        to = str(payload["to"])
        if self.allowed_recipients and to not in self.allowed_recipients:
            # What the real test number said when the recipient was never verified.
            #
            # UNVERIFIED as of 2026-09-07: code 131030 and the wording "Recipient phone
            # number not in allowed list" do not appear anywhere in Meta's current
            # error-code reference (checked against both the new documentation URL and the
            # legacy one, 0 hits on each). It was a real historical development-mode code,
            # so it is kept - but it is a recollection, not a citation, and a client must
            # not branch on this specific number.
            return _error(400, "Recipient phone number not in allowed list.", code=131_030)

        message_type = str(payload["type"])
        body = None
        if message_type == "text":
            text = payload.get("text")
            if not isinstance(text, dict) or not text.get("body"):
                return _error(400, "(#100) The parameter text['body'] is required.", code=100)
            body = str(text["body"])

        message_id = f"wamid.FAKE{len(self.sent):06d}"
        self.sent.append(
            SentMessage(
                message_id=message_id,
                to=to,
                message_type=message_type,
                body=body,
                token=token,
                payload=payload,
            )
        )
        return JSONResponse(
            {
                "messaging_product": MESSAGING_PRODUCT,
                "contacts": [{"input": to, "wa_id": self.resolve_recipient(to)}],
                "messages": [{"id": message_id, "message_status": "accepted"}],
            }
        )

    def resolve_recipient(self, to: str) -> str:
        """Turn a submitted ``to`` into the number Meta would actually deliver to.

        Reproduces the documented behaviour rather than a convenient one, because the
        convenient version hides the single most dangerous property of this API:

            "Plus signs (+), hyphens (-), parenthesis ((,)), and spaces are supported in
            send message requests."

            "If the plus sign is omitted, your business phone number's country calling code
            is prepended to the customer's phone number. This can result in undelivered or
            misdelivered messages."

        So a malformed number is **not rejected**. It is silently rewritten and delivered to
        somebody else, and the request still returns 200. A fake that simply stripped the
        ``+`` would make that failure mode untestable, which is exactly how it would reach
        production unnoticed.

        Read 2026-09-07 from
        https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages
        """

        digits = _SEPARATORS.sub("", to)
        resolved = digits if to.strip().startswith("+") else f"{self.business_country_code}{digits}"
        return self.recipient_rewrite(resolved) if self.recipient_rewrite else resolved


def _bearer(header: str) -> str:
    prefix = "bearer "
    return header[len(prefix) :].strip() if header.lower().startswith(prefix) else ""


def _error(status: int, message: str, *, code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "OAuthException", "code": code}},
        status_code=status,
    )


__all__ = ["FakeGraphApi", "SentMessage"]
