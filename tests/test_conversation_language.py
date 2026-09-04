"""A buyer may change language mid-conversation, and everything must follow.

The declared language used to be decided once, by the caller, and never revisited. In an
Indian B2B call that is not an edge case - a buyer opening in English and moving to Hindi
once the conversation gets concrete is ordinary - and until this was handled the agent
carried on in the language nobody was speaking.

Two things make it worth testing this hard rather than trusting a language detector.

First, the *cost of being wrong in each direction is not symmetric*. Failing to switch
loses one buyer's patience. Switching when nothing happened changes the reply language,
the synthesised voice and the transcriber expectation all at once, on the strength of one
borrowed word - so these tests pin the evidence needed in both directions, not just that a
switch can happen.

Second, on the audio path a *missed* switch is invisible rather than obviously broken.
A transcriber forced to the old language returns fluent text in that language (measured;
see ``docs/BENCHMARKS.md``), so the conversation looks healthy while the buyer is being
misquoted. There is no failing assertion available downstream. It has to be caught here.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pitchbot.conversation.engine import ConversationEngine
from pitchbot.conversation.language import (
    DEFAULT_SWITCH_AFTER,
    LanguageEvidence,
    decide_language,
    detect_language,
    switchable_languages,
)
from pitchbot.conversation.models import ConversationStateCheckpoint
from pitchbot.conversation.planning import supported_languages
from pitchbot.domain import LanguageCode

DIGEST_KEY = b"language-switching-test-key-32b!"


def _session(*, detect_language_switch: bool = True) -> tuple[ConversationEngine, UUID]:
    engine = ConversationEngine(
        turn_digest_key=DIGEST_KEY,
        detect_language_switch=detect_language_switch,
    )
    session_id = uuid4()
    engine.create_session(session_id)
    return engine, session_id


# --------------------------------------------------------------------------------------
# Reading one turn
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("हमारी दुकान कपड़ों की है", LanguageCode.HINDI),
        ("మా దుకాణం బట్టల దుకాణం", LanguageCode.TELUGU),
        ("We run a clothing shop", LanguageCode.ENGLISH),
    ],
)
def test_script_identifies_the_language_it_is_written_in(text: str, expected: LanguageCode) -> None:
    reading = detect_language(text)
    assert reading.language is expected
    assert reading.evidence is LanguageEvidence.SCRIPT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A request may be written in any script, including one that is not the script of
        # the language being asked for. A Hindi speaker asks for English in Devanagari,
        # and indexing requests only by their target's own script would catch the request
        # nobody actually makes.
        ("can you speak in hindi", LanguageCode.HINDI),
        ("hindi mein baat karo", LanguageCode.HINDI),
        ("कृपया अंग्रेजी में बात करें", LanguageCode.ENGLISH),
        ("please switch to english", LanguageCode.ENGLISH),
        ("మీరు తెలుగులో మాట్లాడండి", LanguageCode.TELUGU),
        ("telugu mein baat kar sakte hain", LanguageCode.TELUGU),
    ],
)
def test_naming_a_language_and_a_way_of_speaking_it_is_a_request(
    text: str, expected: LanguageCode
) -> None:
    reading = detect_language(text)
    assert reading.language is expected
    assert reading.evidence is LanguageEvidence.REQUESTED
    assert reading.is_request


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Telugu attaches its case endings to the noun, so the inflected form does not
        # contain the citation form: "ఇంగ్లీషులో" ("in English") replaces the citation
        # form's final virama with a vowel sign. Matching citation forms silently missed
        # every Telugu-language request for English - which is precisely the turn a Telugu
        # speaker uses to ask for English. Found by running the shipped example script,
        # not by a unit test written from the same assumption as the code.
        ("దయచేసి ఇంగ్లీషులో మాట్లాడండి.", LanguageCode.ENGLISH),
        ("తెలుగులో చెప్పండి", LanguageCode.TELUGU),
        ("హిందీలో మాట్లాడగలరా", LanguageCode.HINDI),
    ],
)
def test_an_inflected_language_name_is_still_a_request(text: str, expected: LanguageCode) -> None:
    reading = detect_language(text)
    assert reading.evidence is LanguageEvidence.REQUESTED
    assert reading.language is expected


@pytest.mark.parametrize(
    "text",
    [
        "we sell Hindi books online",
        "our English department needs a catalogue",
        "the telugu market is our biggest",
    ],
)
def test_mentioning_a_language_is_not_asking_for_it(text: str) -> None:
    """A name without a way of speaking is a noun, not an instruction.

    This is the false positive that would hurt most: a buyer describing their *product*
    would have the whole conversation moved into a language they never asked for, and the
    sentence that caused it would look completely ordinary in the transcript.
    """

    reading = detect_language(text)
    assert not reading.is_request


def test_naming_two_languages_at_once_changes_nothing() -> None:
    """Genuinely ambiguous, so it is refused rather than resolved by ordering.

    Guessing here lands the conversation in the language the buyer was moving *away*
    from as often as not, and a wrong guess costs a switch of voice and transcriber.
    """

    reading = detect_language("I speak Hindi but can we talk in English")
    assert not reading.is_request


def test_romanised_indic_vocabulary_is_read_as_hinglish() -> None:
    reading = detect_language("aapka business kya hai aur kitna budget hai")
    assert reading.language is LanguageCode.MIXED
    assert reading.evidence is LanguageEvidence.VOCABULARY


def test_one_borrowed_word_is_not_a_language() -> None:
    """ "Namaste, we run a shop" is an English sentence containing a greeting."""

    reading = detect_language("Namaste, we run a retail shop")
    assert reading.language is LanguageCode.ENGLISH
    assert reading.evidence is LanguageEvidence.SCRIPT


def test_a_transcriber_label_is_used_only_when_nothing_can_be_read_from_the_text() -> None:
    """Text evidence outranks the transcriber, deliberately.

    On the one turn a switch happens, a forced transcriber reports the language it was
    told to expect - so the label is least reliable exactly when it matters most.
    """

    from_text = detect_language("हमारा बजट पचास हज़ार है", transcribed_as=LanguageCode.ENGLISH)
    assert from_text.language is LanguageCode.HINDI

    from_label = detect_language("...", transcribed_as=LanguageCode.TELUGU)
    assert from_label.language is LanguageCode.TELUGU
    assert from_label.evidence is LanguageEvidence.TRANSCRIBER


def test_only_languages_the_planner_can_speak_are_switchable() -> None:
    """Detecting a language and being able to hold a conversation in it differ.

    Switching into a language with no phrases would replace a wrong-language reply with
    a ``KeyError``, which is not an improvement.
    """

    assert switchable_languages() >= supported_languages()
    assert LanguageCode.UNKNOWN not in switchable_languages()


# --------------------------------------------------------------------------------------
# Deciding whether to act on it
# --------------------------------------------------------------------------------------


def test_a_detected_language_needs_consecutive_turns_before_it_switches() -> None:
    reading = detect_language("हमारी दुकान कपड़ों की है")

    first = decide_language(current=LanguageCode.ENGLISH, reading=reading)
    assert first.language is LanguageCode.ENGLISH
    assert not first.switched
    assert first.pending is LanguageCode.HINDI

    second = decide_language(
        current=LanguageCode.ENGLISH,
        reading=reading,
        pending=first.pending,
        pending_count=first.pending_count,
    )
    assert second.language is LanguageCode.HINDI
    assert second.switched
    assert second.pending is None


def test_a_request_is_obeyed_on_the_turn_it_is_made() -> None:
    """Hysteresis is for evidence, not for instructions.

    A person who asks to be spoken to in Hindi and is answered twice more in English has
    been ignored, and knows it.
    """

    decision = decide_language(
        current=LanguageCode.ENGLISH,
        reading=detect_language("please speak in hindi"),
    )
    assert decision.language is LanguageCode.HINDI
    assert decision.switched


def test_returning_to_the_current_language_cancels_a_partial_switch() -> None:
    """Hysteresis counts *consecutive* turns, so one turn back resets it.

    Without this a conversation that alternates languages would accumulate votes and
    eventually switch on a tie, which is the least predictable behaviour available.
    """

    started = decide_language(
        current=LanguageCode.ENGLISH,
        reading=detect_language("हमारी दुकान है"),
    )
    assert started.pending is LanguageCode.HINDI

    back = decide_language(
        current=LanguageCode.ENGLISH,
        reading=detect_language("We need online orders"),
        pending=started.pending,
        pending_count=started.pending_count,
    )
    assert back.pending is None
    assert back.pending_count == 0
    assert not back.switched


def test_alternating_languages_never_switch() -> None:
    hindi = detect_language("हमारी दुकान है")
    english = detect_language("We need online orders")
    pending, count = None, 0
    for reading in (hindi, english) * 6:
        decision = decide_language(
            current=LanguageCode.ENGLISH,
            reading=reading,
            pending=pending,
            pending_count=count,
        )
        assert not decision.switched
        pending, count = decision.pending, decision.pending_count


def test_an_unreadable_turn_leaves_everything_alone() -> None:
    decision = decide_language(
        current=LanguageCode.HINDI,
        reading=detect_language("12345 !!!"),
        pending=LanguageCode.ENGLISH,
        pending_count=1,
    )
    assert decision.language is LanguageCode.HINDI
    assert not decision.switched
    assert decision.pending is LanguageCode.ENGLISH


# --------------------------------------------------------------------------------------
# The conversation acting on it
# --------------------------------------------------------------------------------------


def test_the_engine_follows_a_buyer_who_switches() -> None:
    engine, session = _session()
    engine.process_turn(session, text="We run a clothing shop", language=LanguageCode.ENGLISH)
    first = engine.process_turn(session, text="हमें ऑनलाइन ऑर्डर चाहिए", language=LanguageCode.ENGLISH)
    assert first.language is LanguageCode.ENGLISH
    assert not first.language_switched

    second = engine.process_turn(
        session, text="हमारा बजट पचास हज़ार है", language=LanguageCode.ENGLISH
    )
    assert second.language is LanguageCode.HINDI
    assert second.language_switched


# --------------------------------------------------------------------------------------
# Switching without asking
#
# The buyer who *says nothing about it* and simply starts speaking another language is the
# ordinary case, not the explicit request - people rarely announce it. It is also the
# harder one, because there is no instruction to obey, only evidence to weigh, and the
# system has to be certain enough on that evidence to move a whole conversation.
# --------------------------------------------------------------------------------------


OPENING_TURNS: dict[LanguageCode, str] = {
    LanguageCode.ENGLISH: "We run a shop",
    LanguageCode.HINDI: "हमारी दुकान है",
    LanguageCode.TELUGU: "మా దుకాణం ఉంది",
}
"""A first turn genuinely written in each language.

