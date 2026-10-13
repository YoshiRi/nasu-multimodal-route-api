from __future__ import annotations

from pathlib import Path

from gtfs_multifeed import GTFSEngine

from app.services.service_calendar import no_service_hint, partial_coverage_note

FIXTURES = Path(__file__).parent / "fixtures"
# minimal_gtfs runs Aug 2026 only; suggest_net runs all of 2026-2027.
AUGUST_ONLY = FIXTURES / "minimal_gtfs"
ALL_YEAR = FIXTURES / "suggest_net"


def _merged() -> GTFSEngine:
    return GTFSEngine.from_feeds({"kanto_auto": AUGUST_ONLY, "nasutown": ALL_YEAR})


def test_a_covered_running_day_needs_no_explanation() -> None:
    engine = _merged()

    assert no_service_hint(engine, "2026-08-17") is None
    assert partial_coverage_note(engine, "2026-08-17") is None


def test_a_day_past_every_timetable_is_named_as_such() -> None:
    hint = no_service_hint(_merged(), "2030-01-07")

    assert hint is not None
    assert "有効期間外" in hint
    # Both feeds are listed, by their human name, with their periods.
    assert "関東自動車: 2026-08-01〜2026-08-31" in hint
    assert "那須町: 2026-01-01〜2027-12-31" in hint


def test_one_operator_expiring_is_a_warning_not_a_failure() -> None:
    engine = _merged()

    # Only the August-only feed has lapsed; the other still runs buses.
    assert no_service_hint(engine, "2026-10-01") is None
    note = partial_coverage_note(engine, "2026-10-01")
    assert note is not None
    assert "関東自動車" in note
    assert "那須町" not in note


def test_a_covered_day_with_no_buses_is_not_called_out_of_range() -> None:
    # Saturday inside minimal_gtfs's period; its only service is weekday.
    hint = no_service_hint(GTFSEngine(AUGUST_ONLY), "2026-08-15")

    assert hint is not None
    assert "有効期間外" not in hint
    assert "運行がない" in hint


def test_coverage_note_leads_when_nothing_was_found() -> None:
    from app.services.service_calendar import with_note

    assert with_note("note", "hint") == "hint note"
    assert with_note("note", "hint", failed=True) == "note hint"
    assert with_note(None, "hint", failed=True) == "hint"
