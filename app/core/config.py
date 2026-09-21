from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from gtfs_multifeed import discover_gtfs_feeds

from app.gtfs_feeds import GTFS_DATA_ROOT

REPO_ROOT = Path(__file__).resolve().parents[2]

# PoC default: point at the repo's minimal GTFS fixture so the API runs
# out of the box before real GTFS data is available.
DEFAULT_GTFS_DIR = REPO_ROOT / "tests" / "fixtures" / "minimal_gtfs"

# PoC spot master (spot_id/name/lat/lng) used by /plan/route-plan. Override
# with SPOTS_FILE once a real spot dataset is available.
DEFAULT_SPOTS_FILE = REPO_ROOT / "data" / "spots" / "nasu_spots.json"


@dataclass(frozen=True)
class Settings:
    # feed_name -> GTFS directory. A single entry is loaded as-is
    # (unprefixed IDs); more than one is merged via GTFSEngine.from_feeds().
    gtfs_feeds: dict[str, Path]
    spots_file: Path


@lru_cache
def get_settings() -> Settings:
    override = os.environ.get("GTFS_DIR")
    gtfs_feeds = (
        {"default": Path(override)}
        if override
        else discover_gtfs_feeds(GTFS_DATA_ROOT) or {"default": DEFAULT_GTFS_DIR}
    )
    spots_file = Path(os.environ.get("SPOTS_FILE", str(DEFAULT_SPOTS_FILE)))

    return Settings(gtfs_feeds=gtfs_feeds, spots_file=spots_file)