Not a neutral-looking token: ``"ok"`` is Latin script and is therefore *English evidence*,
so priming a Hindi session with it starts a vote to leave Hindi before the test has begun.
The first turn counts like any other, which is the point of the test above it.
"""


@pytest.mark.parametrize(
    ("opening", "switch_to", "turns"),
    [
        (
            LanguageCode.ENGLISH,
            LanguageCode.HINDI,
            ("हमें ऑनलाइन ऑर्डर चाहिए", "हमारा बजट पचास हज़ार है"),
        ),
        (
            LanguageCode.ENGLISH,
            LanguageCode.TELUGU,
            ("మాకు ఆన్‌లైన్ ఆర్డర్లు కావాలి", "మా బడ్జెట్ యాభై వేలు"),
        ),
        (
            LanguageCode.HINDI,
            LanguageCode.ENGLISH,
            ("We need online orders", "Our budget is 50000 rupees"),
        ),
        (
            LanguageCode.TELUGU,
            LanguageCode.HINDI,
            ("हमें ऑनलाइन ऑर्डर चाहिए", "हमारा बजट पचास हज़ार है"),
        ),
    ],
)
def test_a_buyer_who_never_mentions_it_still_moves_the_conversation(
    opening: LanguageCode, switch_to: LanguageCode, turns: tuple[str, str]
) -> None:
    """No request, no keyword - just two turns in another language.

    Every pair here is a direction someone actually takes on a call, including *out* of an
    Indic language back into English, which is the direction a caller declaring Hindi
    would otherwise never recover from.
    """

    engine, session = _session()
    engine.process_turn(session, text=OPENING_TURNS[opening], language=opening)

    first = engine.process_turn(session, text=turns[0], language=opening)
    assert first.language is opening
    assert not first.language_switched

    second = engine.process_turn(session, text=turns[1], language=opening)
    assert second.language is switch_to
    assert second.language_switched


def test_a_buyer_can_switch_and_come_back_without_asking_either_time() -> None:
    """Switching is not one-way, and the return trip needs the same evidence.

    A conversation that could move to Hindi but never move back would strand a buyer who
    used two Hindi sentences and then returned to English for the rest of the call.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a toy shop", language=LanguageCode.ENGLISH)

    engine.process_turn(session, text="हमें कैटलॉग चाहिए", language=LanguageCode.ENGLISH)
    there = engine.process_turn(
        session, text="हमारा बजट पचास हज़ार है", language=LanguageCode.ENGLISH
    )
    assert there.language is LanguageCode.HINDI
    assert there.language_switched

    engine.process_turn(session, text="We need online payments", language=LanguageCode.ENGLISH)
    back = engine.process_turn(
        session, text="When can you start the work", language=LanguageCode.ENGLISH
    )
    assert back.language is LanguageCode.ENGLISH
    assert back.language_switched


