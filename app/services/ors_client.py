"""OpenRouteService (ORS) client for walking directions.

Response shape here follows ORS's documented GeoJSON directions contract
(features[0].properties.summary.{duration,distance}). Verified against a
live ORS_API_KEY on 2026-08-22.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

ORS_WALKING_URL = "https://api.openrouteservice.org/v2/directions/foot-walking"
DEFAULT_TIMEOUT_S = 10.0


class OrsRequestError(RuntimeError):
    """Raised when the ORS walking-route request fails or returns an
    unexpected shape."""


@dataclass(frozen=True)
class WalkingRoute:
    duration_min: int
    distance_m: float


async def get_walking_route(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    *,
    client: httpx.AsyncClient | None = None,
) -> WalkingRoute:
    """Return walking duration (minutes) and distance (meters) between two points."""

    api_key = os.environ.get("ORS_API_KEY")
    if not api_key:
        raise OrsRequestError("ORS_API_KEY is not set")

    params = {
        "api_key": api_key,
        "start": f"{lng1},{lat1}",
        "end": f"{lng2},{lat2}",
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S)
    try:
        response = await http_client.get(ORS_WALKING_URL, params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise OrsRequestError(f"ORS request failed: {exc}") from exc
    finally:
        if owns_client:
            await http_client.aclose()

    try:
        summary = data["features"][0]["properties"]["summary"]
        duration_s = float(summary["duration"])
        distance_m = float(summary["distance"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OrsRequestError(f"Unexpected ORS response shape: {data!r}") from exc

    return WalkingRoute(duration_min=round(duration_s / 60), distance_m=round(distance_m, 1))
