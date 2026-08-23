from __future__ import annotations

import httpx
import pytest

from app.services.ors_client import (
    OrsRateLimitError,
    OrsRequestError,
    get_driving_route,
    get_walking_route,
)
from app.services.rate_limiter import SlidingWindowRateLimiter

VALID_RESPONSE = {
    "features": [
        {"properties": {"summary": {"duration": 300.0, "distance": 400.0}}},
    ]
}


@pytest.fixture
def permissive_rate_limiter() -> SlidingWindowRateLimiter:
    # A fresh, generous limiter per test so functional tests never
    # collide with the module-level shared budget or each other.
    return SlidingWindowRateLimiter(max_calls=1000, period_seconds=60)


@pytest.mark.anyio
async def test_get_walking_route_parses_duration_and_distance(
    monkeypatch: pytest.MonkeyPatch, permissive_rate_limiter: SlidingWindowRateLimiter
) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "test-key"
        assert "foot-walking" in str(request.url)
        return httpx.Response(200, json=VALID_RESPONSE)

    route = await get_walking_route(
        37.0,
        140.0,
        37.1,
        140.1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rate_limiter=permissive_rate_limiter,
    )

    assert route.duration_min == 5
    assert route.distance_m == 400.0


@pytest.mark.anyio
async def test_get_driving_route_parses_duration_and_distance(
    monkeypatch: pytest.MonkeyPatch, permissive_rate_limiter: SlidingWindowRateLimiter
) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "driving-car" in str(request.url)
        return httpx.Response(200, json=VALID_RESPONSE)

    route = await get_driving_route(
        37.0,
        140.0,
        37.1,
        140.1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rate_limiter=permissive_rate_limiter,
    )

    assert route.duration_min == 5
    assert route.distance_m == 400.0


@pytest.mark.anyio
async def test_get_walking_route_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORS_API_KEY", raising=False)

    with pytest.raises(OrsRequestError, match="ORS_API_KEY"):
        await get_walking_route(37.0, 140.0, 37.1, 140.1)


@pytest.mark.anyio
async def test_get_walking_route_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch, permissive_rate_limiter: SlidingWindowRateLimiter
) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(OrsRequestError):
        await get_walking_route(
            37.0,
            140.0,
            37.1,
            140.1,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            rate_limiter=permissive_rate_limiter,
        )


@pytest.mark.anyio
async def test_get_walking_route_raises_on_unexpected_shape(
    monkeypatch: pytest.MonkeyPatch, permissive_rate_limiter: SlidingWindowRateLimiter
) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(OrsRequestError, match="Unexpected ORS response shape"):
        await get_walking_route(
            37.0,
            140.0,
            37.1,
            140.1,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            rate_limiter=permissive_rate_limiter,
        )


@pytest.mark.anyio
async def test_get_walking_route_raises_ors_rate_limit_error_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    strict_limiter = SlidingWindowRateLimiter(max_calls=1, period_seconds=60)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=VALID_RESPONSE)

    transport = httpx.MockTransport(handler)
    await get_walking_route(
        37.0, 140.0, 37.1, 140.1, client=httpx.AsyncClient(transport=transport), rate_limiter=strict_limiter
    )

    with pytest.raises(OrsRateLimitError):
        await get_walking_route(
            37.0,
            140.0,
            37.1,
            140.1,
            client=httpx.AsyncClient(transport=transport),
            rate_limiter=strict_limiter,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
