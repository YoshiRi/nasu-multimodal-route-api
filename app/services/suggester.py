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

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from gtfs_multifeed import GTFSEngine, haversine_distance_m, parse_gtfs_time_to_minutes

from app.services.opening_hours import VisitRefused, is_closed_on, plan_visit
from app.services.spot_repository import Spot, SpotNotFoundError, SpotRepository
from app.services.travel_estimate import (
    LegCache,
    cached_leg_arrival,
    estimated_taxi_minutes,
    format_minutes,
    needs_taxi_regardless,
    taxi_distance_m,
)

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

# On the second, taxi-allowed pass a taxi is offered in place of a bus that
# does run only when it saves at least this much. Buses here are hourly or
# worse, so a missed connection costs far more than this; anything less
# isn't worth a fare.
TAXI_MIN_SAVING_MINUTES = 30


class SuggestInputError(ValueError):
    """Raised for malformed requests (unknown spot_id, etc.) — maps to HTTP 4xx."""


class RequiredSpotClosed(Exception):
    """A must-include spot is closed that day; the message says which and when."""


@dataclass(frozen=True)
class SuggestedStop:
    spot: Spot
    arrival_minutes: int
    departure_minutes: int
    # True when the only way to get here in time was a taxi.
    by_taxi: bool = False
    # Minutes spent outside before it opened.
    wait_minutes: int = 0


@dataclass(frozen=True)
class Candidate:
    stops: tuple[SuggestedStop, ...]
    destination_arrival_minutes: int
    taxi_legs: int = 0
    # True when the hop to the destination is the taxi one.
    destination_by_taxi: bool = False

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
    taxi_legs: int = 0

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
    dwell_minutes: int | None = None,
    max_candidates: int = 3,
    max_transfers: int = 1,
    max_taxi_legs: int = 0,
) -> list[Candidate]:
    """Return candidate itineraries, soonest arrival at the destination first.

    dwell_minutes applies to every stop when given; when None each spot
    gets its own typical stay (Spot.stay_minutes). Spots closed on
    departure_date are never suggested, and stays must fit their hours.
    """

    try:
        base = spots.get(base_location_id)
        destination = spots.get(destination_spot_id)
        required = [spots.get(spot_id) for spot_id in must_include_spot_ids or []]
    except SpotNotFoundError as exc:
        raise SuggestInputError(f"Unknown spot_id: {exc}") from exc

    for spot in required:
        if is_closed_on(spot.hours, departure_date):
            raise RequiredSpotClosed(
                f"{spot.name}は{departure_date}は休業日です（営業情報は概算・要確認）。"
            )

    depart_minutes = parse_gtfs_time_to_minutes(departure_time)
    deadline_minutes = parse_gtfs_time_to_minutes(arrive_by)
    pool = _build_pool(spots, base, destination, tags, required, departure_date)
    if not pool:
        return []

    search = _Search(
        pool=pool, base=base, destination=destination, engine=engine,
        departure_date=departure_date, deadline_minutes=deadline_minutes,
        dwell_minutes=dwell_minutes, max_transfers=max_transfers,
        required=required, max_taxi_legs=max_taxi_legs, cache={},
    )
    # Two passes, because "what works by bus" and "what would a taxi
    # rescue" pull the beam in opposite directions. The first keeps buses
    # ahead in the beam and only takes a taxi where no bus runs at all. The
    # second, only if taxis are allowed, also offers a taxi past a bus that
    # runs too late, and ranks on time so those branches survive. _rank
    # then keeps the fewest-taxi version of each set of spots, so a taxi
    # only ever shows up in a trip the bus can't do.
    finished = search.run(depart_minutes, spot_count, past_late_buses=False)
    if max_taxi_legs:
        finished += search.run(depart_minutes, spot_count, past_late_buses=True)
    return _rank(finished, max_candidates)


