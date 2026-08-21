"""Download and extract GTFS feeds into data/gtfs/.

Public feeds (nasushiobara, nasutown) have a fixed, non-secret URL baked
into FEEDS below and need no configuration. Feeds gated behind an API key
(kanto_auto) build their URL from a public template plus a secret read from
an environment variable (see .env.example) — the secret itself never
appears in source. Any feed's URL can still be overridden via its
<SLUG>_GTFS_URL environment variable (e.g. to point at a local test
server). A feed is skipped if it ends up with no URL (missing API key and
no override). Each successful download records its GTFS `feed_info.txt`
validity period and a checksum in data/gtfs_manifest.json (committed to
git, unlike the GTFS data itself), so `--status` can report when each
source was last refreshed and whether it is due for an update.

Usage:
    python scripts/download_gtfs.py
    python scripts/download_gtfs.py --feed nasushiobara
    python scripts/download_gtfs.py --status
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("download_gtfs")
# httpx logs each request's full URL at INFO level, which would print the
# API key embedded in kanto_auto's URL to the console/log output.
logging.getLogger("httpx").setLevel(logging.WARNING)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "data" / "gtfs_manifest.json"
STALE_WARNING_DAYS = 30


@dataclass(frozen=True)
class FeedConfig:
    slug: str
    dest_dir: Path
    url_env_var: str
    default_url: str | None = None
    api_key_env_var: str | None = None

    def resolve_url(self) -> str | None:
        override = os.environ.get(self.url_env_var)
        if override:
            return override

        if self.default_url is None:
            return None
        if self.api_key_env_var is None:
            return self.default_url

        api_key = os.environ.get(self.api_key_env_var)
        return self.default_url.format(api_key=api_key) if api_key else None


FEEDS: list[FeedConfig] = [
    FeedConfig(
        slug="nasushiobara",
        dest_dir=REPO_ROOT / "data" / "gtfs" / "nasushiobara",
        default_url="https://api.gtfs-data.jp/v2/organizations/nasushiobaracity/feeds/chiikibasu/files/feed.zip",
        url_env_var="NASUSHIOBARA_GTFS_URL",
    ),
    FeedConfig(
        slug="nasutown",
        dest_dir=REPO_ROOT / "data" / "gtfs" / "nasutown",
        default_url="https://api.gtfs-data.jp/v2/organizations/nasutown/feeds/nasutown/files/feed.zip",
        url_env_var="NASUTOWN_GTFS_URL",
    ),
    FeedConfig(
        slug="kanto_auto",
        dest_dir=REPO_ROOT / "data" / "gtfs" / "kanto_auto",
        # agency_id 0904 = 関東自動車, found via POST https://www.ptd-hs.jp/GetAgencyList
        default_url="https://www.ptd-hs.jp/GetData?agency_id=0904&uid={api_key}",
        url_env_var="KANTO_BUS_GTFS_URL",
        api_key_env_var="KANTO_BUS_GTFS_API_KEY",
    ),
]


def download_zip(
    url: str,
    timeout: float = 60.0,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Download url to a temp file and return its path."""

    fd, tmp_path_str = tempfile.mkstemp(suffix=".zip")
    tmp_path = Path(tmp_path_str)
    try:
        with (
            httpx.Client(transport=transport, timeout=timeout, follow_redirects=True) as client,
            client.stream("GET", url) as response,
            os.fdopen(fd, "wb") as tmp_file,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes():
                tmp_file.write(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def extract_gtfs_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract zip_path into dest_dir, replacing any existing contents.

    Rejects the archive outright if any entry would resolve outside
    dest_dir (Zip Slip), before touching the filesystem.
    """

    dest_dir_resolved = dest_dir.resolve()

    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = (dest_dir_resolved / member.filename).resolve()
            if member_path != dest_dir_resolved and dest_dir_resolved not in member_path.parents:
                raise ValueError(f"Unsafe path in GTFS zip, aborting: {member.filename}")

        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        archive.extractall(dest_dir)


def read_feed_info(dest_dir: Path) -> dict[str, str | None]:
    """Read feed_start_date/feed_end_date/feed_version from feed_info.txt."""

    feed_info_path = dest_dir / "feed_info.txt"
    if not feed_info_path.exists():
        return {"feed_start_date": None, "feed_end_date": None, "feed_version": None}

    with feed_info_path.open(newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle), {})

    return {
        "feed_start_date": row.get("feed_start_date") or None,
        "feed_end_date": row.get("feed_end_date") or None,
        "feed_version": row.get("feed_version") or None,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict[str, dict[str, str | None]]:
    if not MANIFEST_PATH.exists():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, dict[str, str | None]]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def update_feed(
    feed: FeedConfig,
    manifest: dict[str, dict[str, str | None]],
    transport: httpx.BaseTransport | None = None,
) -> bool:
    url = feed.resolve_url()
    if not url:
        reason = (
            f"{feed.api_key_env_var} is not set"
            if feed.api_key_env_var
            else f"{feed.url_env_var} is not set and no default URL is configured"
        )
        logger.info("Skipping %s: %s", feed.slug, reason)
        return True

    logger.info("Downloading %s", feed.slug)
    try:
        zip_path = download_zip(url, transport=transport)
    except Exception as exc:
        logger.error("Failed to download %s: %s", feed.slug, exc)
        return False

    try:
        checksum = sha256_file(zip_path)
        extract_gtfs_zip(zip_path, feed.dest_dir)
    except Exception as exc:
        logger.error("Failed to extract %s: %s", feed.slug, exc)
        return False
    finally:
        zip_path.unlink(missing_ok=True)

    manifest[feed.slug] = {
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": checksum,
        **read_feed_info(feed.dest_dir),
    }

    logger.info("Updated %s -> %s", feed.slug, feed.dest_dir)
    return True


def print_status() -> int:
    manifest = load_manifest()
    today = date.today()

    header = f"{'slug':<14}{'downloaded_at':<27}{'feed_end_date':<16}status"
    print(header)
    print("-" * len(header))

    for feed in FEEDS:
        entry = manifest.get(feed.slug)
        if entry is None:
            print(f"{feed.slug:<14}{'-':<27}{'-':<16}not downloaded yet")
            continue

        feed_end_date = entry.get("feed_end_date")
        status = "unknown (no feed_info.txt)"
        if feed_end_date:
            days_left = (datetime.strptime(feed_end_date, "%Y%m%d").date() - today).days
            if days_left < 0:
                status = "EXPIRED (feed_end_date passed)"
            elif days_left <= STALE_WARNING_DAYS:
                status = f"update soon ({days_left}d left)"
            else:
                status = "ok"

        downloaded_at = str(entry.get("downloaded_at") or "-")
        print(f"{feed.slug:<14}{downloaded_at:<27}{feed_end_date or '-':<16}{status}")

    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Download and extract GTFS feeds configured via .env.",
    )
    parser.add_argument(
        "--feed",
        choices=[feed.slug for feed in FEEDS],
        help="Only update this feed (default: all configured feeds).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print each feed's last download time and freshness, then exit.",
    )
    args = parser.parse_args(argv)

    if args.status:
        return print_status()

    feeds = [feed for feed in FEEDS if args.feed is None or feed.slug == args.feed]
    configured_feeds = [feed for feed in feeds if feed.resolve_url()]

    if not configured_feeds:
        logger.warning(
            "No feed has a resolvable URL. Missing API keys? See .env.example."
        )
        return 1

    manifest = load_manifest()
    results = [update_feed(feed, manifest) for feed in feeds]
    save_manifest(manifest)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
