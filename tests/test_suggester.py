from __future__ import annotations

import json
from pathlib import Path

import pytest
from gtfs_multifeed import GTFSEngine

from app.services.spot_repository import SpotRepository
from app.services.suggester import SuggestInputError, suggest_itineraries
from app.services.travel_estimate import format_minutes

SUGGEST_NET_DIR = Path(__file__).parent / "fixtures" / "suggest_net"

# Spots sit on their stops so walking never clouds what is being asserted.
SPOTS = [
    {"spot_id": "hub", "name": "Hub", "lat": 36.0, "lng": 140.0, "tags": ["交通"]},
    {"spot_id": "a", "name": "A Farm", "lat": 36.05, "lng": 140.0, "tags": ["牧場"]},
    {"spot_id": "b", "name": "B Museum", "lat": 36.1, "lng": 140.0, "tags": ["美術館"]},
    {"spot_id": "c", "name": "C Far", "lat": 36.4, "lng": 140.0, "tags": ["牧場"]},
    {"spot_id": "end", "name": "End Hotel", "lat": 36.15, "lng": 140.0, "tags": ["宿泊"]},
]


@pytest.fixture
def engine() -> GTFSEngine:
    return GTFSEngine(SUGGEST_NET_DIR)


@pytest.fixture
def spots(tmp_path: Path) -> SpotRepository:
    spots_file = tmp_path / "spots.json"
    spots_file.write_text(json.dumps(SPOTS), encoding="utf-8")
    return SpotRepository(spots_file)


def _suggest(spots: SpotRepository, engine: GTFSEngine, **kwargs):
    defaults = dict(
        base_location_id="hub",
        destination_spot_id="end",
        departure_time="08:00",
        departure_date="2026-03-02",
        arrive_by="18:00",
        spot_count=2,
        dwell_minutes=60,
    )
    return suggest_itineraries(spots=spots, engine=engine, **{**defaults, **kwargs})


def _names(candidate) -> list[str]:
    return [stop.spot.name for stop in candidate.stops]


def test_suggests_several_candidates_with_different_spots(spots, engine) -> None:
    candidates = _suggest(spots, engine)

    assert candidates
    # Orderings of the same places are not separate choices.
    spot_sets = [candidate.spot_ids for candidate in candidates]
    assert len(spot_sets) == len(set(spot_sets))


def test_never_includes_the_start_or_destination_as_a_stop(spots, engine) -> None:
    for candidate in _suggest(spots, engine):
        assert "hub" not in candidate.spot_ids
        assert "end" not in candidate.spot_ids


def test_respects_the_arrival_deadline(spots, engine) -> None:
    candidates = _suggest(spots, engine, arrive_by="12:00")

    for candidate in candidates:
        assert candidate.destination_arrival_minutes <= 12 * 60


def test_returns_nothing_when_the_deadline_is_impossible(spots, engine) -> None:
    assert _suggest(spots, engine, arrive_by="08:30") == []


def test_tags_narrow_the_suggestions(spots, engine) -> None:
    candidates = _suggest(spots, engine, tags=["牧場"], spot_count=1)

    assert candidates
    for candidate in candidates:
        for stop in candidate.stops:
            assert "牧場" in stop.spot.tags
    # B Museum carries no 牧場 tag, so it must not appear anywhere.
    assert all("B Museum" not in _names(candidate) for candidate in candidates)


def test_must_include_spot_appears_in_every_candidate(spots, engine) -> None:
    candidates = _suggest(spots, engine, must_include_spot_ids=["b"])

    assert candidates
    for candidate in candidates:
        assert "b" in candidate.spot_ids


def test_must_include_wins_over_a_tag_filter(spots, engine) -> None:
    # "b" has no 牧場 tag but was explicitly demanded.
    candidates = _suggest(
        spots, engine, tags=["牧場"], must_include_spot_ids=["b"], spot_count=2
    )

    assert candidates
    for candidate in candidates:
        assert "b" in candidate.spot_ids


def test_stop_times_run_forward_and_respect_the_dwell(spots, engine) -> None:
    candidate = _suggest(spots, engine, dwell_minutes=45)[0]

    previous = 0
    for stop in candidate.stops:
        assert stop.arrival_minutes >= previous
        assert stop.departure_minutes == stop.arrival_minutes + 45
        previous = stop.departure_minutes
    assert candidate.destination_arrival_minutes >= previous


def test_candidate_collects_the_tags_of_its_spots(spots, engine) -> None:
    candidate = _suggest(spots, engine, must_include_spot_ids=["a"], spot_count=1)[0]

    assert candidate.tags == ["牧場"]


def test_honours_max_candidates(spots, engine) -> None:
    assert len(_suggest(spots, engine, max_candidates=1)) <= 1


def test_unknown_spot_id_is_an_input_error(spots, engine) -> None:
    with pytest.raises(SuggestInputError):
        _suggest(spots, engine, destination_spot_id="nope")


def test_format_minutes_handles_times_past_midnight() -> None:
    assert format_minutes(25 * 60 + 5) == "25:05"


def test_departure_grid_rounds_up_so_estimates_stay_conservative() -> None:
    """Snapping a departure onto the cache grid must never move it earlier.

    Rounding down was silently letting candidates catch buses that had
    already left. On real data that turned a 14:05 estimate into a 15:34
    arrival — the missed connection cost an extra hour of waiting — so
    the direction of this rounding is load-bearing, not a detail.
    """

    from app.services.suggester import TIME_GRID_MINUTES, _on_grid

    for minutes in range(0, 120):
        assert _on_grid(minutes) >= minutes
        assert _on_grid(minutes) - minutes < TIME_GRID_MINUTES
        assert _on_grid(minutes) % TIME_GRID_MINUTES == 0


def test_suggested_times_are_not_earlier_than_a_direct_estimate(spots, engine) -> None:
    """A candidate must not promise an arrival the estimator cannot back up."""

    from app.services.travel_estimate import estimate_leg_arrival

    candidate = _suggest(spots, engine, spot_count=1)[0]
    stop = candidate.stops[0]

    direct = estimate_leg_arrival(
        spots.get("hub"), stop.spot, 8 * 60, engine, "2026-03-02"
    )
    assert direct is not None
    assert stop.arrival_minutes >= direct
