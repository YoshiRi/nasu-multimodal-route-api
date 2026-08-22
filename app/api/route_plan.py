from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_gtfs_engine, get_spot_repository
from app.models.errors import ErrorResponse
from app.models.route_plan import RoutePlanRequest, RoutePlanResponse
from app.services.gtfs_engine import GTFSEngine
from app.services.route_planner import RoutePlanInputError, build_route_plan
from app.services.spot_repository import SpotRepository

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post(
    "/route-plan",
    response_model=RoutePlanResponse,
    summary="Build a bus + walking itinerary for an AI travel concierge",
    description=(
        "Visits target_spot_ids in the given order, starting and ending at "
        "base_location_id, using direct bus trips (GTFSEngine) and "
        "OpenRouteService walking legs. A well-formed request always "
        "returns HTTP 200: status='success' with a plan, or status='error' "
        "with an ai_hint explaining why no plan was feasible and how to "
        "adjust the request. Malformed requests (unknown spot_id, bad time "
        "format) still return HTTP 4xx."
    ),
    responses={400: {"model": ErrorResponse, "description": "Invalid request."}},
)
async def post_route_plan(
    request: RoutePlanRequest,
    engine: GTFSEngine = Depends(get_gtfs_engine),
    spots: SpotRepository = Depends(get_spot_repository),
) -> RoutePlanResponse:
    try:
        return await build_route_plan(request, engine, spots)
    except RoutePlanInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