def _build_pool(
    spots: SpotRepository,
    base: Spot,
    destination: Spot,
    tags: list[str] | None,
    required: list[Spot],
    departure_date: str,
) -> list[Spot]:
    excluded = {base.spot_id, destination.spot_id}
    # Drop closed spots before cutting the pool down to size, or a day off
    # would waste a slot that an open spot could have had.
    pool = [
        spot for spot in spots.list_all()
        if spot.spot_id not in excluded and not is_closed_on(spot.hours, departure_date)
    ]

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


@dataclass
class _Search:
    """One beam search over a fixed pool; run() once per pass."""

    pool: list[Spot]
    base: Spot
    destination: Spot
    engine: GTFSEngine
    departure_date: str
    deadline_minutes: int
    dwell_minutes: int | None
    max_transfers: int
    required: list[Spot]
    max_taxi_legs: int
    cache: LegCache  # shared by both passes: same legs, same times

    def run(self, depart_minutes: int, spot_count: int, past_late_buses: bool) -> list[Candidate]:
        beam = [_Partial(stops=(), free_at_minutes=depart_minutes)]
        finished: list[Candidate] = []
        for _ in range(spot_count):
            beam = self._expand(beam, past_late_buses)
            if not beam:
                break
            finished.extend(self._close_off(beam, past_late_buses))
        return finished

    def _reach(
        self, origin: Spot, target: Spot, depart_minutes: int, taxi_legs_used: int,
        past_late_buses: bool,
    ) -> list[tuple[int, bool]]:
        """Ways to make this hop: (arrival, by_taxi) for each, maybe none.

        Taxi is a rescue, not an optimisation. On the first pass it is only
        considered where no bus gets through at all; on the second it may
        also replace a bus that runs but lands TAXI_MIN_SAVING_MINUTES or
        more later — the winter bus that still goes to the farm, four hours
        after the summer one would have.

        Hops with no bus stop at either end and too far to walk are a taxi
        by geography rather than by choice, but they still count against
        the allowance: asking for no taxis and being handed a ¥15,000 fare
        is not an answer to the question that was asked.
        """

        can_taxi = taxi_legs_used < self.max_taxi_legs
        by_taxi = depart_minutes + estimated_taxi_minutes(origin, target)
        by_bus = cached_leg_arrival(
            origin, target, _on_grid(depart_minutes), self.engine, self.departure_date,
            self.cache, max_transfers=self.max_transfers,
        )

        if by_bus is None:
            return [(by_taxi, True)] if can_taxi else []
        if needs_taxi_regardless(origin, target, self.engine):
            return [(by_bus, True)] if can_taxi else []

        options = [(by_bus, False)]
        if past_late_buses and can_taxi and by_taxi + TAXI_MIN_SAVING_MINUTES <= by_bus:
            options.append((by_taxi, True))
        return options

    def _expand(self, beam: list[_Partial], past_late_buses: bool) -> list[_Partial]:
        grown: list[_Partial] = []
        for partial in beam:
            visited = {stop.spot.spot_id for stop in partial.stops}
            position = self.base if not partial.stops else partial.position
            for spot in self.pool:
                if spot.spot_id in visited:
                    continue
                stay = spot.stay_minutes(self.dwell_minutes)
                if _too_far(position, spot, partial.free_at_minutes + stay, self.deadline_minutes):
                    continue
                for arrival, by_taxi in self._reach(
                    position, spot, partial.free_at_minutes, partial.taxi_legs, past_late_buses
                ):
                    try:
                        visit = plan_visit(spot.name, spot.hours, self.departure_date, arrival, stay)
                    except VisitRefused:
                        continue
                    # No point going somewhere we are already too late to leave.
                    if visit.end_minutes > self.deadline_minutes:
                        continue
                    grown.append(
                        _Partial(
                            stops=(
                                *partial.stops,
                                SuggestedStop(
                                    spot=spot,
                                    arrival_minutes=arrival,
                                    departure_minutes=visit.end_minutes,
                                    by_taxi=by_taxi,
                                    wait_minutes=visit.start_minutes - arrival,
                                ),
                            ),
                            free_at_minutes=visit.end_minutes,
                            taxi_legs=partial.taxi_legs + (1 if by_taxi else 0),
                        )
                    )

        # Rank on what is still missing first. Sorting on time alone drops
        # every branch through a required spot the moment that spot is
        # further out than the alternatives, and the must-include check at
        # the end is then left with nothing to accept.
        #
        # Then, on the first pass, on taxis used: a taxi hop usually lands
        # sooner, so time alone fills the beam with taxi branches and the
        # all-bus candidates _rank would have put first never reach it. The
        # second pass exists to find the taxi branches, so it ranks on time.
        required_ids = {spot.spot_id for spot in self.required}

        def still_missing(partial: _Partial) -> int:
            return len(required_ids - {stop.spot.spot_id for stop in partial.stops})

        def key(partial: _Partial) -> tuple[int, ...]:
            if past_late_buses:
                return still_missing(partial), partial.free_at_minutes
            return still_missing(partial), partial.taxi_legs, partial.free_at_minutes

        grown.sort(key=key)
        return grown[:SUGGESTION_BEAM_WIDTH]

    def _close_off(self, beam: list[_Partial], past_late_buses: bool) -> list[Candidate]:
        required_ids = {spot.spot_id for spot in self.required}
        candidates: list[Candidate] = []
        for partial in beam:
            if not required_ids.issubset({stop.spot.spot_id for stop in partial.stops}):
                continue
            if _too_far(
                partial.position, self.destination, partial.free_at_minutes, self.deadline_minutes
            ):
                continue
            for arrival, by_taxi in self._reach(
                partial.position, self.destination, partial.free_at_minutes,
                partial.taxi_legs, past_late_buses,
            ):
                if arrival > self.deadline_minutes:
                    continue
                candidates.append(
                    Candidate(
                        stops=partial.stops,
                        destination_arrival_minutes=arrival,
                        taxi_legs=partial.taxi_legs + (1 if by_taxi else 0),
                        destination_by_taxi=by_taxi,
                    )
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
        if current is None or (candidate.taxi_legs, candidate.destination_arrival_minutes) < (
            current.taxi_legs,
            current.destination_arrival_minutes,
        ):
            best_by_spots[candidate.spot_ids] = candidate

    ranked = sorted(
        best_by_spots.values(),
        key=lambda candidate: (
            candidate.taxi_legs,  # an all-bus trip beats one needing a taxi
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


def taxi_legs_of(
    candidate: Candidate, base: Spot, destination: Spot
) -> list[tuple[Spot, Spot]]:
    """The (from, to) pairs in this candidate that assume a taxi.

    The stops carry their own flag; the hop to the destination has its
    own on the candidate.
    """

    legs: list[tuple[Spot, Spot]] = []
    position = base
    for stop in candidate.stops:
        if stop.by_taxi:
            legs.append((position, stop.spot))
        position = stop.spot
    if candidate.destination_by_taxi:
        legs.append((position, destination))
    return legs


def estimate_taxi_fare_jpy(
    candidate: Candidate,
    base: Spot,
    destination: Spot,
    fare_estimator: Callable[[float], int],
) -> int:
    return sum(
        fare_estimator(taxi_distance_m(origin, target))
        for origin, target in taxi_legs_of(candidate, base, destination)
    )


def describe(candidate: Candidate, destination: Spot) -> str:
    """One-line summary, for the ai_hint."""

    names = " → ".join(stop.spot.name for stop in candidate.stops)
    taxi = f"・タクシー{candidate.taxi_legs}区間" if candidate.taxi_legs else ""
    return (
        f"{names} → {destination.name}"
        f"（{format_minutes(candidate.destination_arrival_minutes)}着{taxi}）"
    )
