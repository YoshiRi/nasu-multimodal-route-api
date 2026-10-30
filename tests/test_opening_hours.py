from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gtfs_multifeed import GTFSEngine

import app.services.route_planner as route_planner
from app.api.deps import get_gtfs_engine, get_spot_repository
from app.main import app
from app.services.opening_hours import OpeningHours, VisitRefused, plan_visit
from app.services.ors_client import RouteEstimate
from app.services.spot_repository import DEFAULT_STAY_MINUTES, SpotRepository
from app.services.suggester import RequiredSpotClosed, suggest_itineraries
from tests.test_suggester import SPOTS, SUGGEST_NET_DIR

MONDAY = "2026-03-02"
THURSDAY = "2026-03-05"

SAFARI_LIKE = {
    "open": "09:30", "close": "16:30", "last_entry": "15:45",
    "closed_weekdays": ["thu"],
}
WINTER_CLOSED = {
    "open": "08:30", "close": "16:30",
    "seasons": [{"from": "12-01", "to": "03-15", "closed": True}],
}
WINTER_SHORT = {
    "open": "08:00", "close": "17:30",
    "seasons": [{"from": "12-01", "to": "03-31", "open": "09:00", "close": "16:30"}],
}


def _hours(raw: dict) -> OpeningHours:
    return OpeningHours.from_json(raw)


