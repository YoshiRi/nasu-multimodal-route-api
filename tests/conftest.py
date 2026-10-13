from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from app.main import app


def settings_with(*, using_fixture_gtfs: bool) -> Settings:
    return Settings(
        gtfs_feeds={}, spots_file=Path("unused.json"), using_fixture_gtfs=using_fixture_gtfs
    )


@pytest.fixture(autouse=True)
def treat_test_gtfs_as_real_data():
    """Pin whether the server believes it is running on the test feed.

    Left to itself, get_settings() answers that by looking for data/gtfs/
    on disk, so a test asserting on an error hint would pass on a machine
    with the real feeds downloaded and fail in CI. API tests swap in their
    own engine anyway; from the server's point of view that is real data.
    """

    app.dependency_overrides[get_settings] = lambda: settings_with(using_fixture_gtfs=False)
    yield
    app.dependency_overrides.pop(get_settings, None)
