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

    assert "Simulation only" in html
    assert 'meta name="referrer" content="no-referrer"' in html
    assert "innerHTML" not in javascript
    assert "textContent" in javascript
    assert "MAX_QUEUE_ITEMS = 24" in transport
    assert "MAX_CHUNK_BYTES = 256 * 1024" in transport
    assert "MediaRecorder.isTypeSupported" in transport
    assert "http://" not in javascript + transport
    assert "https://" not in javascript + transport


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
