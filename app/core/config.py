from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# PoC default: point at the repo's minimal GTFS fixture so the API runs
# out of the box before real GTFS data is available. Override with GTFS_DIR
# once real data is placed under data/gtfs/.
DEFAULT_GTFS_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "minimal_gtfs"
)


@dataclass(frozen=True)
class Settings:
    gtfs_dir: Path


@lru_cache
def get_settings() -> Settings:
    return Settings(gtfs_dir=Path(os.environ.get("GTFS_DIR", str(DEFAULT_GTFS_DIR))))
