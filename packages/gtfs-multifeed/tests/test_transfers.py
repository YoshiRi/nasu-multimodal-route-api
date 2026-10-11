from __future__ import annotations

from pathlib import Path

import pytest

from gtfs_multifeed import GTFSEngine
from gtfs_multifeed.engine import MIN_TRANSFER_MINUTES

TRANSFER_NET_DIR = Path(__file__).parent / "fixtures" / "transfer_net"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"


def _edge(edges, from_stop_id, to_stop_id):
    match = edges[
        (edges["from_stop_id"] == from_stop_id) & (edges["to_stop_id"] == to_stop_id)
    ]
    return None if match.empty else match.iloc[0]


def test_transfer_edges_connects_stops_within_radius() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    edges = engine.transfer_edges()

    # stop_b and stop_b2 are ~35m apart, across the road from each other.
    assert _edge(edges, "stop_b", "stop_b2") is not None
    # ...and the pair is listed in both directions.
    assert _edge(edges, "stop_b2", "stop_b") is not None


def test_transfer_edges_excludes_distant_and_self_pairs() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    edges = engine.transfer_edges()

    # ~1.1km apart — far beyond a transfer walk.
    assert _edge(edges, "stop_a", "stop_b") is None
    # ~21km away, connected to nothing.
    assert not (edges["from_stop_id"] == "stop_far").any()
    # A stop never transfers to itself.
    assert not (edges["from_stop_id"] == edges["to_stop_id"]).any()


def test_transfer_edges_respects_radius_argument() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    assert _edge(engine.transfer_edges(max_distance_m=10), "stop_b", "stop_b2") is None
    assert _edge(engine.transfer_edges(max_distance_m=300), "stop_b", "stop_b2") is not None


def test_transfer_minutes_floors_at_minimum_for_same_place() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    edge = _edge(engine.transfer_edges(), "stop_b", "stop_b2")

    # ~35m apart: a real but trivial walk, so it takes the floor rather
    # than rounding down to zero — the bus will not wait.
    assert edge["transfer_minutes"] == MIN_TRANSFER_MINUTES
    assert 30 < edge["distance_m"] < 45


def test_transfer_edges_are_cached_per_radius() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    assert engine.transfer_edges(max_distance_m=300) is engine.transfer_edges(max_distance_m=300)
    assert engine.transfer_edges(max_distance_m=50) is not engine.transfer_edges(max_distance_m=300)


def test_transfer_edges_works_on_merged_feeds() -> None:
    # from_feeds() builds instances via __new__, bypassing __init__, so the
    # edge cache has to tolerate not being set up there.
    merged = GTFSEngine.from_feeds({"net": TRANSFER_NET_DIR, "other": FIXTURE_DIR})

    edges = merged.transfer_edges()

    assert _edge(edges, "net:stop_b", "net:stop_b2") is not None


# --- journey search --------------------------------------------------------

DATE = "2026-03-02"  # a Monday inside the fixture's calendar


def test_search_journeys_finds_connection_direct_search_cannot() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    # No single vehicle serves both stops...
    assert engine.search_next_bus("stop_a", "stop_c", "08:00", DATE) == []

    # ...but changing buses at the B Junction pair gets there.
    journeys = engine.search_journeys("stop_a", "stop_c", "08:00", DATE)

    assert journeys
    journey = journeys[0]
    assert journey["num_transfers"] == 1
    assert journey["departure_time"] == "09:00:00"
    assert journey["arrival_time"] == "09:50:00"
    assert journey["duration_min"] == 50
    assert [leg["line_name"] for leg in journey["legs"]] == ["Line 1", "Line 2"]
    assert [leg["origin_stop_id"] for leg in journey["legs"]] == ["stop_a", "stop_b2"]

    transfer = journey["transfers"][0]
    assert (transfer["from_stop_id"], transfer["to_stop_id"]) == ("stop_b", "stop_b2")
    assert transfer["transfer_minutes"] == MIN_TRANSFER_MINUTES


def test_search_journeys_respects_max_transfers_zero() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    assert engine.search_journeys("stop_a", "stop_c", "08:00", DATE, max_transfers=0) == []


def test_search_journeys_will_not_walk_beyond_the_radius() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    # The two B stops are ~35m apart; a 10m radius disconnects them.
    assert engine.search_journeys(
        "stop_a", "stop_c", "08:00", DATE, max_distance_m=10
    ) == []


def test_search_journeys_will_not_board_a_departed_connection() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    # Departing at 10:00 reaches B at 10:20, by which point the 09:30
    # connection is long gone — the journey has to wait for the 11:00.
    journeys = engine.search_journeys("stop_a", "stop_c", "10:00", DATE)

    assert journeys
    assert journeys[0]["legs"][0]["departure_time"] == "10:00:00"
    assert journeys[0]["legs"][1]["departure_time"] == "11:00:00"
    assert journeys[0]["arrival_time"] == "11:20:00"


def test_search_journeys_keeps_direct_trips_when_they_exist() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    journeys = engine.search_journeys("stop_yumoto", "stop_ropeway", "10:00", "2026-08-17")

    assert journeys
    assert journeys[0]["num_transfers"] == 0
    assert len(journeys[0]["legs"]) == 1
    assert journeys[0]["transfers"] == []


def test_search_journeys_rejects_identical_stops() -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)

    with pytest.raises(ValueError):
        engine.search_journeys("stop_a", "stop_a", "08:00", DATE)