def test_typing_hinglish_without_asking_is_answered_in_hinglish() -> None:
    """Romanised code-switching is a switch too, and the commonest one typed.

    A buyer writing Latin-script Hindi cannot be told from an English speaker by script,
    so the vocabulary signal is the only thing that separates them - and the reply comes
    back in the same romanised register rather than in formal Devanagari.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    engine.process_turn(session, text="aapka kaam kitna accha hai", language=LanguageCode.ENGLISH)
    result = engine.process_turn(
        session, text="hamara budget kitna hona chahiye batao", language=LanguageCode.ENGLISH
    )
    assert result.language is LanguageCode.MIXED
    assert result.language_switched
    assert not any("\u0900" <= character <= "\u097f" for character in result.reply)


def test_a_transcriber_label_alone_can_move_a_conversation() -> None:
    """Speech whose text carries no script evidence still has the transcriber's label.

    This is the path that existed and was unreachable: the pipeline computed a language
    for every utterance and the CLI discarded it, so on the voice loop - where speech is
    the only input there is - the transcriber's own evidence was never used at all.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)

    first = engine.process_turn(
        session,
        text="123 456",
        language=LanguageCode.ENGLISH,
        transcribed_as=LanguageCode.TELUGU,
    )
    assert not first.language_switched

    second = engine.process_turn(
        session,
        text="789 101",
        language=LanguageCode.ENGLISH,
        transcribed_as=LanguageCode.TELUGU,
    )
    assert second.language is LanguageCode.TELUGU
    assert second.language_switched


