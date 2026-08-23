from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.models.errors import ErrorResponse
from app.models.non_transit import NonTransitRouteResponse
from app.services.ors_client import OrsRateLimitError, OrsRequestError, get_driving_route, get_walking_route
from app.services.taxi_fare import estimate_fare_jpy

router = APIRouter(prefix="/non-transit", tags=["non-transit"])


@router.get(
    "/route",
    response_model=NonTransitRouteResponse,
    summary="Estimate a walking or taxi route between two coordinates",
    description=(
        "Calls OpenRouteService for the route estimate. mode='taxi' adds "
        "a rough distance-based cost_jpy estimate; mode='walking' leaves "
        "cost_jpy null."
    ),
    responses={
        429: {"model": ErrorResponse, "description": "Local ORS call budget exceeded, retry shortly."},
        503: {"model": ErrorResponse, "description": "OpenRouteService request failed."},
    },
)
async def get_non_transit_route(
    origin_lat: float = Query(description="Origin latitude.", examples=[37.0950]),
    origin_lng: float = Query(description="Origin longitude.", examples=[140.0010]),
    dest_lat: float = Query(description="Destination latitude.", examples=[37.1240]),
    dest_lng: float = Query(description="Destination longitude.", examples=[139.9850]),
    mode: Literal["walking", "taxi"] = Query(description="Transport mode."),
) -> NonTransitRouteResponse:
    try:
        if mode == "walking":
            route = await get_walking_route(origin_lat, origin_lng, dest_lat, dest_lng)
            cost_jpy = None
        else:
            route = await get_driving_route(origin_lat, origin_lng, dest_lat, dest_lng)
            cost_jpy = estimate_fare_jpy(route.distance_m)
    except OrsRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except OrsRequestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return NonTransitRouteResponse(
        mode=mode,
        distance_m=route.distance_m,
        duration_min=route.duration_min,
        cost_jpy=cost_jpy,
    )
