from pathlib import Path
from datetime import date

import pytest

from gtfs_multifeed import (
    GTFSEngine,
    haversine_distance_m,
    discover_gtfs_feeds,
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
        "shape_id": "shape_nasu_line",
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


def test_bus_leg_path_returns_shape_slice_between_stops() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    # trip_1's shape starts exactly at stop_yumoto and ends exactly at
    # stop_ropeway, so the whole shape is the ridden segment.
    path = engine.bus_leg_path("trip_1", "stop_yumoto", "stop_ropeway")

    assert path == [[37.0950, 140.0010], [37.1100, 139.9900], [37.1240, 139.9850]]


def test_bus_leg_path_returns_none_for_trip_without_shape() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    assert engine.bus_leg_path("trip_reverse", "stop_ropeway", "stop_yumoto") is None


def test_bus_leg_path_returns_none_when_no_shapes_file() -> None:
    engine = GTFSEngine(OTHER_FIXTURE_DIR)

    assert engine.bus_leg_path("trip_1", "stop_yumoto", "stop_ropeway") is None


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


def test_discover_gtfs_feeds_returns_empty_for_missing_root(tmp_path: Path) -> None:
    assert discover_gtfs_feeds(tmp_path / "does-not-exist") == {}


def test_discover_gtfs_feeds_finds_valid_feed_subdirectories(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(FIXTURE_DIR, tmp_path / "feed_a")
    shutil.copytree(OTHER_FIXTURE_DIR, tmp_path / "feed_b")
    (tmp_path / "not_a_feed").mkdir()
    (tmp_path / "not_a_feed" / "readme.txt").write_text("no GTFS files here")

    feeds = discover_gtfs_feeds(tmp_path)

    assert set(feeds) == {"feed_a", "feed_b"}
    assert feeds["feed_a"] == tmp_path / "feed_a"


# --- nearby_stops ----------------------------------------------------------
# Route planning calls this once per endpoint of every leg it considers, so
# it is both hot and load-bearing; these pin the contract its callers rely
# on (ordering, the radius boundary, and the distances themselves).


def test_nearby_stops_returns_nearest_first() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    # Just north of 那須塩原駅, which sits ~140m from その他停留所.
    nearby = engine.nearby_stops(lat=36.9310, lng=140.0200, radius_m=5000)

    assert [stop["stop_id"] for stop in nearby] == ["stop_station", "stop_extra"]
    distances = [stop["distance_m"] for stop in nearby]
    assert distances == sorted(distances)


def test_nearby_stops_measures_from_the_given_point() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    nearby = engine.nearby_stops(lat=37.0950, lng=140.0010, radius_m=100)

    assert len(nearby) == 1
    assert nearby[0]["stop_id"] == "stop_yumoto"
    assert nearby[0]["distance_m"] == 0.0
    assert nearby[0]["stop_name"] == "那須湯本"
    assert nearby[0]["stop_lat"] == pytest.approx(37.0950)
    assert nearby[0]["stop_lon"] == pytest.approx(140.0010)


def test_nearby_stops_excludes_anything_past_the_radius() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    # 那須塩原駅 to その他停留所 is roughly 140m.
    assert len(engine.nearby_stops(36.9310, 140.0200, radius_m=50)) == 1
    assert len(engine.nearby_stops(36.9310, 140.0200, radius_m=500)) == 2


def test_nearby_stops_returns_empty_when_nothing_is_in_range() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    assert engine.nearby_stops(lat=0.0, lng=0.0, radius_m=1000) == []


def test_nearby_stops_agrees_with_haversine_distance_m() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    nearby = engine.nearby_stops(lat=37.0950, lng=140.0010, radius_m=50_000)

    for stop in nearby:
        expected = haversine_distance_m(
            37.0950, 140.0010, stop["stop_lat"], stop["stop_lon"]
        )
        assert stop["distance_m"] == pytest.approx(round(expected, 1))


def test_nearby_stops_works_on_merged_feeds() -> None:
    # from_feeds() bypasses __init__, where the coordinate cache would
    # otherwise be set up.
    merged = GTFSEngine.from_feeds({"nasu": FIXTURE_DIR, "other": OTHER_FIXTURE_DIR})

    nearby = merged.nearby_stops(lat=37.0950, lng=140.0010, radius_m=100)

    assert [stop["stop_id"] for stop in nearby] == ["nasu:stop_yumoto"]


def test_service_period_spans_calendar_and_added_dates() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    # calendar.txt runs Aug 1-31; calendar_dates.txt adds Aug 22, inside it.
    assert engine.service_period() == (date(2026, 8, 1), date(2026, 8, 31))


def test_service_period_covers_every_merged_feed() -> None:
    merged = GTFSEngine.from_feeds({"a": FIXTURE_DIR, "b": OTHER_FIXTURE_DIR})
    start, end = merged.service_period()

    for single in (GTFSEngine(FIXTURE_DIR), GTFSEngine(OTHER_FIXTURE_DIR)):
        own_start, own_end = single.service_period()
        assert start <= own_start and own_end <= end


def test_service_periods_reports_each_feed_separately() -> None:
    merged = GTFSEngine.from_feeds({"a": FIXTURE_DIR, "b": OTHER_FIXTURE_DIR})

    assert merged.service_periods() == {
        "a": GTFSEngine(FIXTURE_DIR).service_period(),
        "b": GTFSEngine(OTHER_FIXTURE_DIR).service_period(),
    }


def test_single_feed_reports_under_its_directory_name() -> None:
    assert list(GTFSEngine(FIXTURE_DIR).service_periods()) == ["minimal_gtfs"]


def test_feeds_not_covering_names_only_the_expired_feeds() -> None:
    engine = GTFSEngine(FIXTURE_DIR)

    assert engine.feeds_not_covering("2026-08-17") == {}
    assert engine.feeds_not_covering("2026-09-01") == {
        "minimal_gtfs": (date(2026, 8, 1), date(2026, 8, 31))
    }
