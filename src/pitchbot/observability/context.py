"""Which conversation a log line or a metric belongs to.

Before this, nothing correlated. Nineteen log statements across the codebase and not one of
them carried a session id, so "why was this call slow" could not be answered even in
principle: the transcription warning, the recall timeout and the reply-audio failure that
belonged to the same turn were indistinguishable from three unrelated ones.

Carried in :mod:`contextvars` rather than threaded through call signatures. The turn path
crosses the router, the service, the engine, the pipeline and two adapters, most of them
`async`, and a correlation id is not an argument any of them have an opinion about - passing
it explicitly would touch every signature on the path and still be forgotten by the next
caller. ``contextvars`` also does the one thing thread-locals get wrong here: a value set
inside a task is visible to everything that task awaits, and invisible to concurrent tasks
serving other sessions.

Nothing here is ever used as a metric label. Session and turn ids are unbounded, and a label
whose value space is unbounded turns a metrics registry into a memory leak - the same reason
the rate limiter keys on credentials rather than client addresses.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID, uuid4

_session_id: ContextVar[str | None] = ContextVar("pitchbot_session_id", default=None)
_turn_id: ContextVar[str | None] = ContextVar("pitchbot_turn_id", default=None)


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """The identifiers currently in scope. Either may be absent."""

    session_id: str | None
    turn_id: str | None

    def as_fields(self) -> dict[str, str]:
        fields = {}
        if self.session_id is not None:
            fields["session_id"] = self.session_id
        if self.turn_id is not None:
            fields["turn_id"] = self.turn_id
        return fields


def current_context() -> CorrelationContext:
    return CorrelationContext(session_id=_session_id.get(), turn_id=_turn_id.get())


@contextmanager
def correlated(
    *,
    session_id: UUID | str | None = None,
    turn_id: UUID | str | None = None,
) -> Iterator[CorrelationContext]:
    """Attach a session and/or turn to everything logged inside this block.

    Restores the previous values on exit, including on an exception, so a failed turn cannot
    leak its id into whatever the task does next.
    """

    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if session_id is not None:
        tokens.append((_session_id, _session_id.set(str(session_id))))
    if turn_id is not None:
        tokens.append((_turn_id, _turn_id.set(str(turn_id))))
    try:
        yield current_context()
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def new_turn_id() -> str:
    """A fresh id for one turn.

    Turns already carry an ``operation_id`` for idempotency, but that is chosen by the client
    and may legitimately repeat when a request is retried - which is exactly when two attempts
    must stay distinguishable in the logs.
    """

    return uuid4().hex


__all__ = ["CorrelationContext", "correlated", "current_context", "new_turn_id"]
