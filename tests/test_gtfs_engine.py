from pathlib import Path
from datetime import date

import pytest

from app.services.gtfs_engine import (
    GTFSEngine,
    parse_gtfs_time_to_minutes,
    parse_service_date_to_gtfs_date,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"


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


def test_parse_gtfs_time_to_minutes_allows_hour_over_24() -> None:
    assert parse_gtfs_time_to_minutes("24:15:00") == 1455


def test_parse_gtfs_time_to_minutes_rejects_invalid_minute() -> None:
    with pytest.raises(ValueError, match="Invalid GTFS time"):
        parse_gtfs_time_to_minutes("10:60:00")


def test_parse_service_date_to_gtfs_date_accepts_iso_and_compact_dates() -> None:
    assert parse_service_date_to_gtfs_date("2026-08-20") == "20260820"
    assert parse_service_date_to_gtfs_date("20260820") == "20260820"