def test_a_transcriber_label_never_overrules_the_words_themselves() -> None:
    """A forced transcriber reports what it was told to expect, so the text wins.

    Measured: Hindi audio forced to ``en`` returns fluent English at probability 1.00. If
    the label outranked the script, that turn would confirm the conversation was still in
    English on the strength of the very evidence the forcing fabricated.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    engine.process_turn(
        session,
        text="हमें ऑनलाइन ऑर्डर चाहिए",
        language=LanguageCode.ENGLISH,
        transcribed_as=LanguageCode.ENGLISH,
    )
    result = engine.process_turn(
        session,
        text="हमारा बजट पचास हज़ार है",
        language=LanguageCode.ENGLISH,
        transcribed_as=LanguageCode.ENGLISH,
    )
    assert result.language is LanguageCode.HINDI
    assert result.language_switched


def test_a_switch_does_not_cost_the_conversation_its_place() -> None:
    """Language is orthogonal to qualification.

    The failure worth guarding is a switch that resets the planner: the buyer would be
    asked again for what they had already given, in a new language, which reads as not
    having listened at the exact moment the agent appeared to start listening.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run an online toy store", language=LanguageCode.ENGLISH)
    engine.process_turn(session, text="हमें कैटलॉग और ऑनलाइन पेमेंट चाहिए", language=LanguageCode.ENGLISH)
    switched = engine.process_turn(
        session, text="हमारा बजट 150000 रुपये है", language=LanguageCode.ENGLISH
    )
    assert switched.language_switched

    known = {fact.key for fact in engine.snapshot(session).facts}
    assert "business_type" in known
    assert "budget_stated" in known


