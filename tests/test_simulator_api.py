from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from pitchbot.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest.mark.asyncio
async def test_static_simulator_is_same_origin_and_hardened(client: AsyncClient) -> None:
    response = await client.get("/simulator/")

    assert response.status_code == 200
    assert "PitchBot Browser Simulator" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_session_turn_history_and_interrupt_flow(client: AsyncClient) -> None:
    created = await client.post(
        "/api/simulator/sessions",
        json={"lead_ref": "api-synthetic", "language": "en"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert created.json()["events"][0]["event_type"] == "disclosure"

    turn = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json={
            "text": "Show a preview",
            "language": "mixed",
            "preview_action": "callback-preview",
            "simulated_latency_ms": 0,
            "inject_failure": False,
        },
    )
    assert turn.status_code == 200
    assert turn.json()["preview"]["action"] == "callback-preview"

    interrupted = await client.post(f"/api/simulator/sessions/{session_id}/interrupt")
    assert interrupted.status_code == 200
    assert interrupted.json()["events"][-1]["event_type"] == "interruption"

    history = await client.get(f"/api/simulator/sessions/{session_id}/history")
    assert history.status_code == 200
    assert len(history.json()["events"]) == 5

    closed = await client.delete(f"/api/simulator/sessions/{session_id}")
    assert closed.status_code == 204
    assert (await client.get(f"/api/simulator/sessions/{session_id}")).status_code == 404


@pytest.mark.asyncio
async def test_api_validation_and_injected_failure_are_explicit(client: AsyncClient) -> None:
    invalid = await client.post(
        "/api/simulator/sessions",
        json={"lead_ref": "not valid with spaces", "language": "en"},
    )
    assert invalid.status_code == 422

    created = await client.post(
        "/api/simulator/sessions",
        json={"lead_ref": "failure-case", "language": "en"},
    )
    session_id = created.json()["session_id"]
    failed = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json={
            "text": "inject",
            "language": "en",
            "inject_failure": True,
        },
    )
    assert failed.status_code == 503
    assert "failure injected" in failed.json()["detail"]


@pytest.mark.asyncio
async def test_replay_and_missing_resources(client: AsyncClient) -> None:
    replay = await client.get("/api/simulator/replay/english-discovery")
    missing = await client.get("/api/simulator/replay/unknown")
    missing_session = await client.get(
        "/api/simulator/sessions/00000000-0000-0000-0000-000000000000"
    )

    assert replay.status_code == 200
    assert len(replay.json()["turns"]) == 2
    assert missing.status_code == 404
    assert missing_session.status_code == 404
