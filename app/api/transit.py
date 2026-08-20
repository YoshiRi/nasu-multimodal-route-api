from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_gtfs_engine
from app.models.transit import BusTripOption, NearbyStop
from app.services.gtfs_engine import GTFSEngine

router = APIRouter(prefix="/transit", tags=["transit"])


@router.get("/stops/nearby", response_model=list[NearbyStop])
def get_nearby_stops(
    lat: float = Query(description="Query point latitude."),
    lng: float = Query(description="Query point longitude."),
    radius_m: int = Query(500, gt=0, description="Search radius in meters."),
    engine: GTFSEngine = Depends(get_gtfs_engine),
) -> list[dict[str, str | float]]:
    return engine.nearby_stops(lat=lat, lng=lng, radius_m=radius_m)


@router.get("/routes", response_model=list[BusTripOption])
def get_transit_routes(
    origin_stop_id: str = Query(description="Origin GTFS stop_id."),
    destination_stop_id: str = Query(description="Destination GTFS stop_id."),
    departure_time: str = Query(description='Departure time, "HH:MM" or "HH:MM:SS".'),
    departure_date: date | None = Query(
        None, description="Optional service date used for calendar filtering."
    ),
    engine: GTFSEngine = Depends(get_gtfs_engine),
) -> list[dict[str, str | int | None]]:
    try:
        return engine.search_next_bus(
            origin_stop_id=origin_stop_id,
            dest_stop_id=destination_stop_id,
            departure_time=departure_time,
            departure_date=departure_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
