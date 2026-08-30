import pytest
from httpx import ASGITransport, AsyncClient

from pitchbot.main import app


@pytest.mark.asyncio
async def test_health_defaults_side_effects_off() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["telephony_enabled"] is False
    assert payload["whatsapp_enabled"] is False
    assert payload["external_network_enabled"] is False
