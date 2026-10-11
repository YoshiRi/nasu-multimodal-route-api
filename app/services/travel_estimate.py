"""Cheap travel-time estimates, used to compare many routes quickly.

Both choosing a visit order and suggesting which spots to visit at all
need to evaluate far more legs than building one itinerary does — hundreds
rather than a handful. Routing every walk through OpenRouteService would
blow the shared rate limit long before the search finished, so this module
estimates walks from straight-line distance and leaves the real routing to
app/services/route_planner.py, which only runs on the chosen itinerary.

Stop selection deliberately mirrors route_planner._build_leg (first
workable pair of stops wins) so that what gets compared here resembles
what will actually be built.
"""

from __future__ import annotations

from math import ceil

from gtfs_multifeed import GTFSEngine, haversine_distance_m, parse_gtfs_time_to_minutes

from app.services.spot_repository import Spot

NEAREST_STOP_RADIUS_M = 1500

# Estimating searches a narrower set of stops than the real planner, which
# tries MAX_STOP_CANDIDATES. A pair wrongly judged unreachable here only
# produces a worse comparison, never a worse itinerary — the winner is
# still built with the full set.
ESTIMATE_STOP_CANDIDATES = 3

WALKING_SPEED_M_PER_MIN = 80.0
# Straight-line distance understates street distance.
WALKING_DETOUR_FACTOR = 1.3

# Leg cache key: (origin spot, destination spot, departure minute).
LegCache = dict[tuple[str, str, int], "int | None"]


def estimated_walk_minutes(distance_m: float) -> int:
    return max(1, ceil(distance_m * WALKING_DETOUR_FACTOR / WALKING_SPEED_M_PER_MIN))


def nearest_stop_candidates(
    spot: Spot, engine: GTFSEngine, limit: int = ESTIMATE_STOP_CANDIDATES
) -> list[dict[str, str | float]]:
    nearby = engine.nearby_stops(lat=spot.lat, lng=spot.lng, radius_m=NEAREST_STOP_RADIUS_M)
    return nearby[:limit]


def estimate_leg_arrival(
    origin: Spot,
    destination: Spot,
    depart_minutes: int,
    engine: GTFSEngine,
    departure_date: str,
    transfer_buffer_minutes: int = 0,
    max_transfers: int = 1,
) -> int | None:
    """Estimate when a leg would land, or None if there is no way through."""

    origin_candidates = nearest_stop_candidates(origin, engine)
    destination_candidates = nearest_stop_candidates(destination, engine)
    direct_distance_m = haversine_distance_m(
        origin.lat, origin.lng, destination.lat, destination.lng
    )

    if (
        not origin_candidates
        or not destination_candidates
        or origin_candidates[0]["stop_id"] == destination_candidates[0]["stop_id"]
    ):
        return depart_minutes + estimated_walk_minutes(direct_distance_m)

    for origin_stop in origin_candidates:
        board_minutes = depart_minutes + estimated_walk_minutes(
            haversine_distance_m(
                origin.lat, origin.lng, origin_stop["stop_lat"], origin_stop["stop_lon"]
            )
        )
        for destination_stop in destination_candidates:
            if origin_stop["stop_id"] == destination_stop["stop_id"]:
                continue
            journeys = engine.search_journeys(
                origin_stop_id=origin_stop["stop_id"],
                dest_stop_id=destination_stop["stop_id"],
                departure_time=format_minutes(board_minutes + transfer_buffer_minutes),
                departure_date=departure_date,
                max_transfers=max_transfers,
            )
            if not journeys:
                continue
            return parse_gtfs_time_to_minutes(journeys[0]["arrival_time"]) + (
                estimated_walk_minutes(
                    haversine_distance_m(
                        destination_stop["stop_lat"],
                        destination_stop["stop_lon"],
                        destination.lat,
                        destination.lng,
                    )
                )
            )

    return None


def cached_leg_arrival(
    origin: Spot,
    destination: Spot,
    depart_minutes: int,
    engine: GTFSEngine,
    departure_date: str,
    cache: LegCache,
    transfer_buffer_minutes: int = 0,
    max_transfers: int = 1,
) -> int | None:
    key = (origin.spot_id, destination.spot_id, depart_minutes)
    if key not in cache:
        cache[key] = estimate_leg_arrival(
            origin,
            destination,
            depart_minutes,
            engine,
            departure_date,
            transfer_buffer_minutes,
            max_transfers,
        )
    return cache[key]


def format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"
