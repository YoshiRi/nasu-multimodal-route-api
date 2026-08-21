from __future__ import annotations

from pathlib import Path

from app.core.config import discover_gtfs_feeds

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"
OTHER_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs_other"


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
