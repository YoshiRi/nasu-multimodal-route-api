import pytest
from fastapi.testclient import TestClient

import app.api.non_transit as non_transit
from app.main import app
from app.services.ors_client import OrsRateLimitError, OrsRequestError, RouteEstimate

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_routes(monkeypatch: pytest.MonkeyPatch):
    async def fake_walking(lat1, lng1, lat2, lng2, **kwargs):
        return RouteEstimate(duration_min=58, distance_m=4815.0)

    async def fake_driving(lat1, lng1, lat2, lng2, **kwargs):
        return RouteEstimate(duration_min=12, distance_m=6000.0)

    monkeypatch.setattr(non_transit, "get_walking_route", fake_walking)
    monkeypatch.setattr(non_transit, "get_driving_route", fake_driving)


def _params(mode: str) -> dict[str, object]:
    return {
        "origin_lat": 37.0950,
        "origin_lng": 140.0010,
        "dest_lat": 37.1240,
        "dest_lng": 139.9850,
        "mode": mode,
    }


def test_non_transit_route_walking_has_null_cost() -> None:
    response = client.get("/non-transit/route", params=_params("walking"))

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "walking"
    assert body["duration_min"] == 58
    assert body["distance_m"] == 4815.0
    assert body["cost_jpy"] is None


def test_non_transit_route_taxi_has_estimated_cost() -> None:
    response = client.get("/non-transit/route", params=_params("taxi"))

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "taxi"
    assert body["duration_min"] == 12
    assert body["distance_m"] == 6000.0
    assert body["cost_jpy"] == 500 + 5 * 400  # BASE_FARE_JPY + (6km - 1km) * RATE_PER_KM_JPY


def test_non_transit_route_rejects_invalid_mode() -> None:
    response = client.get("/non-transit/route", params=_params("driving"))

    assert response.status_code == 422


def test_non_transit_route_returns_429_when_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(lat1, lng1, lat2, lng2, **kwargs):
        raise OrsRateLimitError("too many calls")

    monkeypatch.setattr(non_transit, "get_walking_route", failing)

    response = client.get("/non-transit/route", params=_params("walking"))

    assert response.status_code == 429


def test_non_transit_route_returns_503_on_ors_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(lat1, lng1, lat2, lng2, **kwargs):
        raise OrsRequestError("ors is down")

    monkeypatch.setattr(non_transit, "get_walking_route", failing)

    response = client.get("/non-transit/route", params=_params("walking"))

    assert response.status_code == 503