def _at(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


# --- the hours themselves ---------------------------------------------------


def test_closed_weekday_has_no_window() -> None:
    hours = _hours(SAFARI_LIKE)

    assert hours.window(date(2026, 3, 5)) is None  # Thursday
    assert hours.window(date(2026, 3, 2)) == (_at("09:30"), _at("16:30"), _at("15:45"))


def test_a_season_wrapping_new_year_covers_both_ends() -> None:
    hours = _hours(WINTER_CLOSED)

    assert hours.window(date(2026, 12, 20)) is None
    assert hours.window(date(2027, 1, 10)) is None
    assert hours.window(date(2026, 3, 15)) is None
    assert hours.window(date(2026, 3, 16)) is not None
    assert hours.window(date(2026, 11, 30)) is not None


def test_a_season_can_shorten_the_day_instead_of_closing_it() -> None:
    hours = _hours(WINTER_SHORT)

    assert hours.window(date(2026, 1, 10))[:2] == (_at("09:00"), _at("16:30"))
    assert hours.window(date(2026, 8, 10))[:2] == (_at("08:00"), _at("17:30"))


def test_always_open_never_refuses() -> None:
    hours = _hours({"always_open": True})

    visit = plan_visit("橋", hours, THURSDAY, _at("22:00"), 15)
    assert visit.start_minutes == _at("22:00")


@pytest.mark.parametrize("raw", [SAFARI_LIKE, WINTER_CLOSED, WINTER_SHORT, {"always_open": True}])
def test_hours_survive_a_round_trip_through_json(raw: dict) -> None:
    hours = _hours({**raw, "source": "https://example.com", "note": "n"})

    assert OpeningHours.from_json(hours.to_json()) == hours


# --- fitting a stay into them -----------------------------------------------


def test_arriving_early_waits_for_opening() -> None:
    visit = plan_visit("サファリ", _hours(SAFARI_LIKE), MONDAY, _at("09:00"), 60)

    assert visit.start_minutes == _at("09:30")
    assert visit.end_minutes == _at("10:30")


def test_arriving_after_last_entry_is_refused() -> None:
    with pytest.raises(VisitRefused, match="最終入場"):
        plan_visit("サファリ", _hours(SAFARI_LIKE), MONDAY, _at("15:50"), 30)


def test_a_stay_running_past_closing_is_refused_not_cut_short() -> None:
    with pytest.raises(VisitRefused, match="閉館"):
        plan_visit("サファリ", _hours(SAFARI_LIKE), MONDAY, _at("15:00"), 150)


def test_a_closed_day_is_refused_with_the_weekday() -> None:
    with pytest.raises(VisitRefused, match="（木）は休業日"):
        plan_visit("サファリ", _hours(SAFARI_LIKE), THURSDAY, _at("10:00"), 60)


def test_unknown_hours_never_refuse() -> None:
    visit = plan_visit("どこか", None, THURSDAY, _at("23:00"), 60)

    assert (visit.start_minutes, visit.end_minutes) == (_at("23:00"), _at("24:00"))


# --- in the suggester -------------------------------------------------------


def _repo(tmp_path: Path, **per_spot: dict) -> SpotRepository:
    spots = [{**spot, **per_spot.get(spot["spot_id"], {})} for spot in SPOTS]
    path = tmp_path / "spots.json"
    path.write_text(json.dumps(spots, ensure_ascii=False), encoding="utf-8")
    return SpotRepository(path)


def _suggest(spots: SpotRepository, **kwargs):
    defaults = dict(
        base_location_id="hub", destination_spot_id="end",
        departure_time="08:00", departure_date=MONDAY, arrive_by="18:00",
        spot_count=1, max_candidates=10,
    )
    return suggest_itineraries(
        spots=spots, engine=GTFSEngine(SUGGEST_NET_DIR), **{**defaults, **kwargs}
    )


def test_a_spot_closed_that_day_is_never_suggested(tmp_path: Path) -> None:
    spots = _repo(tmp_path, a={"hours": SAFARI_LIKE})

    on_monday = {sid for c in _suggest(spots) for sid in c.spot_ids}
    on_thursday = {sid for c in _suggest(spots, departure_date=THURSDAY) for sid in c.spot_ids}

    assert "a" in on_monday
    assert "a" not in on_thursday


def test_a_closed_spot_does_not_use_up_a_place_in_the_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool is cut to size before searching; closed spots go first.

    A and B both sit on the straight line, A listed first. With room for
    one, a closed A taking that slot would leave nothing to suggest.
    """

    import app.services.suggester as suggester

    monkeypatch.setattr(suggester, "MAX_SUGGESTION_POOL", 1)
    spots = _repo(tmp_path, a={"hours": SAFARI_LIKE})

    assert {sid for c in _suggest(spots, departure_date=THURSDAY) for sid in c.spot_ids} == {"b"}


def test_requiring_a_closed_spot_says_so(tmp_path: Path) -> None:
    spots = _repo(tmp_path, a={"hours": SAFARI_LIKE})

    with pytest.raises(RequiredSpotClosed, match="A Farm"):
        _suggest(spots, departure_date=THURSDAY, must_include_spot_ids=["a"])


def test_suggestion_waits_for_opening(tmp_path: Path) -> None:
    spots = _repo(tmp_path, a={"hours": {"open": "10:00", "close": "17:00"}})

    [candidate] = _suggest(spots, must_include_spot_ids=["a"], dwell_minutes=30)
    stop = candidate.stops[0]

    # The 08:00 bus gets there at 08:20; it opens at 10:00.
    assert stop.arrival_minutes < _at("10:00")
    assert stop.departure_minutes == _at("10:30")
    assert stop.wait_minutes == _at("10:00") - stop.arrival_minutes


def test_a_stay_that_cannot_finish_before_closing_is_dropped(tmp_path: Path) -> None:
    spots = _repo(tmp_path, a={"hours": {"open": "08:00", "close": "09:00"}})

    suggested = {sid for c in _suggest(spots, dwell_minutes=60) for sid in c.spot_ids}

    assert "a" not in suggested


def test_each_spot_keeps_its_own_stay_unless_overridden(tmp_path: Path) -> None:
    spots = _repo(tmp_path, a={"dwell_minutes": 150}, b={"dwell_minutes": 20})

    stays = {
        stop.spot.spot_id: stop.departure_minutes - stop.arrival_minutes - stop.wait_minutes
        for candidate in _suggest(spots)
        for stop in candidate.stops
    }
    assert stays["a"] == 150
    assert stays["b"] == 20

    overridden = _suggest(spots, dwell_minutes=45)
    assert all(
        stop.departure_minutes - stop.arrival_minutes == 45
        for candidate in overridden for stop in candidate.stops
    )


def test_a_spot_with_no_typical_stay_gets_the_default(tmp_path: Path) -> None:
    assert _repo(tmp_path).get("a").stay_minutes() == DEFAULT_STAY_MINUTES


def test_hours_and_stay_survive_being_saved(tmp_path: Path) -> None:
    spots = _repo(tmp_path, a={"hours": SAFARI_LIKE, "dwell_minutes": 150})
    spots.add("New", 36.0, 140.0)  # rewrites the file

    reloaded = SpotRepository(tmp_path / "spots.json").get("a")
    assert reloaded.hours == _hours(SAFARI_LIKE)
    assert reloaded.dwell_minutes == 150


# --- in the API -------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _repo(
        tmp_path,
        a={"hours": {"open": "10:00", "close": "17:00"}},
        b={"hours": SAFARI_LIKE},
    )

    async def fake_walk(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=0, distance_m=0.0, path=[[lat1, lng1], [lat2, lng2]])

    monkeypatch.setattr(route_planner, "get_walking_route", fake_walk)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(SUGGEST_NET_DIR)
    app.dependency_overrides[get_spot_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def _route_plan(client, date: str, *targets: dict) -> dict:
    return client.post("/plan/route-plan", json={
        "base_location_id": "hub", "target_spot_ids": list(targets),
        "departure_time": "08:00", "departure_date": date,
    }).json()


def test_route_plan_waits_for_opening(client) -> None:
    body = _route_plan(client, MONDAY, {"spot_id": "a", "dwell_minutes": 30}, {"spot_id": "end"})

    assert body["status"] == "success", body["ai_hint"]
    steps = body["plans"][0]["steps"]
    wait = next(step for step in steps if step["mode"] == "wait")
    assert wait["end_time"] == "10:00"
    assert "開館" in wait["details"]
    stay = next(step for step in steps if step["mode"] == "stay")
    assert (stay["start_time"], stay["end_time"]) == ("10:00", "10:30")


def test_route_plan_names_a_closed_day(client) -> None:
    body = _route_plan(client, THURSDAY, {"spot_id": "b"}, {"spot_id": "end"})

    assert body["status"] == "error"
    assert "B Museum" in body["ai_hint"] and "休業日" in body["ai_hint"]


def test_route_plan_drops_an_optional_closed_spot(client) -> None:
    body = _route_plan(
        client, THURSDAY,
        {"spot_id": "b", "required": False}, {"spot_id": "a"}, {"spot_id": "end"},
    )

    assert body["status"] == "success", body["ai_hint"]
    assert "B Museum" in body["ai_hint"]  # named as dropped


def test_suggest_reports_hours_and_stay(client) -> None:
    body = client.post("/plan/suggest", json={
        "base_location_id": "hub", "destination_spot_id": "end",
        "departure_time": "08:00", "departure_date": MONDAY, "arrive_by": "18:00",
        "spot_count": 1, "must_include_spot_ids": ["b"],
    }).json()

    [stop] = body["candidates"][0]["stops"]
    assert stop["opening_hours"].startswith("09:30〜16:30（最終入場15:45）")
    assert stop["stay_minutes"] == DEFAULT_STAY_MINUTES
    assert stop["wait_minutes"] > 0


def test_suggest_with_a_closed_required_spot_is_an_error_not_a_crash(client) -> None:
    body = client.post("/plan/suggest", json={
        "base_location_id": "hub", "destination_spot_id": "end",
        "departure_time": "08:00", "departure_date": THURSDAY, "arrive_by": "18:00",
        "must_include_spot_ids": ["b"],
    }).json()

    assert body["status"] == "error"
    assert "休業日" in body["ai_hint"]
