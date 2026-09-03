"""Tests for the interactive talk CLI.

Two things matter here and neither is about formatting. The first is that the CLI runs the
**real** engine - if it drifted onto a demo path it would stop being evidence about the
product. The second is that it works with **no optional extra installed**, because its
whole reason to exist is letting someone try PitchBot before deciding to download a model
or a voice.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from pitchbot.cli.talk import build_parser, known_slots, main, render_turn
from pitchbot.conversation.planning import Slot, supported_languages
from pitchbot.domain import LanguageCode, RequirementFact


def _fact(key: str) -> RequirementFact:
    """A real domain fact, so the display is tested against the type it will receive."""

    return RequirementFact(lead_id=uuid4(), key=key, value="x", confidence=1.0)


ENGLISH_TURNS = (
    "We are an online clothing store.",
    "The site needs online payments and a catalogue.",
    "Our budget is 200000 rupees.",
)

TELUGU_TURNS = (
    "మేము ఆన్‌లైన్‌లో దుస్తులు అమ్ముతాము.",
    "మా బడ్జెట్ 200000 రూపాయలు.",
)


def _script(tmp_path: Path, turns: tuple[str, ...]) -> str:
    path = tmp_path / "turns.txt"
    path.write_text("\n".join(turns), encoding="utf-8")
    return str(path)


def test_importing_the_cli_does_not_import_any_optional_extra() -> None:
    """The heavy imports must stay inside the functions that need them.

    A module-level ``import piper`` would make ``pitchbot-talk --help`` fail on a machine
    with nothing installed, which is precisely the machine this command is for.
    """

    source = Path("src/pitchbot/cli/talk.py").read_text(encoding="utf-8")
    header = source.split("def ", 1)[0]
    for package in ("piper", "onnxruntime_genai", "faster_whisper", "numpy"):
        assert package not in header


def test_scripted_english_conversation_runs_without_extras(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--script", _script(tmp_path, ENGLISH_TURNS)])
    out = capsys.readouterr().out

    assert exit_code == 0
    # The reply must react to what was said, not repeat one fixed sentence.
    assert "Thanks, that helps me picture the business." in out
    assert "3 turns" in out
    # And the slots must accumulate rather than reset: displaying per-turn facts made the
    # conversation look like it kept forgetting, which was a bug in this CLI, not the engine.
    assert "business_type, requested_features, budget_stated" in out


def test_scripted_telugu_conversation_speaks_telugu(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--language", "te", "--script", _script(tmp_path, TELUGU_TURNS)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "నమస్కారం" in out
    assert "business_type" in out
    assert "budget_stated" in out


def test_quiet_prints_replies_without_the_breakdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--quiet", "--script", _script(tmp_path, ENGLISH_TURNS)])
    out = capsys.readouterr().out

    assert "bot" in out
    assert "knows" not in out
    assert "missing" not in out


def test_script_comments_and_blank_lines_are_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "annotated.txt"
    path.write_text(
        "# fills business_type\nWe are an online clothing store.\n\n\n", encoding="utf-8"
    )
    main(["--script", str(path)])

    assert "1 turns" in capsys.readouterr().out


def test_known_slots_are_reported_in_ask_order() -> None:
    """Order is the order the agent asks in, so the display reads as progress."""

    facts = [_fact("budget_stated"), _fact("business_type"), _fact("unrelated")]
    assert known_slots(facts) == ["business_type", "budget_stated"]


def test_render_turn_flags_a_safety_signal() -> None:
    """A refused or stopped turn must be visible, not buried in an ordinary reply."""

    from uuid import uuid4

    from pitchbot.conversation import ConversationEngine

    engine = ConversationEngine()
    session_id = uuid4()
    engine.create_session(session_id)
    result = engine.process_turn(
        session_id, text="Please do not call me again.", language=LanguageCode.ENGLISH
    )
    rendered = render_turn(result, [], 1.0, verbose=True)

    assert "SAFETY" in rendered
    assert "opt-out" in rendered


def test_every_supported_language_is_offered_by_the_parser() -> None:
    """A language the planner speaks but the CLI will not accept is unreachable."""

    action = next(a for a in build_parser()._actions if a.dest == "language")  # noqa: SLF001
    offered = {LanguageCode(value) for value in action.choices or ()}
    assert supported_languages() <= offered


def test_an_unknown_language_is_rejected_rather_than_answered_in_english(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["--language", "zz"])
    assert "invalid choice" in capsys.readouterr().err


def test_every_planner_slot_can_appear_in_the_breakdown() -> None:
    """The display enumerates `Slot`, so a new slot shows up without touching the CLI."""

    every = [_fact(slot.value) for slot in Slot]
    assert known_slots(every) == [slot.value for slot in Slot]