def test_the_very_first_turn_counts_as_evidence_like_any_other() -> None:
    """The opening language is a default, not a turn the buyer has to spend.

    Seeding the session used to consume the first turn: a buyer speaking Hindi from the
    start needed *three* turns to be heard rather than two, because turn one was swallowed
    recording what the caller had already said. Found by asking what happens when someone
    simply opens in another language, which is the ordinary way a call in the wrong
    language begins.
    """

    engine, session = _session()
    first = engine.process_turn(session, text="हमारी दुकान कपड़ों की है", language=LanguageCode.ENGLISH)
    assert not first.language_switched

    second = engine.process_turn(
        session, text="हमें ऑनलाइन ऑर्डर चाहिए", language=LanguageCode.ENGLISH
    )
    assert second.language is LanguageCode.HINDI
    assert second.language_switched


def test_a_request_made_as_the_opening_turn_is_obeyed() -> None:
    """Asking straight after the greeting is the most natural moment to ask.

    While seeding returned early this was ignored outright - the one turn where a request
    is most likely was the one turn that could not be heard.
    """

    engine, session = _session()
    result = engine.process_turn(
        session, text="please speak in hindi", language=LanguageCode.ENGLISH
    )
    assert result.language is LanguageCode.HINDI
    assert result.language_switched


def test_a_switch_is_acknowledged_in_the_language_switched_to() -> None:
    """Changing language without saying so reads as a glitch.

    For a buyer who actually asked, this sentence is the entire answer to their request,
    so it comes first rather than being appended after a qualifying question.
    """

    from pitchbot.conversation.planning import _PHRASES  # noqa: PLC2701

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    result = engine.process_turn(
        session, text="please speak in hindi", language=LanguageCode.ENGLISH
    )
    assert result.language_switched
    assert result.reply.startswith(_PHRASES[LanguageCode.HINDI].switched)


