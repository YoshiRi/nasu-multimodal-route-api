from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.gtfs_engine import GTFSEngine


def _feeds_cache_key(feeds: dict[str, Path]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((name, str(path)) for name, path in feeds.items()))


@lru_cache
def _load_engine(cache_key: tuple[tuple[str, str], ...]) -> GTFSEngine:
    feeds = dict(cache_key)
    if len(feeds) == 1:
        ((_, only_dir),) = feeds.items()
        return GTFSEngine(only_dir)
    return GTFSEngine.from_feeds(feeds)


def get_gtfs_engine(settings: Settings = Depends(get_settings)) -> GTFSEngine:
    return _load_engine(_feeds_cache_key(settings.gtfs_feeds))
