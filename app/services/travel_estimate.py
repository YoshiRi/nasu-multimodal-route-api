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

# Rough road speed for sizing a taxi leg, used to answer "would this trip
# work if we took a taxi for one hop" without routing it. Deliberately
# conservative for mountain roads.
TAXI_SPEED_M_PER_MIN = 500.0
TAXI_DETOUR_FACTOR = 1.3
# Waiting for, and getting into, the taxi.
TAXI_OVERHEAD_MINUTES = 10

# Above this straight-line distance, a spot-to-spot hop with no usable bus
# stop at either end is a taxi ride, not a walk. route_planner._direct_step
# builds exactly that, so estimating it as a walk would have the estimate
# describe a journey nobody would take — a 21km hop came back as a
# five-and-a-half-hour walk, which then looked merely slow rather than
# impossible, and no taxi was ever considered for it.
MAX_DIRECT_WALKING_DISTANCE_M = 3000

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
        if direct_distance_m > MAX_DIRECT_WALKING_DISTANCE_M:
            return depart_minutes + estimated_taxi_minutes(origin, destination)
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


def needs_taxi_regardless(origin: Spot, destination: Spot, engine: GTFSEngine) -> bool:
    """True when this hop has no bus option at all and is too far to walk.

    Geography, not choice: the planner will build a taxi leg here whatever
    the caller's taxi budget says, so it is not counted against it.
    """

    if (
        nearest_stop_candidates(origin, engine)
        and nearest_stop_candidates(destination, engine)
        and nearest_stop_candidates(origin, engine)[0]["stop_id"]
        != nearest_stop_candidates(destination, engine)[0]["stop_id"]
    ):
        return False
    return (
        haversine_distance_m(origin.lat, origin.lng, destination.lat, destination.lng)
        > MAX_DIRECT_WALKING_DISTANCE_M
    )


def taxi_distance_m(origin: Spot, destination: Spot) -> float:
    """Road distance for a taxi hop, approximated from the straight line."""

    return (
        haversine_distance_m(origin.lat, origin.lng, destination.lat, destination.lng)
        * TAXI_DETOUR_FACTOR
    )


def estimated_taxi_minutes(origin: Spot, destination: Spot) -> int:
    """Door-to-door minutes for a taxi hop, including getting hold of one."""

    return TAXI_OVERHEAD_MINUTES + max(
        1, ceil(taxi_distance_m(origin, destination) / TAXI_SPEED_M_PER_MIN)
    )


def format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"
