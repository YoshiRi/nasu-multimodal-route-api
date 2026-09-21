"""Download and extract this app's GTFS feeds into data/gtfs/.

Feed definitions live in app/gtfs_feeds.py; the download, extraction and
freshness-tracking machinery comes from the gtfs-multifeed library. Each
successful download records its GTFS `feed_info.txt` validity period and a
checksum in data/gtfs_manifest.json (committed to git, unlike the GTFS data
itself), so `--status` can report when each source was last refreshed.

Usage:
    python scripts/download_gtfs.py
    python scripts/download_gtfs.py --feed nasushiobara
    python scripts/download_gtfs.py --status
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from gtfs_multifeed import run_cli

from app.gtfs_feeds import FEEDS, MANIFEST_PATH, REPO_ROOT

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
# httpx logs each request's full URL at INFO level, which would print the
# API key embedded in kanto_auto's URL to the console/log output.
logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    return run_cli(
        FEEDS,
        MANIFEST_PATH,
        argv,
        description="Download and extract the Nasu-area GTFS feeds.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