def test_the_turn_that_ends_the_conversation_is_answered_in_the_new_language() -> None:
    """An opt-out is the worst possible turn to answer in the wrong language.

    The language is resolved before the safety branches for exactly this: a buyer asking
    in Hindi never to be contacted again must be told in Hindi that they will not be.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    result = engine.process_turn(
        session,
        text="हिंदी में बात करें, मुझे दोबारा कॉल मत कीजिए।",
        language=LanguageCode.ENGLISH,
    )
    assert result.language is LanguageCode.HINDI
    assert result.reply == engine._reply(LanguageCode.HINDI, "opt_out")  # noqa: SLF001


def test_a_caller_that_re_declares_the_language_is_obeyed_at_once() -> None:
    """An operator reassigning a live call is giving an instruction, not evidence."""

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    result = engine.process_turn(session, text="tell me more", language=LanguageCode.TELUGU)
    assert result.language is LanguageCode.TELUGU
    assert result.language_switched


def test_a_re_declaration_clears_a_partial_switch() -> None:
    """A vote cast before a reassignment must not land after it."""

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    engine.process_turn(session, text="हमारी दुकान है", language=LanguageCode.ENGLISH)
    engine.process_turn(session, text="tell me more", language=LanguageCode.TELUGU)
    result = engine.process_turn(session, text="ok", language=LanguageCode.TELUGU)
    assert result.language is LanguageCode.TELUGU
    assert not result.language_switched


def test_switching_can_be_turned_off_for_a_caller_that_owns_the_language() -> None:
    engine, session = _session(detect_language_switch=False)
    results = [
        engine.process_turn(session, text="हमारी दुकान है", language=LanguageCode.ENGLISH)
        for _ in range(4)
    ]
    assert all(result.language is LanguageCode.ENGLISH for result in results)
    assert not any(result.language_switched for result in results)


def test_a_conversation_that_repeats_its_declaration_still_behaves_as_before() -> None:
    """The existing contract is unchanged until a buyer actually switches.

    Callers that never read ``result.language`` back - the HTTP API today - keep getting
    exactly what they asked for, which is what makes this safe to enable by default.
    """

    engine, session = _session()
    for text in ("We run a shop", "We need online orders", "Our budget is 50000 rupees"):
        result = engine.process_turn(session, text=text, language=LanguageCode.ENGLISH)
        assert result.language is LanguageCode.ENGLISH
        assert not result.language_switched


# --------------------------------------------------------------------------------------
# Surviving a restart
# --------------------------------------------------------------------------------------


def test_a_checkpoint_carries_the_language_and_its_pending_vote() -> None:
    """Hysteresis lives in state so a restore does not make the buyer start again.

    A partial switch held outside the checkpoint would silently reset, and a buyer one
    turn from being understood would have to convince the system a second time.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    engine.process_turn(session, text="हमारी दुकान है", language=LanguageCode.ENGLISH)

    checkpoint = engine.export_checkpoint(session)
    assert checkpoint.checkpoint_schema_version == "2"
    assert checkpoint.language is LanguageCode.ENGLISH
    assert checkpoint.pending_language is LanguageCode.HINDI
    assert checkpoint.pending_language_count == DEFAULT_SWITCH_AFTER - 1

    restored = ConversationEngine(turn_digest_key=DIGEST_KEY)
    resumed = uuid4()
    restored.restore_checkpoint(resumed, checkpoint)
    result = restored.process_turn(
        resumed, text="हमारा बजट पचास हज़ार है", language=LanguageCode.ENGLISH
    )
    assert result.language is LanguageCode.HINDI
    assert result.language_switched


def test_a_checkpoint_written_before_switching_existed_still_restores() -> None:
    """Version ``"1"`` meant one language for the whole conversation, so defaults are right."""

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    checkpoint = engine.export_checkpoint(session)
    legacy = checkpoint.model_copy(
        update={
            "checkpoint_schema_version": "1",
            "language": LanguageCode.UNKNOWN,
            "declared_language": LanguageCode.UNKNOWN,
            "pending_language": None,
            "pending_language_count": 0,
        }
    )

    restored = ConversationEngine(turn_digest_key=DIGEST_KEY)
    resumed = uuid4()
    restored.restore_checkpoint(resumed, legacy)
    result = restored.process_turn(resumed, text="tell me more", language=LanguageCode.ENGLISH)
    assert result.language is LanguageCode.ENGLISH
    assert not result.language_switched


def test_a_pending_language_without_a_count_is_refused() -> None:
    """Both halves are needed or the hysteresis restores into a state it cannot reach.

    A candidate with no count switches on the next turn regardless of what is said; a
    count with no candidate can never complete. Both are silent, so both are rejected.
    """

    engine, session = _session()
    engine.process_turn(session, text="We run a shop", language=LanguageCode.ENGLISH)
    payload = engine.export_checkpoint(session).model_dump()

    with pytest.raises(ValueError, match="pending language"):
        ConversationStateCheckpoint.model_validate(
            payload | {"pending_language": LanguageCode.HINDI.value}
        )
    with pytest.raises(ValueError, match="pending language"):
        ConversationStateCheckpoint.model_validate(payload | {"pending_language_count": 1})
