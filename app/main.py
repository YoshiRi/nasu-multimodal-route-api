from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.non_transit import router as non_transit_router
from app.api.plan import router as plan_router
from app.api.route_plan import router as route_plan_router
from app.api.spots import router as spots_router
from app.api.status import router as status_router
from app.api.suggest import router as suggest_router
from app.api.transit import router as transit_router
from app.core.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

TAGS_METADATA = [
    {
        "name": "transit",
        "description": "GTFS-backed bus stop and direct-trip search.",
    },
    {
        "name": "non-transit",
        "description": "Walking/taxi point-to-point estimates via OpenRouteService.",
    },
    {
        "name": "plan",
        "description": "Batch endpoints for AI agents to build multi-stop travel plans.",
    },
    {
        "name": "spots",
        "description": "Spot (tourist location) master data: list and add.",
    },
    {
        "name": "status",
        "description": "Which timetables are loaded and whether they are usable.",
    },
]

app = FastAPI(
    title="Nasu Multimodal Route API",
    description="PoC multimodal (bus/walking/taxi) route search API for the Nasu area.",
    version="0.4.0",
    openapi_tags=TAGS_METADATA,
)

app.include_router(transit_router)
app.include_router(non_transit_router)
app.include_router(plan_router)
app.include_router(route_plan_router)
app.include_router(suggest_router)
app.include_router(spots_router)
app.include_router(status_router)

app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


if get_settings().using_fixture_gtfs:
    logging.getLogger("uvicorn.error").warning(
        "No GTFS feeds under data/gtfs/ — running on the tiny test feed, so "
        "plans for real Nasu spots will come back empty. Run "
        "scripts/download_gtfs.py (see README) and restart."
    )


@app.get("/health", summary="Liveness check")
def health() -> dict[str, str]:
    return {"status": "ok"}
