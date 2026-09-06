import re
from pathlib import Path
from typing import Protocol, cast

from pitchbot.main import app
from pitchbot.simulator.router import is_allowed_websocket_origin, router
from pitchbot.speech.pipeline import UtteranceOutcome

WEB = Path("apps/web")


class RouteWithPath(Protocol):
    path: str


def test_simulator_static_assets_exist_and_use_safe_rendering() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    transport = (WEB / "audio-transport.js").read_text(encoding="utf-8")
    worklet = (WEB / "pcm-worklet.js").read_text(encoding="utf-8")

    assert "Simulation only" in html
    assert 'meta name="referrer" content="no-referrer"' in html
    assert "innerHTML" not in javascript
    assert "textContent" in javascript
    assert "MAX_QUEUE_ITEMS = 24" in transport
    assert "http://" not in javascript + transport
    assert "https://" not in javascript + transport

    # The browser must capture raw PCM, not `MediaRecorder`'s WebM/Opus. Nothing in the
    # server decodes Opus and the detector accepts only 320/640/960-byte PCM frames, so a
    # recorder here means every frame is rejected and the buyer is never heard.
    assert "MediaRecorder" not in transport
    assert "audioWorklet.addModule" in transport
    assert "TARGET_SAMPLE_RATE_HZ = 16_000" in transport
    assert "FRAME_SAMPLES = 480" in transport
    # A browser that will not resample must fail loudly rather than send frames whose byte
    # count misrepresents their duration - the endpointer times an utterance by summing them.
    assert "context.sampleRate !== TARGET_SAMPLE_RATE_HZ" in transport
    assert 'registerProcessor("pcm-frame-splitter"' in worklet


def test_the_capture_worklet_is_reachable_from_the_page() -> None:
    """`addModule` fetches by URL, so a worklet that is not served is a runtime failure."""

    transport = (WEB / "audio-transport.js").read_text(encoding="utf-8")

    assert 'WORKLET_URL = "/simulator/pcm-worklet.js"' in transport
    assert (WEB / "pcm-worklet.js").is_file()


def test_simulator_static_and_websocket_routes_are_registered() -> None:
    app_paths = {cast(RouteWithPath, route).path for route in app.routes if hasattr(route, "path")}
    simulator_paths = {
        cast(RouteWithPath, route).path for route in router.routes if hasattr(route, "path")
    }

    assert "/simulator" in app_paths
    assert "/api/simulator/sessions" in simulator_paths
    assert "/api/simulator/sessions/{session_id}/turns" in simulator_paths
    assert "/api/simulator/sessions/{session_id}/audio" in simulator_paths
    assert "/api/simulator/sessions/{session_id}/history" in simulator_paths


def test_websocket_origin_must_exactly_match_host() -> None:
    assert is_allowed_websocket_origin("http://localhost:8000", "localhost:8000")
    assert is_allowed_websocket_origin("https://demo.example", "demo.example")
    assert not is_allowed_websocket_origin("https://evil.example", "demo.example")
    assert not is_allowed_websocket_origin(None, "demo.example")
    assert not is_allowed_websocket_origin("https://demo.example", None)


def test_the_client_plays_a_filler_without_handing_the_floor_back() -> None:
    """A backchannel never took the floor, so it must never report playing one.

    Reporting would release the floor the *reply* is about to hold, and the buyer talking
    over the answer would stop counting as an interruption for that turn.
    """

    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    player = (WEB / "reply-audio.js").read_text(encoding="utf-8")

    assert "report: !payload.filler" in javascript
    assert "end({ report = true } = {})" in player
    assert "if (report) this.onFinished();" in player


def test_a_reply_queues_behind_a_finished_filler_rather_than_cutting_it_off() -> None:
    """`begin` used to stop everything, which would clip a filler mid-syllable.

    A clipped syllable sounds like a fault where a completed one sounds like a person.
    Capped, so a stuck stream delays the answer by a beat and never by a minute.
    """

    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    player = (WEB / "reply-audio.js").read_text(encoding="utf-8")

    assert "after: !payload.filler" in javascript
    assert "MAX_CARRY_OVER_SECONDS = 2" in player
    assert "queued <= MAX_CARRY_OVER_SECONDS" in player


def test_every_utterance_outcome_the_browser_can_receive_has_a_label() -> None:
    """The label map is Python enum values written out by hand in JavaScript, so it drifts.

    It had already drifted before anyone noticed: `language-unsupported` was added to
    `UtteranceOutcome` in an earlier change and never labelled, so the browser rendered the
    raw identifier at the buyer. Adding `transcription-timed-out` would have done it again.

    There is no import that could have caught this - one side is Python, the other is a
    JavaScript object literal - so the check has to be a test, and it has to read the real
    file rather than a copy of the list.
    """

    javascript = (WEB / "app.js").read_text(encoding="utf-8")
    block = javascript.split("const OUTCOME_LABELS = {", 1)[1].split("};", 1)[0]
    labelled = set(re.findall(r'"([a-z-]+)":', block))

    # `transcribed` never reaches the label path - it is the branch that renders a reply.
    expected = {outcome.value for outcome in UtteranceOutcome} - {UtteranceOutcome.TRANSCRIBED}
    assert expected <= labelled, f"unlabelled outcomes: {sorted(expected - labelled)}"
    assert labelled <= expected, (
        f"labels for outcomes that do not exist: {sorted(labelled - expected)}"
    )
