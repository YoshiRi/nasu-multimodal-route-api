from __future__ import annotations

from fastapi import FastAPI

from app.api.plan import router as plan_router
from app.api.transit import router as transit_router

TAGS_METADATA = [
    {
        "name": "transit",
        "description": "GTFS-backed bus stop and direct-trip search.",
    },
    {
        "name": "plan",
        "description": "Batch endpoints for AI agents to build multi-stop travel plans.",
    },
]

app = FastAPI(
    title="Nasu Multimodal Route API",
    description=(
        "PoC bus route search API for the Nasu area, backed by GTFS data. "
        "Walking/taxi routing is out of scope for this phase."
    ),
    version="0.2.0",
    openapi_tags=TAGS_METADATA,
)

app.include_router(transit_router)
app.include_router(plan_router)


@app.get("/health", summary="Liveness check")
def health() -> dict[str, str]:
    return {"status": "ok"}
