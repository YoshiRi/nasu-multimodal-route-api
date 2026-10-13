from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.api.status import FeedStatus, data_warnings
from app.core.config import get_settings
from app.main import app
from tests.conftest import settings_with


def _feed(end: date) -> FeedStatus:
    return FeedStatus(name="nasutown", label="那須町", start_date=date(2026, 4, 1), end_date=end)


def test_status_lists_loaded_feeds() -> None:
    body = TestClient(app).get("/status").json()

    assert body["using_fixture_gtfs"] is False
    assert body["feeds"]
    assert {"name", "label", "start_date", "end_date"} <= set(body["feeds"][0])


def test_status_warns_when_running_on_the_test_feed() -> None:
    app.dependency_overrides[get_settings] = lambda: settings_with(using_fixture_gtfs=True)

    body = TestClient(app).get("/status").json()

    assert body["using_fixture_gtfs"] is True
    assert any("download_gtfs.py" in warning for warning in body["warnings"])


def test_healthy_feed_raises_no_warning() -> None:
    assert data_warnings([_feed(date(2027, 3, 31))], False, date(2026, 10, 13)) == []


def test_feed_close_to_expiry_is_flagged_before_it_lapses() -> None:
    [warning] = data_warnings([_feed(date(2026, 11, 1))], False, date(2026, 10, 13))

    assert "那須町" in warning and "2026-11-01" in warning


def test_expired_feed_says_to_update() -> None:
    [warning] = data_warnings([_feed(date(2026, 10, 1))], False, date(2026, 10, 13))

    assert "期限切れ" in warning
