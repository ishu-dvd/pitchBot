from __future__ import annotations

from pitchbot.adapters.contracts import (
    ActionResult,
    ResearchResult,
    TelephonyAdapter,
    WhatsAppAdapter,
)
from pitchbot.adapters.network import NetworkPolicy


class NetworkDisabledTelephonyAdapter(TelephonyAdapter):
    def __init__(self) -> None:
        self._policy = NetworkPolicy()

    async def dial(self, contact_ref: str, idempotency_key: str) -> ActionResult:
        _ = (contact_ref, idempotency_key)
        self._policy.require_external_network("telephony.dial")
        raise AssertionError("unreachable")


class NetworkDisabledWhatsAppAdapter(WhatsAppAdapter):
    def __init__(self) -> None:
        self._policy = NetworkPolicy()

    async def send_message(
        self,
        contact_ref: str,
        message: str,
        idempotency_key: str,
    ) -> ActionResult:
        _ = (contact_ref, message, idempotency_key)
        self._policy.require_external_network("whatsapp.send_message")
        raise AssertionError("unreachable")


class NetworkDisabledResearchAdapter:
    def __init__(self) -> None:
        self._policy = NetworkPolicy()

    async def fetch(self, url: str) -> ResearchResult:
        _ = url
        self._policy.require_external_network("research.fetch")
        raise AssertionError("unreachable")
