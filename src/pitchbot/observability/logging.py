"""JSON logs that carry the conversation, and refuse to carry what the buyer said.

Two problems are solved here, and the second matters more.

**Correlation.** Nineteen log statements, none of which named a session, so the transcription
warning, the recall timeout and the reply-audio failure belonging to one turn were
indistinguishable from three unrelated ones. The formatter injects whatever
:mod:`pitchbot.observability.context` has in scope, so no call site has to remember.

**Redaction.** Structured logging makes it trivially easy to attach "just a bit of context"
to a line, and the most tempting context on this code path is the transcript. This service
promises `audio_retained: false` and keeps buyer speech out of its journal; leaking the same
text into stdout through a convenience field would break that promise somewhere nobody thinks
to look. So field names that carry buyer content or credentials are **redacted by the
formatter**, not by convention - a reviewer cannot forget, and a new caller cannot opt in by
accident.

Redaction is by field name rather than by inspecting values, because a value-based check
either misses (a transcript is just a string) or destroys legitimate content. Names are
matched on a substring so `text`, `reply_text` and `utterance_text` are all covered by one
entry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any, Final

from pitchbot.observability.context import current_context

REDACTED: Final[str] = "[redacted]"

SENSITIVE_FIELD_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "authorization",
        "content",
        "credential",
        "message_body",
        "password",
        "phone",
        "reply",
        "secret",
        "text",
        "token",
        "transcript",
        "utterance",
    }
)
"""Substrings that make a field name unloggable.

`reply` and `text` are here for the same reason `transcript` is: the agent's reply quotes the
buyer's own numbers back at them, so it carries the same content by a different name.
"""

_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def is_sensitive(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS)


def redact(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Replace the value of any sensitive-looking field, keeping the key visible.

    The key is kept deliberately. A line that says `transcript: [redacted]` tells a reader
    that text existed and was withheld; dropping the key would make the redaction itself
    invisible and indistinguishable from the field never being set.
    """

    return {name: (REDACTED if is_sensitive(name) else value) for name, value in fields.items()}


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, with correlation ids and redacted extras."""

    def __init__(self, *, service: str = "pitchbot") -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "message": record.getMessage(),
        }
        payload.update(current_context().as_fields())

        extras = {
            name: value
            for name, value in record.__dict__.items()
            if name not in _RESERVED and not name.startswith("_")
        }
        if extras:
            payload.update(redact(extras))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # `default=str` so a UUID or a datetime in an extra cannot make logging raise - a
        # logger that throws turns a diagnosable failure into an undiagnosable one.
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    *,
    level: str = "INFO",
    json_format: bool = True,
    stream: Any | None = None,
    extra_handlers: Iterable[logging.Handler] = (),
) -> None:
    """Install the formatter on the root logger.

    Replaces existing handlers rather than adding to them, so calling this twice - which
    `uvicorn --reload` does - cannot double every line.
    """

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        JsonLogFormatter()
        if json_format
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    for extra in extra_handlers:
        root.addHandler(extra)
    root.setLevel(level.upper())


def take_over_third_party_loggers(names: Iterable[str] = ()) -> None:
    """Route libraries that install their own handlers through the root formatter.

    Uvicorn calls ``dictConfig`` when the server starts, which is *after* this module is
    imported, and gives ``uvicorn.access`` its own handler with ``propagate`` disabled. The
    result is a process emitting two log formats at once: JSON from the application and
    plain text from every request line, so half the output is unparseable by whatever reads
    the other half - and the request lines are exactly the ones an operator greps first.

    Clearing the handlers and re-enabling propagation hands those records to the root
    logger, where they get correlation ids and redaction like everything else.

    Must be called *after* the third-party library has configured itself. For uvicorn that
    means application startup, not import.
    """

    for name in names or DEFAULT_TAKEOVER_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


DEFAULT_TAKEOVER_LOGGERS: Final[tuple[str, ...]] = (
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
)


__all__ = [
    "DEFAULT_TAKEOVER_LOGGERS",
    "REDACTED",
    "SENSITIVE_FIELD_MARKERS",
    "JsonLogFormatter",
    "configure_logging",
    "is_sensitive",
    "redact",
    "take_over_third_party_loggers",
]
