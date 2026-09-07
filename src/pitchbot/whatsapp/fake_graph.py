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
from dataclasses import dataclass, field
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MESSAGING_PRODUCT: Final = "whatsapp"
_REQUIRED_FIELDS: Final = ("messaging_product", "recipient_type", "to", "type")
_RECIPIENT_TYPES: Final = frozenset({"individual", "group"})


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
            # What the real test number says when the recipient was never verified. It is
            # the first wall anyone hits, so the fake has to have it.
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
                "contacts": [{"input": to, "wa_id": to.lstrip("+")}],
                "messages": [{"id": message_id, "message_status": "accepted"}],
            }
        )


def _bearer(header: str) -> str:
    prefix = "bearer "
    return header[len(prefix) :].strip() if header.lower().startswith(prefix) else ""


def _error(status: int, message: str, *, code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "OAuthException", "code": code}},
        status_code=status,
    )


__all__ = ["FakeGraphApi", "SentMessage"]
