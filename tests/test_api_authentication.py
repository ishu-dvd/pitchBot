"""The API refuses unauthenticated callers, and refuses to run open outside `local`."""

from __future__ import annotations

import pytest
from httpx import Response
from starlette.testclient import TestClient

from pitchbot.config import Settings
from pitchbot.main import app
from pitchbot.security import CredentialStore, RateLimiter, parse_api_keys
from pitchbot.simulator import router as router_module

SECRET = "a-sufficiently-long-secret"
ORIGIN = {"origin": "http://testserver", "host": "testserver"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def enforcing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the router's module-level store at a configured credential.

    `require_credential` reads these globals per call, so replacing them is enough - no
    module reload, and no risk of a half-reloaded app object.
    """

    monkeypatch.setattr(
        router_module, "credentials", CredentialStore(parse_api_keys(f"web:{SECRET}"))
    )
    monkeypatch.setattr(
        router_module, "rate_limiter", RateLimiter(capacity=100, refill_per_second=100.0)
    )


def _create(client: TestClient, headers: dict[str, str] | None = None) -> Response:
    response: Response = client.post(
        "/api/simulator/sessions",
        json={"lead_ref": "lead-1"},
        headers=headers,
    )
    return response


def test_open_when_no_credential_is_configured(client: TestClient) -> None:
    """The local demo keeps working exactly as it did before authentication existed."""

    assert router_module.credentials.enforcing is False
    assert _create(client).status_code == 201


@pytest.mark.usefixtures("enforcing")
def test_request_without_a_key_is_refused(client: TestClient) -> None:
    response = _create(client)
    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]


@pytest.mark.usefixtures("enforcing")
def test_request_with_a_wrong_key_is_refused(client: TestClient) -> None:
    response = _create(client, headers={"X-API-Key": "wrong-but-long-enough"})
    assert response.status_code == 401


@pytest.mark.usefixtures("enforcing")
def test_request_with_the_configured_key_is_admitted(client: TestClient) -> None:
    response = _create(client, headers={"X-API-Key": SECRET})
    assert response.status_code == 201


@pytest.mark.usefixtures("enforcing")
def test_every_route_is_covered_not_just_the_one_that_was_remembered(
    client: TestClient,
) -> None:
    """The dependency is registered on the router, so no endpoint can be forgotten."""

    unauthenticated = [
        client.get("/api/simulator/sessions/00000000-0000-0000-0000-000000000000"),
        client.get("/api/simulator/replay/discovery"),
        client.delete("/api/simulator/sessions/00000000-0000-0000-0000-000000000000"),
    ]
    assert [response.status_code for response in unauthenticated] == [401, 401, 401]


@pytest.mark.usefixtures("enforcing")
def test_rate_limit_refuses_and_advertises_when_to_return(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        router_module, "rate_limiter", RateLimiter(capacity=1, refill_per_second=0.5)
    )
    first = _create(client, headers={"X-API-Key": SECRET})
    second = _create(client, headers={"X-API-Key": SECRET})
    assert first.status_code == 201
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


@pytest.mark.usefixtures("enforcing")
def test_websocket_without_a_key_is_closed(client: TestClient) -> None:
    session = _create(client, headers={"X-API-Key": SECRET}).json()
    with pytest.raises(Exception):  # noqa: B017 - starlette raises on a rejected handshake
        with client.websocket_connect(
            f"/api/simulator/sessions/{session['session_id']}/audio", headers=ORIGIN
        ):
            pass


@pytest.mark.usefixtures("enforcing")
def test_websocket_accepts_the_key_as_a_subprotocol(client: TestClient) -> None:
    """A browser cannot set a header on a WebSocket, so the key travels as a subprotocol."""

    session = _create(client, headers={"X-API-Key": SECRET}).json()
    with client.websocket_connect(
        f"/api/simulator/sessions/{session['session_id']}/audio",
        headers=ORIGIN,
        subprotocols=["pitchbot.v1", f"pitchbot.key.{SECRET}"],
    ) as socket:
        assert socket.receive_json()["type"] == "ready"


def test_non_local_environment_refuses_to_start_without_a_credential() -> None:
    """The failure this whole module exists to prevent: a silently open production server."""

    with pytest.raises(ValueError, match="api_keys must define at least one"):
        Settings(app_env="production", api_keys="")


def test_non_local_environment_starts_with_a_credential() -> None:
    settings = Settings(app_env="production", api_keys=f"web:{SECRET}")
    assert settings.api_keys == f"web:{SECRET}"


def test_local_environment_may_run_open() -> None:
    assert Settings(app_env="local", api_keys="").api_keys == ""
