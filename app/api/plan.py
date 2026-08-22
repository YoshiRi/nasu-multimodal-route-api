from __future__ import annotations

from itertools import permutations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_gtfs_engine
from app.models.errors import ErrorResponse
from app.models.plan import GraphDataRequest, GraphDataResponse, GraphEdge
from app.services.gtfs_engine import GTFSEngine

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post(
    "/graph-data",
    response_model=GraphDataResponse,
    summary="Get bus travel costs between every pair of stops",
    description=(
        "Compute direct bus options for every ordered pair of the given "
        "stop_ids in one call, so an AI agent can build a travel-cost "
        "graph without issuing one /transit/routes request per pair. "
        "Walking/taxi edges are not included yet (bus only)."
    ),
    responses={400: {"model": ErrorResponse, "description": "Invalid request."}},
)
def get_graph_data(
    request: GraphDataRequest,
    engine: GTFSEngine = Depends(get_gtfs_engine),
) -> GraphDataResponse:
    try:
        edges = [
            GraphEdge(
                from_stop_id=origin,
                to_stop_id=destination,
                bus_options=engine.search_next_bus(
                    origin_stop_id=origin,
                    dest_stop_id=destination,
                    departure_time=request.departure_time,
                    departure_date=request.departure_date,
                ),
            )
            for origin, destination in permutations(request.stop_ids, 2)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return GraphDataResponse(edges=edges)
