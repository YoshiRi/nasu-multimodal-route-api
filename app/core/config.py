from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.gtfs_engine import REQUIRED_GTFS_FILES

REPO_ROOT = Path(__file__).resolve().parents[2]

# PoC default: point at the repo's minimal GTFS fixture so the API runs
# out of the box before real GTFS data is available.
DEFAULT_GTFS_DIR = REPO_ROOT / "tests" / "fixtures" / "minimal_gtfs"

# scripts/download_gtfs.py extracts each feed into its own subdirectory
# here. Any subdirectory found is loaded automatically (merged via
# GTFSEngine.from_feeds() when there is more than one), so the API picks
# up real data as soon as it's downloaded, with no extra configuration.
GTFS_DATA_ROOT = REPO_ROOT / "data" / "gtfs"

# PoC spot master (spot_id/name/lat/lng) used by /plan/route-plan. Override
# with SPOTS_FILE once a real spot dataset is available.
DEFAULT_SPOTS_FILE = REPO_ROOT / "data" / "spots" / "nasu_spots.json"


@dataclass(frozen=True)
class Settings:
    # feed_name -> GTFS directory. A single entry is loaded as-is
    # (unprefixed IDs); more than one is merged via GTFSEngine.from_feeds().
    gtfs_feeds: dict[str, Path]
    spots_file: Path


def discover_gtfs_feeds(data_root: Path = GTFS_DATA_ROOT) -> dict[str, Path]:
    """Return {subdirectory name: path} for every valid GTFS feed under data_root."""

    if not data_root.is_dir():
        return {}

    return {
        entry.name: entry
        for entry in sorted(data_root.iterdir())
        if entry.is_dir()
        and all((entry / file_name).exists() for file_name in REQUIRED_GTFS_FILES.values())
    }


@lru_cache
def get_settings() -> Settings:
    override = os.environ.get("GTFS_DIR")
    gtfs_feeds = (
        {"default": Path(override)} if override else discover_gtfs_feeds() or {"default": DEFAULT_GTFS_DIR}
    )
    spots_file = Path(os.environ.get("SPOTS_FILE", str(DEFAULT_SPOTS_FILE)))

    return Settings(gtfs_feeds=gtfs_feeds, spots_file=spots_file)
