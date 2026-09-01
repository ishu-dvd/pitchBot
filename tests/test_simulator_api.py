from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

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
        json={
            "lead_ref": "api-synthetic",
            "language": "en",
            "preview_consent_granted": True,
            "contact_policy": {
                "outreach_allowed": True,
                "allowlisted": True,
                "dnd_check_passed": True,
                "calling_hours_check_passed": True,
                "opted_out": False,
            },
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert created.json()["events"][0]["event_type"] == "disclosure"

    turn_body = {
        "operation_id": str(uuid4()),
        "text": "Please schedule a callback preview",
        "language": "mixed",
        "preview_action": "callback-preview",
        "simulated_latency_ms": 0,
        "inject_failure": False,
    }
    turn = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json=turn_body,
    )
    retry = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json=turn_body,
    )
    assert turn.status_code == 200
    assert retry.json() == turn.json()
    assert turn.json()["preview"]["decision"]["status"] == "approved"
    assert turn.json()["preview"]["callback"]["status"] == "scheduled"
    conflict = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json={**turn_body, "text": "Different input with the same operation ID"},
    )
    assert conflict.status_code == 409

    interrupted = await client.post(f"/api/simulator/sessions/{session_id}/interrupt")
    assert interrupted.status_code == 200
    assert interrupted.json()["events"][-1]["event_type"] == "interruption"

    history = await client.get(f"/api/simulator/sessions/{session_id}/history")
    assert history.status_code == 200
    assert len(history.json()["events"]) == 6

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
    missing_operation = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json={"text": "missing operation", "language": "en"},
    )
    assert missing_operation.status_code == 422
    failed = await client.post(
        f"/api/simulator/sessions/{session_id}/turns",
        json={
            "operation_id": str(uuid4()),
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


@pytest.mark.asyncio
async def test_durable_history_routes_are_bounded_and_explicitly_disabled(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/simulator/sessions",
        json={"lead_ref": "durable-disabled", "language": "en"},
    )
    session_id = created.json()["session_id"]

    history = await client.get(f"/api/simulator/sessions/{session_id}/durable-history")
    invalid_limit = await client.get(
        f"/api/simulator/sessions/{session_id}/durable-history?limit=101"
    )
    resume = await client.post(
        f"/api/simulator/sessions/{session_id}/resume",
        json={"lead_ref": "durable-disabled"},
    )

    assert history.status_code == 409
    assert "disabled" in history.json()["detail"]
    assert invalid_limit.status_code == 422
    assert resume.status_code == 409


@pytest.mark.asyncio
async def test_only_capacity_exhaustion_maps_to_too_many_requests(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitchbot.simulator import router as simulator_router
    from pitchbot.simulator.service import SessionCapacityError

    class _Failing:
        def __init__(self, error: BaseException) -> None:
            self._error = error

        def create_session(self, request: object) -> object:
            raise self._error

    monkeypatch.setattr(
        simulator_router,
        "simulator_service",
        _Failing(SessionCapacityError("Simulator session capacity reached")),
    )
    response = await client.post("/api/simulator/sessions", json={"lead_ref": "cap"})
    assert response.status_code == 429

    monkeypatch.setattr(
        simulator_router,
        "simulator_service",
        _Failing(RuntimeError("adapter exploded")),
    )
    with pytest.raises(RuntimeError, match="adapter exploded"):
        await client.post("/api/simulator/sessions", json={"lead_ref": "cap"})
