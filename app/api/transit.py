from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_gtfs_engine
from app.models.errors import ErrorResponse
from app.models.network import NetworkResponse
from app.models.transit import BusTripOption, NearbyStop
from app.services.gtfs_engine import GTFSEngine

router = APIRouter(prefix="/transit", tags=["transit"])


@router.get(
    "/network",
    response_model=NetworkResponse,
    summary="Get the full currently-loaded stop and route-shape network",
    description=(
        "Return every boardable stop and every route shape (polyline) in "
        "the GTFS data currently loaded by the server, for visualizing "
        "network coverage on a map."
    ),
)
def get_transit_network(engine: GTFSEngine = Depends(get_gtfs_engine)) -> dict[str, list]:
    stops = [
        {
            "stop_id": str(row.stop_id),
            "stop_name": str(row.stop_name),
            "stop_lat": float(row.stop_lat),
            "stop_lon": float(row.stop_lon),
        }
        for row in engine.stops.itertuples(index=False)
    ]
    return {"stops": stops, "shapes": engine.route_shapes()}


@router.get(
    "/stops/nearby",
    response_model=list[NearbyStop],
    summary="Find GTFS stops near a coordinate",
    description="Return GTFS stops within radius_m of (lat, lng), nearest first.",
)
def get_nearby_stops(
    lat: float = Query(description="Query point latitude.", examples=[36.8847]),
    lng: float = Query(description="Query point longitude.", examples=[139.9866]),
    radius_m: int = Query(500, gt=0, description="Search radius in meters."),
    engine: GTFSEngine = Depends(get_gtfs_engine),
) -> list[dict[str, str | float]]:
    return engine.nearby_stops(lat=lat, lng=lng, radius_m=radius_m)


@router.get(
    "/routes",
    response_model=list[BusTripOption],
    summary="Search direct bus trips between two stops",
    description=(
        "Return up to 3 direct (no-transfer) bus trips departing "
        "origin_stop_id at or after departure_time, earliest first. "
        "Empty list if none exist."
    ),
    responses={400: {"model": ErrorResponse, "description": "Invalid request."}},
)
def get_transit_routes(
    origin_stop_id: str = Query(description="Origin GTFS stop_id.", examples=["nasushiobara:1_01"]),
    destination_stop_id: str = Query(
        description="Destination GTFS stop_id.", examples=["nasushiobara:112_01"]
    ),
    departure_time: str = Query(
        description='Departure time, "HH:MM" or GTFS "HH:MM:SS".', examples=["10:00"]
    ),
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
