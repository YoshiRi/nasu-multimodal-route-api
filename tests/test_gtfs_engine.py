from pathlib import Path
from datetime import date

import pytest

from app.services.gtfs_engine import (
    GTFSEngine,
    parse_gtfs_time_to_minutes,
    parse_service_date_to_gtfs_date,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"
OTHER_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs_other"


def test_search_next_bus_returns_up_to_three_direct_trips() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus("stop_yumoto", "stop_ropeway", "10:00")

    assert [result["trip_id"] for result in results] == ["trip_1", "trip_2", "trip_3"]
    assert results[0] == {
        "route_id": "route_nasu",
        "route_short_name": "那須線",
        "route_long_name": "那須湯本・ロープウェイ線",
        "line_name": "那須線",
        "trip_id": "trip_1",
        "trip_headsign": "那須ロープウェイ行",
        "origin_stop_id": "stop_yumoto",
        "destination_stop_id": "stop_ropeway",
        "departure_time": "10:15:00",
        "arrival_time": "10:35:00",
        "duration_min": 20,
    }


def test_search_next_bus_ignores_reverse_direction() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus("stop_yumoto", "stop_ropeway", "10:16")

    assert [result["trip_id"] for result in results] == ["trip_2", "trip_3", "trip_4"]


def test_search_next_bus_supports_gtfs_times_after_midnight() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus("stop_yumoto", "stop_ropeway", "24:00")

    assert [result["trip_id"] for result in results] == ["trip_4"]
    assert results[0]["duration_min"] == 20


def test_search_next_bus_returns_empty_when_no_direct_trip_exists() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus("stop_station", "stop_ropeway", "10:00")

    assert results == []


def test_search_next_bus_filters_by_active_weekday_service() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus(
        "stop_yumoto",
        "stop_ropeway",
        "10:00",
        departure_date="2026-08-17",
    )

    assert [result["trip_id"] for result in results] == ["trip_1", "trip_2", "trip_3"]


def test_search_next_bus_applies_removed_service_exception() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus(
        "stop_yumoto",
        "stop_ropeway",
        "10:00",
        departure_date="2026-08-21",
    )

    assert results == []


def test_search_next_bus_applies_added_service_exception() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus(
        "stop_yumoto",
        "stop_ropeway",
        "10:00",
        departure_date=date(2026, 8, 22),
    )

    assert [result["trip_id"] for result in results] == ["trip_1", "trip_2", "trip_3"]


def test_search_next_bus_returns_empty_for_inactive_service_date() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    results = engine.search_next_bus(
        "stop_yumoto",
        "stop_ropeway",
        "10:00",
        departure_date="2026-08-23",
    )

    assert results == []


def test_from_feeds_prefixes_ids_to_avoid_collisions() -> None:
    # Both fixtures independently use stop_id "stop_yumoto", route_id
    # "route_nasu", trip_id "trip_1", and service_id "weekday" for
    # unrelated stops/routes/trips.
    merged = GTFSEngine.from_feeds({"nasu": FIXTURE_DIR, "other": OTHER_FIXTURE_DIR})

    assert len(merged.stops) == 6  # 4 boardable (FIXTURE_DIR) + 2 (OTHER_FIXTURE_DIR)
    assert merged.stops["stop_id"].nunique() == 6
    assert set(merged.stops["stop_id"]) >= {
        "nasu:stop_yumoto",
        "other:stop_yumoto",
    }
    assert set(merged.trips["trip_id"]) >= {"nasu:trip_1", "other:trip_1"}


def test_from_feeds_search_next_bus_is_scoped_per_feed() -> None:
    merged = GTFSEngine.from_feeds({"nasu": FIXTURE_DIR, "other": OTHER_FIXTURE_DIR})

    nasu_results = merged.search_next_bus("nasu:stop_yumoto", "nasu:stop_ropeway", "10:00")
    assert [r["trip_id"] for r in nasu_results] == ["nasu:trip_1", "nasu:trip_2", "nasu:trip_3"]

    other_results = merged.search_next_bus("other:stop_yumoto", "other:stop_town_hall", "00:00")
    assert [r["trip_id"] for r in other_results] == ["other:trip_1"]


def test_from_feeds_unprefixed_id_matches_nothing() -> None:
    merged = GTFSEngine.from_feeds({"nasu": FIXTURE_DIR, "other": OTHER_FIXTURE_DIR})

    # The raw (unprefixed) ID that collides across feeds should not match
    # either feed's stops once merged.
    assert merged.search_next_bus("stop_yumoto", "stop_ropeway", "10:00") == []


def test_from_feeds_merges_calendar_even_when_one_feed_lacks_calendar_dates() -> None:
    merged = GTFSEngine.from_feeds({"nasu": FIXTURE_DIR, "other": OTHER_FIXTURE_DIR})

    active = merged.active_service_ids("2026-08-17")

    assert active == {"nasu:weekday", "other:weekday"}


def test_route_shapes_returns_points_in_sequence_order_with_line_name() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    shapes = engine.route_shapes()

    assert shapes == [
        {
            "shape_id": "shape_nasu_line",
            "route_id": "route_nasu",
            "line_name": "那須線",
            "points": [[37.0950, 140.0010], [37.1100, 139.9900], [37.1240, 139.9850]],
        }
    ]


def test_route_shapes_returns_empty_list_when_no_shapes_file() -> None:
    engine = GTFSEngine(OTHER_FIXTURE_DIR)

    assert engine.route_shapes() == []


def test_from_feeds_prefixes_shape_ids() -> None:
    merged = GTFSEngine.from_feeds({"nasu": FIXTURE_DIR, "other": OTHER_FIXTURE_DIR})

    shapes = merged.route_shapes()

    assert [shape["shape_id"] for shape in shapes] == ["nasu:shape_nasu_line"]
    assert shapes[0]["route_id"] == "nasu:route_nasu"


def test_from_feeds_requires_at_least_one_feed() -> None:
    with pytest.raises(ValueError, match="at least one feed"):
        GTFSEngine.from_feeds({})


def test_parse_gtfs_time_to_minutes_allows_hour_over_24() -> None:
    assert parse_gtfs_time_to_minutes("24:15:00") == 1455


def test_parse_gtfs_time_to_minutes_rejects_invalid_minute() -> None:
    with pytest.raises(ValueError, match="Invalid GTFS time"):
        parse_gtfs_time_to_minutes("10:60:00")


def test_parse_service_date_to_gtfs_date_accepts_iso_and_compact_dates() -> None:
    assert parse_service_date_to_gtfs_date("2026-08-20") == "20260820"
    assert parse_service_date_to_gtfs_date("20260820") == "20260820"
