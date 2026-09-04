"""API credentials for the simulator's HTTP and WebSocket surface.

Until this existed the API had no authentication of any kind: ten HTTP endpoints and one
WebSocket, no dependency, no key, no rate limit. That is survivable on a laptop and
indefensible anywhere else, and it is worse than the usual "open API" because a turn costs
seconds of CPU on this hardware - an unauthenticated caller does not just read data, they
consume the machine.

Two decisions worth stating, because both are deliberate:

**Deny-by-default is scoped to where it means something.** With no keys configured and
``app_env == "local"`` the store is not enforcing, so the local demo keeps working exactly
as it did. With no keys configured anywhere else the application refuses to start - see
:mod:`pitchbot.config`. A silent unauthenticated production server is the failure this
module exists to prevent, so it is made impossible rather than discouraged.

**A weak key is refused, not accepted with a warning.** A 4-character secret looks like
authentication while providing none, which is strictly worse than the honest "no auth"
state it replaced, because it stops anyone asking the question again.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

MIN_SECRET_LENGTH: Final[int] = 16
"""Shortest secret accepted. Short enough to type, long enough not to be guessed."""


@dataclass(frozen=True, slots=True)
class ApiCredential:
    """The identity behind an accepted request, used for rate limiting and logging.

    Carries the credential's *name*, never its secret, so it is safe to log and safe to
    attach to a metric label.
    """

    name: str


def parse_api_keys(value: str) -> tuple[tuple[str, str], ...]:
    """``"web:s3cret...,ops:0th3r..."`` to ``(name, secret)`` pairs.

    Refuses duplicates by name *and* by secret. Two names sharing a secret cannot be told
    apart afterwards, so every per-credential decision made downstream - rate limiting,
    attribution in logs - would silently be about the wrong one.
    """

    entries: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    seen_secrets: set[str] = set()
    for raw in (item.strip() for item in value.split(",")):
        if not raw:
            continue
        name, separator, secret = raw.partition(":")
        name = name.strip()
        secret = secret.strip()
        if not separator or not name or not secret:
            raise ValueError(
                f"api key entry {raw!r} must be '<name>:<secret>', for example "
                "'web:a-long-random-string'"
            )
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"api key {name!r} has a {len(secret)}-character secret; at least "
                f"{MIN_SECRET_LENGTH} are required. A short key is not weak "
                "authentication, it is the appearance of authentication"
            )
        if name in seen_names:
            raise ValueError(f"api key name {name!r} is defined twice")
        if secret in seen_secrets:
            raise ValueError(
                f"api key {name!r} reuses another key's secret; the two could never be "
                "distinguished afterwards"
            )
        seen_names.add(name)
        seen_secrets.add(secret)
        entries.append((name, secret))
    return tuple(entries)


class CredentialStore:
    """Matches a presented secret against the configured credentials in constant time."""

    def __init__(self, entries: Iterable[tuple[str, str]]) -> None:
        self._entries = tuple((name, secret.encode("utf-8")) for name, secret in entries)

    @property
    def enforcing(self) -> bool:
        """Whether any credential is configured. When false, every request is admitted."""

        return bool(self._entries)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._entries)

    def identify(self, presented: str | None) -> ApiCredential | None:
        """Return the matching credential, or ``None``.

        Every configured secret is compared even after one matches. Returning early would
        make the response time depend on which key was presented, which over enough
        requests reveals the ordering and then the keys themselves.
        """

        if not self._entries or presented is None:
            return None
        candidate = presented.encode("utf-8")
        matched: ApiCredential | None = None
        for name, secret in self._entries:
            if hmac.compare_digest(candidate, secret):
                matched = ApiCredential(name=name)
        return matched


__all__ = ["MIN_SECRET_LENGTH", "ApiCredential", "CredentialStore", "parse_api_keys"]
