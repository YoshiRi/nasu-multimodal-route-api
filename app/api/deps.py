from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.gtfs_engine import GTFSEngine


@lru_cache
def _load_engine(gtfs_dir: str) -> GTFSEngine:
    return GTFSEngine(gtfs_dir)


def get_gtfs_engine(settings: Settings = Depends(get_settings)) -> GTFSEngine:
    return _load_engine(str(settings.gtfs_dir))
