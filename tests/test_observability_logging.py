"""Logs must correlate, and must never carry what the buyer said.

The second half is the one that matters. This service promises `audio_retained: false` and
keeps buyer speech out of its journal; structured logging makes it a one-line mistake to
undo that by attaching "a bit of context" to a warning. Redaction is therefore enforced by
the formatter rather than by review, and these tests are what make that enforcement real.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from pitchbot.observability.context import correlated, current_context, new_turn_id
from pitchbot.observability.logging import (
    REDACTED,
    JsonLogFormatter,
    configure_logging,
    is_sensitive,
    redact,
)


def _emit(logger_name: str = "pitchbot.test", **kwargs: object) -> dict[str, object]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger(logger_name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("something happened", extra=kwargs)
    payload = json.loads(stream.getvalue().strip())
    assert isinstance(payload, dict)
    return payload


# --------------------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------------------


def test_no_context_by_default() -> None:
    context = current_context()
    assert context.session_id is None
    assert context.turn_id is None


def test_context_reaches_the_log_line_without_the_call_site_knowing() -> None:
    with correlated(session_id="abc", turn_id="t1"):
        payload = _emit()
    assert payload["session_id"] == "abc"
    assert payload["turn_id"] == "t1"


def test_context_is_restored_on_exit() -> None:
    with correlated(session_id="outer"):
        with correlated(session_id="inner"):
            assert current_context().session_id == "inner"
        assert current_context().session_id == "outer"
    assert current_context().session_id is None


def test_context_is_restored_even_when_the_block_raises() -> None:
    """A failed turn must not leak its id into whatever the task does next."""

    with pytest.raises(ValueError, match="boom"), correlated(session_id="doomed"):
        raise ValueError("boom")
    assert current_context().session_id is None


def test_turn_ids_are_unique() -> None:
    assert new_turn_id() != new_turn_id()


# --------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "transcript",
        "reply_text",
        "utterance_text",
        "text",
        "api_key",
        "Authorization",
        "message_body",
        "buyer_phone",
        "secret_token",
        "content",
    ],
)
def test_content_and_credential_fields_are_redacted(field: str) -> None:
    payload = _emit(**{field: "we run a retail shop and our budget is 50,000 rupees"})
    assert payload[field] == REDACTED


def test_redaction_keeps_the_key_so_the_withholding_is_visible() -> None:
    """`transcript: [redacted]` says text existed; dropping the key would hide that."""

    redacted = redact({"transcript": "hello"})
    assert redacted == {"transcript": REDACTED}


def test_harmless_fields_survive() -> None:
    payload = _emit(outcome="transcribed", transcribe_ms=2407.0, declined_language="te")
    assert payload["outcome"] == "transcribed"
    assert payload["transcribe_ms"] == 2407.0
    assert payload["declined_language"] == "te"


def test_sensitivity_is_matched_on_a_substring_and_case_insensitively() -> None:
    assert is_sensitive("TRANSCRIPT")
    assert is_sensitive("agent_reply")
    assert is_sensitive("X_Api_Key")
    assert not is_sensitive("outcome")
    assert not is_sensitive("language")


def test_the_message_itself_is_not_redacted() -> None:
    """Redaction is by field name; a hand-written message is the author's responsibility."""

    payload = _emit()
    assert payload["message"] == "something happened"


# --------------------------------------------------------------------------------------
# Shape and robustness
# --------------------------------------------------------------------------------------


def test_every_line_is_one_json_object_with_the_expected_envelope() -> None:
    payload = _emit()
    for key in ("timestamp", "level", "logger", "service", "message"):
        assert key in payload
    assert payload["level"] == "INFO"
    assert payload["service"] == "pitchbot"


def test_an_unserialisable_extra_does_not_break_logging() -> None:
    """A logger that throws turns a diagnosable failure into an undiagnosable one."""

    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    payload = _emit(thing=Opaque())
    assert payload["thing"] == "<opaque>"


def test_exceptions_are_captured() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("pitchbot.test.exc")
    logger.handlers = [handler]
    logger.propagate = False
    try:
        raise ValueError("kaboom")
    except ValueError:
        logger.exception("it broke")
    payload = json.loads(stream.getvalue().strip())
    assert "kaboom" in payload["exception"]


def test_configure_logging_replaces_handlers_rather_than_stacking_them() -> None:
    """`uvicorn --reload` calls this twice; doubling every line would be its own bug."""

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    configure_logging(level="INFO", stream=stream)
    logging.getLogger().info("once")
    assert stream.getvalue().count("once") == 1
    configure_logging(level="INFO", json_format=False)


def test_third_party_loggers_are_routed_through_the_root_formatter() -> None:
    """Uvicorn configures itself AFTER import and gives uvicorn.access its own handler.

    Left alone, the process emits two formats at once: JSON from the application and plain
    text for every request line -- and the request lines are the ones an operator greps
    first. Verified live before this existed: access logs came out unstructured.
    """

    from pitchbot.observability.logging import (
        DEFAULT_TAKEOVER_LOGGERS,
        take_over_third_party_loggers,
    )

    hijacked = logging.getLogger("uvicorn.access")
    hijacked.handlers = [logging.StreamHandler(io.StringIO())]
    hijacked.propagate = False

    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    take_over_third_party_loggers()

    assert hijacked.handlers == []
    assert hijacked.propagate is True

    hijacked.info("127.0.0.1 - GET /health 200")
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload["logger"] == "uvicorn.access"
    assert payload["message"] == "127.0.0.1 - GET /health 200"
    assert "uvicorn.access" in DEFAULT_TAKEOVER_LOGGERS

    configure_logging(level="INFO", json_format=False)
