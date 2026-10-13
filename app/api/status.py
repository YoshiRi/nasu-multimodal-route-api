from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from gtfs_multifeed import GTFSEngine
from pydantic import BaseModel

from app.api.deps import get_gtfs_engine
from app.core.config import Settings, get_settings
from app.gtfs_feeds import FEED_LABELS
from app.services.service_calendar import FIXTURE_GTFS_HINT

router = APIRouter(tags=["status"])

# Warn this far ahead of a feed lapsing: trips get planned weeks in advance.
EXPIRY_WARNING_DAYS = 30


class FeedStatus(BaseModel):
    name: str
    label: str
    start_date: date | None
    end_date: date | None


class DataStatus(BaseModel):
    using_fixture_gtfs: bool
    feeds: list[FeedStatus]
    warnings: list[str]


@router.get(
    "/status",
    response_model=DataStatus,
    summary="Which timetables are loaded, and what's wrong with them",
    description=(
        "Check this first when plans unexpectedly come back empty. "
        "warnings is empty when the data is healthy; otherwise each entry "
        "is a sentence saying what is wrong and how to fix it."
    ),
)
def get_status(
    engine: GTFSEngine = Depends(get_gtfs_engine),
    settings: Settings = Depends(get_settings),
) -> DataStatus:
    feeds = [
        FeedStatus(
            name=name,
            label=FEED_LABELS.get(name, name),
            start_date=period[0] if period else None,
            end_date=period[1] if period else None,
        )
        for name, period in engine.service_periods().items()
    ]
    return DataStatus(
        using_fixture_gtfs=settings.using_fixture_gtfs,
        feeds=feeds,
        warnings=data_warnings(feeds, settings.using_fixture_gtfs, date.today()),
    )


def data_warnings(feeds: list[FeedStatus], using_fixture_gtfs: bool, today: date) -> list[str]:
    if using_fixture_gtfs:
        return [FIXTURE_GTFS_HINT]

    warnings = []
    for feed in feeds:
        if feed.end_date is None:
            continue
        if feed.end_date < today:
            warnings.append(
                f"{feed.label}の時刻表は{feed.end_date}で期限切れです。"
                "scripts/download_gtfs.py で更新してください。"
            )
        elif feed.end_date < today + timedelta(days=EXPIRY_WARNING_DAYS):
            warnings.append(
                f"{feed.label}の時刻表は{feed.end_date}までです。"
                "それ以降の日付ではこの事業者のバスが検索に含まれません。"
            )
    return warnings
