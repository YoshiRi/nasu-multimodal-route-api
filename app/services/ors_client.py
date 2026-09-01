"""OpenRouteService (ORS) client for walking and driving directions.

Response shape here follows ORS's documented GeoJSON directions contract
(features[0].properties.summary.{duration,distance}). Verified against a
live ORS_API_KEY on 2026-08-22 (foot-walking profile).

Outbound calls go through a shared in-process rate limiter to stay under
ORS's free-tier quota (commonly 40 requests/minute) regardless of which
endpoint or internal caller triggers them — see app/services/rate_limiter.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from app.services.rate_limiter import RateLimitExceededError, SlidingWindowRateLimiter

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/{profile}"
DEFAULT_TIMEOUT_S = 10.0

# Conservative default under ORS's free-tier limit (commonly 40/min);
# override by constructing a different SlidingWindowRateLimiter if needed.
ORS_MAX_REQUESTS_PER_MINUTE = 30

_rate_limiter = SlidingWindowRateLimiter(max_calls=ORS_MAX_REQUESTS_PER_MINUTE, period_seconds=60)


class OrsRequestError(RuntimeError):
    """Raised when an ORS request fails or returns an unexpected shape."""


class OrsRateLimitError(OrsRequestError):
    """Raised when the local ORS call budget (not ORS's own) is exceeded."""


@dataclass(frozen=True)
class RouteEstimate:
    duration_min: int
    distance_m: float
    # Ordered [lat, lng] points of the actual route geometry, for map
    # display. Falls back to [[start], [end]] if ORS returned no geometry.
    path: list[list[float]] = field(default_factory=list)


async def get_walking_route(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    *,
    client: httpx.AsyncClient | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> RouteEstimate:
    """Return walking duration (minutes) and distance (meters) between two points."""

    return await _get_route(
        "foot-walking", lat1, lng1, lat2, lng2, client=client, rate_limiter=rate_limiter
    )


async def get_driving_route(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    *,
    client: httpx.AsyncClient | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> RouteEstimate:
    """Return driving duration (minutes) and distance (meters) between two points."""

    return await _get_route(
        "driving-car", lat1, lng1, lat2, lng2, client=client, rate_limiter=rate_limiter
    )


async def _get_route(
    profile: str,
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    *,
    client: httpx.AsyncClient | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> RouteEstimate:
    api_key = os.environ.get("ORS_API_KEY")
    if not api_key:
        raise OrsRequestError("ORS_API_KEY is not set")

    try:
        (rate_limiter or _rate_limiter).acquire()
    except RateLimitExceededError as exc:
        raise OrsRateLimitError(str(exc)) from exc

    params = {
        "api_key": api_key,
        "start": f"{lng1},{lat1}",
        "end": f"{lng2},{lat2}",
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S)
    try:
        response = await http_client.get(ORS_DIRECTIONS_URL.format(profile=profile), params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise OrsRequestError(f"ORS request failed: {exc}") from exc
    finally:
        if owns_client:
            await http_client.aclose()

    try:
        feature = data["features"][0]
        summary = feature["properties"]["summary"]
        duration_s = float(summary["duration"])
        distance_m = float(summary["distance"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OrsRequestError(f"Unexpected ORS response shape: {data!r}") from exc

    try:
        coordinates = feature["geometry"]["coordinates"]
        path = [[float(lat), float(lng)] for lng, lat in coordinates]
        if len(path) < 2:
            raise ValueError("geometry has fewer than 2 points")
    except (KeyError, IndexError, TypeError, ValueError):
        path = [[lat1, lng1], [lat2, lng2]]

    return RouteEstimate(
        duration_min=round(duration_s / 60), distance_m=round(distance_m, 1), path=path
    )
