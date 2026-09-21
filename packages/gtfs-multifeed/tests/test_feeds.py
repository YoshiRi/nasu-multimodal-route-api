from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from gtfs_multifeed import (
    FeedConfig,
    download_zip,
    extract_gtfs_zip,
    load_manifest,
    print_status,
    read_feed_info,
    save_manifest,
    update_feed,
)


def _make_zip(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def test_extract_gtfs_zip_extracts_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "feed.zip"
    _make_zip(zip_path, {"stops.txt": "stop_id,stop_name\n1,Test\n"})
    dest_dir = tmp_path / "dest"

    extract_gtfs_zip(zip_path, dest_dir)

    assert (dest_dir / "stops.txt").read_text() == "stop_id,stop_name\n1,Test\n"


def test_extract_gtfs_zip_replaces_stale_files(tmp_path: Path) -> None:
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "old.txt").write_text("stale")

    zip_path = tmp_path / "feed.zip"
    _make_zip(zip_path, {"stops.txt": "stop_id,stop_name\n"})

    extract_gtfs_zip(zip_path, dest_dir)

    assert not (dest_dir / "old.txt").exists()
    assert (dest_dir / "stops.txt").exists()


def test_extract_gtfs_zip_rejects_zip_slip(tmp_path: Path) -> None:
    zip_path = tmp_path / "evil.zip"
    _make_zip(zip_path, {"../evil.txt": "pwned"})
    dest_dir = tmp_path / "dest"

    with pytest.raises(ValueError, match="Unsafe path"):
        extract_gtfs_zip(zip_path, dest_dir)

    assert not (tmp_path / "evil.txt").exists()


def test_download_zip_writes_response_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04fake-zip-bytes")

    zip_path = download_zip("https://example.invalid/feed.zip", transport=httpx.MockTransport(handler))
    try:
        assert zip_path.read_bytes() == b"PK\x03\x04fake-zip-bytes"
    finally:
        zip_path.unlink()


def test_update_feed_skips_when_no_url_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TEST_FEED_URL", raising=False)
    feed = FeedConfig(slug="test", url_env_var="TEST_FEED_URL", dest_dir=tmp_path / "dest")

    assert update_feed(feed, {}) is True
    assert not feed.dest_dir.exists()


def test_resolve_url_uses_default_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FEED_URL", raising=False)
    feed = FeedConfig(
        slug="test",
        url_env_var="TEST_FEED_URL",
        dest_dir=Path("unused"),
        default_url="https://example.invalid/public-feed.zip",
    )

    assert feed.resolve_url() == "https://example.invalid/public-feed.zip"


def test_resolve_url_env_override_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_FEED_URL", "https://example.invalid/override.zip")
    feed = FeedConfig(
        slug="test",
        url_env_var="TEST_FEED_URL",
        dest_dir=Path("unused"),
        default_url="https://example.invalid/public-feed.zip",
    )

    assert feed.resolve_url() == "https://example.invalid/override.zip"


def test_resolve_url_builds_from_api_key_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FEED_URL", raising=False)
    monkeypatch.setenv("TEST_FEED_API_KEY", "secret123")
    feed = FeedConfig(
        slug="test",
        url_env_var="TEST_FEED_URL",
        dest_dir=Path("unused"),
        default_url="https://example.invalid/GetData?uid={api_key}",
        api_key_env_var="TEST_FEED_API_KEY",
    )

    assert feed.resolve_url() == "https://example.invalid/GetData?uid=secret123"


def test_resolve_url_none_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FEED_URL", raising=False)
    monkeypatch.delenv("TEST_FEED_API_KEY", raising=False)
    feed = FeedConfig(
        slug="test",
        url_env_var="TEST_FEED_URL",
        dest_dir=Path("unused"),
        default_url="https://example.invalid/GetData?uid={api_key}",
        api_key_env_var="TEST_FEED_API_KEY",
    )

    assert feed.resolve_url() is None


def test_read_feed_info_parses_fields(tmp_path: Path) -> None:
    dest_dir = tmp_path / "feed"
    dest_dir.mkdir()
    (dest_dir / "feed_info.txt").write_text(
        "feed_publisher_name,feed_lang,feed_start_date,feed_end_date,feed_version\n"
        "Test,ja,20260401,20270331,v1\n",
        encoding="utf-8",
    )

    assert read_feed_info(dest_dir) == {
        "feed_start_date": "20260401",
        "feed_end_date": "20270331",
        "feed_version": "v1",
    }


def test_read_feed_info_missing_file_returns_none_fields(tmp_path: Path) -> None:
    assert read_feed_info(tmp_path / "missing") == {
        "feed_start_date": None,
        "feed_end_date": None,
        "feed_version": None,
    }


def test_manifest_round_trip(tmp_path: Path) -> None:
    manifest_path = tmp_path / "gtfs_manifest.json"

    save_manifest(manifest_path, {"some_feed": {"downloaded_at": "2026-08-20T00:00:00+00:00"}})

    assert load_manifest(manifest_path) == {
        "some_feed": {"downloaded_at": "2026-08-20T00:00:00+00:00"}
    }


def test_update_feed_records_manifest_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TEST_FEED_URL", "https://example.invalid/feed.zip")
    feed = FeedConfig(slug="test", url_env_var="TEST_FEED_URL", dest_dir=tmp_path / "dest")

    def handler(request: httpx.Request) -> httpx.Response:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("stops.txt", "stop_id\n")
            archive.writestr(
                "feed_info.txt",
                "feed_publisher_name,feed_lang,feed_start_date,feed_end_date,feed_version\n"
                "Test,ja,20260401,20270331,v1\n",
            )
        return httpx.Response(200, content=buffer.getvalue())

    manifest: dict[str, dict[str, str | None]] = {}
    assert update_feed(feed, manifest, transport=httpx.MockTransport(handler)) is True

    entry = manifest["test"]
    assert entry["feed_start_date"] == "20260401"
    assert entry["feed_end_date"] == "20270331"
    assert entry["feed_version"] == "v1"
    assert "downloaded_at" in entry
    assert "sha256" in entry
    assert (feed.dest_dir / "stops.txt").exists()


def test_print_status_reports_expired_and_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = tmp_path / "gtfs_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "downloaded_feed": {
                    "downloaded_at": "2020-01-01T00:00:00+00:00",
                    "feed_start_date": "20190401",
                    "feed_end_date": "20200101",
                    "feed_version": "old",
                    "sha256": "deadbeef",
                }
            }
        ),
        encoding="utf-8",
    )
    feeds = [
        FeedConfig(
            slug="downloaded_feed",
            url_env_var="DOWNLOADED_FEED_URL",
            dest_dir=tmp_path / "downloaded",
        ),
        FeedConfig(
            slug="missing_feed",
            url_env_var="MISSING_FEED_URL",
            dest_dir=tmp_path / "missing",
        ),
    ]

    assert print_status(feeds, manifest_path) == 0

    out = capsys.readouterr().out
    assert "downloaded_feed" in out
    assert "EXPIRED" in out
    assert "not downloaded yet" in out
