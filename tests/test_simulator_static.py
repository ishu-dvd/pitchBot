from pathlib import Path
from typing import Protocol, cast

from pitchbot.main import app
from pitchbot.simulator.router import is_allowed_websocket_origin, router

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
