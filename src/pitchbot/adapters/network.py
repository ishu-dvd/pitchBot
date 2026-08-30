from __future__ import annotations

from dataclasses import dataclass

from pitchbot.adapters.errors import ExternalNetworkDisabledError


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    external_network_enabled: bool = False

    def require_external_network(self, operation: str) -> None:
        if not self.external_network_enabled:
            raise ExternalNetworkDisabledError(
                f"External network is disabled; blocked operation: {operation}"
            )
