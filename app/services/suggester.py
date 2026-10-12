"""Suggest which spots to visit, given only a start, an end and a deadline.

/plan/route-plan answers "how do I visit these places"; this answers the
question before it — "which places can I fit in at all". It returns
several candidates so a person (or an agent talking to one) can compare
and pick, after which the chosen one goes through route-plan to get a real
itinerary with real walking routes.

Everything here runs on app/services/travel_estimate.py, so a suggestion
costs no routing-service calls however many combinations it tries. The
times it reports are estimates and will shift by a few minutes once the
chosen candidate is actually built.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from gtfs_multifeed import GTFSEngine, haversine_distance_m, parse_gtfs_time_to_minutes

from app.services.spot_repository import Spot, SpotNotFoundError, SpotRepository
from app.services.travel_estimate import LegCache, cached_leg_arrival, format_minutes

# Without a tag hint every spot is a candidate, which is too many legs to
# evaluate interactively. Keep the ones closest to the straight line from
# start to destination — a detour the whole width of the region is not a
# suggestion anyone wants anyway.
MAX_SUGGESTION_POOL = 8

# Partial itineraries kept at each step of the search.
SUGGESTION_BEAM_WIDTH = 3

# Departure times are rounded down to this many minutes before estimating.
# Different partial itineraries then ask about the same leg at the same
# time, which is what makes the leg cache hit often enough for the search
# to stay quick. Buses here run hourly at best, so the rounding costs
# nothing in practice.
TIME_GRID_MINUTES = 10

# Nothing in this network covers ground faster than roughly this, so a
# straight-line distance divided by it is a floor on the travel time. Used
# only to rule a spot out before paying for a real search — an optimistic
# bound, so it can never discard something that would have fitted.
MAX_PLAUSIBLE_SPEED_M_PER_MIN = 700.0


class SuggestInputError(ValueError):
    """Raised for malformed requests (unknown spot_id, etc.) — maps to HTTP 4xx."""


@dataclass(frozen=True)
class SuggestedStop:
    spot: Spot
    arrival_minutes: int
    departure_minutes: int


@dataclass(frozen=True)
class Candidate:
    stops: tuple[SuggestedStop, ...]
    destination_arrival_minutes: int

    @property
    def spot_ids(self) -> frozenset[str]:
        return frozenset(stop.spot.spot_id for stop in self.stops)

    @property
    def tags(self) -> list[str]:
        seen: dict[str, None] = {}
        for stop in self.stops:
            for tag in stop.spot.tags:
                seen.setdefault(tag, None)
        return list(seen)


@dataclass(frozen=True)
class _Partial:
    stops: tuple[SuggestedStop, ...]
    free_at_minutes: int

    @property
    def position(self) -> Spot:
        return self.stops[-1].spot


def suggest_itineraries(
    spots: SpotRepository,
    engine: GTFSEngine,
    base_location_id: str,
    destination_spot_id: str,
    departure_time: str,
    departure_date: str,
    arrive_by: str,
    spot_count: int = 3,
    tags: list[str] | None = None,
    must_include_spot_ids: list[str] | None = None,
    dwell_minutes: int = 60,
    max_candidates: int = 3,
    max_transfers: int = 1,
) -> list[Candidate]:
    """Return candidate itineraries, soonest arrival at the destination first."""

    try:
        base = spots.get(base_location_id)
        destination = spots.get(destination_spot_id)
        required = [spots.get(spot_id) for spot_id in must_include_spot_ids or []]
    except SpotNotFoundError as exc:
        raise SuggestInputError(f"Unknown spot_id: {exc}") from exc

    depart_minutes = parse_gtfs_time_to_minutes(departure_time)
    deadline_minutes = parse_gtfs_time_to_minutes(arrive_by)
    pool = _build_pool(spots, base, destination, tags, required)
    if not pool:
        return []

    cache: LegCache = {}
    beam = [_Partial(stops=(), free_at_minutes=depart_minutes)]
    finished: list[Candidate] = []

    for _ in range(spot_count):
        beam = _expand(
            beam, pool, base, engine, departure_date, deadline_minutes,
            dwell_minutes, max_transfers, cache, required,
        )
        if not beam:
            break
        finished.extend(
            _close_off(
                beam, base, destination, engine, departure_date, deadline_minutes,
                max_transfers, required, cache,
            )
        )

    return _rank(finished, max_candidates)


def _build_pool(
    spots: SpotRepository,
    base: Spot,
    destination: Spot,
    tags: list[str] | None,
    required: list[Spot],
) -> list[Spot]:
    excluded = {base.spot_id, destination.spot_id}
    pool = [spot for spot in spots.list_all() if spot.spot_id not in excluded]

    if tags:
        wanted = set(tags)
        pool = [spot for spot in pool if wanted.intersection(spot.tags)]

    # Spots the caller insisted on are in regardless of tags.
    by_id = {spot.spot_id: spot for spot in pool}
    for spot in required:
        if spot.spot_id not in excluded:
            by_id.setdefault(spot.spot_id, spot)
    pool = list(by_id.values())

    pool.sort(key=lambda spot: _detour_m(base, destination, spot))
    required_ids = {spot.spot_id for spot in required}
    must_keep = [spot for spot in pool if spot.spot_id in required_ids]
    optional = [spot for spot in pool if spot.spot_id not in required_ids]
    room = max(0, MAX_SUGGESTION_POOL - len(must_keep))
    return must_keep + optional[:room]


def _detour_m(base: Spot, destination: Spot, spot: Spot) -> float:
    """How much further going via this spot is than going straight there."""

    direct = haversine_distance_m(base.lat, base.lng, destination.lat, destination.lng)
    via = haversine_distance_m(base.lat, base.lng, spot.lat, spot.lng) + haversine_distance_m(
        spot.lat, spot.lng, destination.lat, destination.lng
    )
    return via - direct


def _expand(
    beam: list[_Partial],
    pool: list[Spot],
    base: Spot,
    engine: GTFSEngine,
    departure_date: str,
    deadline_minutes: int,
    dwell_minutes: int,
    max_transfers: int,
    cache: LegCache,
    required: list[Spot],
) -> list[_Partial]:
    grown: list[_Partial] = []
    for partial in beam:
        visited = {stop.spot.spot_id for stop in partial.stops}
        position = base if not partial.stops else partial.position
        for spot in pool:
            if spot.spot_id in visited:
                continue
            if _too_far(position, spot, partial.free_at_minutes + dwell_minutes, deadline_minutes):
                continue
            arrival = cached_leg_arrival(
                position,
                spot,
                _on_grid(partial.free_at_minutes),
                engine,
                departure_date,
                cache,
                max_transfers=max_transfers,
            )
            # No point going somewhere we are already too late to leave.
            if arrival is None or arrival + dwell_minutes > deadline_minutes:
                continue
            grown.append(
                _Partial(
                    stops=(
                        *partial.stops,
                        SuggestedStop(
                            spot=spot,
                            arrival_minutes=arrival,
                            departure_minutes=arrival + dwell_minutes,
                        ),
                    ),
                    free_at_minutes=arrival + dwell_minutes,
                )
            )

    # Rank on what is still missing first, then on time. Sorting on time
    # alone drops every branch through a required spot the moment that spot
    # is further out than the alternatives, and the must-include check at
    # the end is then left with nothing to accept.
    required_ids = {spot.spot_id for spot in required}

    def still_missing(partial: _Partial) -> int:
        return len(required_ids - {stop.spot.spot_id for stop in partial.stops})

    grown.sort(key=lambda partial: (still_missing(partial), partial.free_at_minutes))
    return grown[:SUGGESTION_BEAM_WIDTH]


def _close_off(
    beam: list[_Partial],
    base: Spot,
    destination: Spot,
    engine: GTFSEngine,
    departure_date: str,
    deadline_minutes: int,
    max_transfers: int,
    required: list[Spot],
    cache: LegCache,
) -> list[Candidate]:
    required_ids = {spot.spot_id for spot in required}
    candidates: list[Candidate] = []
    for partial in beam:
        if not required_ids.issubset({stop.spot.spot_id for stop in partial.stops}):
            continue
        if _too_far(partial.position, destination, partial.free_at_minutes, deadline_minutes):
            continue
        arrival = cached_leg_arrival(
            partial.position,
            destination,
            _on_grid(partial.free_at_minutes),
            engine,
            departure_date,
            cache,
            max_transfers=max_transfers,
        )
        if arrival is None or arrival > deadline_minutes:
            continue
        candidates.append(
            Candidate(stops=partial.stops, destination_arrival_minutes=arrival)
        )
    return candidates


def _rank(candidates: list[Candidate], max_candidates: int) -> list[Candidate]:
    """Most spots first, then soonest arrival — one entry per set of spots.

    Candidates that differ only in what order the same places are visited
    are not a choice worth presenting, so the set of spots is the identity.
    """

    best_by_spots: dict[frozenset[str], Candidate] = {}
    for candidate in candidates:
        current = best_by_spots.get(candidate.spot_ids)
        if (
            current is None
            or candidate.destination_arrival_minutes < current.destination_arrival_minutes
        ):
            best_by_spots[candidate.spot_ids] = candidate

    ranked = sorted(
        best_by_spots.values(),
        key=lambda candidate: (
            -len(candidate.stops),
            candidate.destination_arrival_minutes,
        ),
    )
    return ranked[:max_candidates]


def _too_far(origin: Spot, destination: Spot, committed_minutes: int, deadline: int) -> bool:
    """True when even an impossibly fast trip would miss the deadline."""

    floor_minutes = ceil(
        haversine_distance_m(origin.lat, origin.lng, destination.lat, destination.lng)
        / MAX_PLAUSIBLE_SPEED_M_PER_MIN
    )
    return committed_minutes + floor_minutes > deadline


def _on_grid(minutes: int) -> int:
    """Snap a departure onto the cache grid, always rounding *up*.

    Rounding down would quietly assume the traveller leaves earlier than
    they really can, which with buses this sparse means promising a
    connection that has already gone — and then waiting an hour for the
    next one. Rounding up can only ever be conservative.
    """

    return minutes + (-minutes % TIME_GRID_MINUTES)


def describe(candidate: Candidate, destination: Spot) -> str:
    """One-line summary, for the ai_hint."""

    names = " → ".join(stop.spot.name for stop in candidate.stops)
    return (
        f"{names} → {destination.name}"
        f"（{format_minutes(candidate.destination_arrival_minutes)}着）"
    )
