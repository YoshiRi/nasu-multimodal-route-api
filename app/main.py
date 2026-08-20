from __future__ import annotations

from fastapi import FastAPI

from app.api.transit import router as transit_router

app = FastAPI(
    title="Nasu Multimodal Route API",
    description=(
        "PoC bus route search API for the Nasu area, backed by GTFS data. "
        "Walking/taxi routing is out of scope for this phase."
    ),
)

app.include_router(transit_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
