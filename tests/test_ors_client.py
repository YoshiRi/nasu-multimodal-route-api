from __future__ import annotations

import httpx
import pytest

from app.services.ors_client import OrsRequestError, get_walking_route

VALID_RESPONSE = {
    "features": [
        {"properties": {"summary": {"duration": 300.0, "distance": 400.0}}},
    ]
}


@pytest.mark.anyio
async def test_get_walking_route_parses_duration_and_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "test-key"
        return httpx.Response(200, json=VALID_RESPONSE)

    route = await get_walking_route(
        37.0, 140.0, 37.1, 140.1, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    assert route.duration_min == 5
    assert route.distance_m == 400.0


@pytest.mark.anyio
async def test_get_walking_route_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORS_API_KEY", raising=False)

    with pytest.raises(OrsRequestError, match="ORS_API_KEY"):
        await get_walking_route(37.0, 140.0, 37.1, 140.1)


@pytest.mark.anyio
async def test_get_walking_route_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(OrsRequestError):
        await get_walking_route(
            37.0, 140.0, 37.1, 140.1, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )


@pytest.mark.anyio
async def test_get_walking_route_raises_on_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(OrsRequestError, match="Unexpected ORS response shape"):
        await get_walking_route(
            37.0, 140.0, 37.1, 140.1, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
