from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_spot_repository
from app.models.spot import CreateSpotRequest, SpotResponse
from app.services.spot_repository import SpotRepository

router = APIRouter(prefix="/spots", tags=["spots"])


@router.get(
    "",
    response_model=list[SpotResponse],
    summary="List all spots",
    description="Return every spot in the currently loaded spot master.",
)
def list_spots(spots: SpotRepository = Depends(get_spot_repository)) -> list[SpotResponse]:
    return [SpotResponse(**vars(spot)) for spot in spots.list_all()]


@router.post(
    "",
    response_model=SpotResponse,
    summary="Add a new spot",
    description=(
        "Add a spot to the spot master (spot_id is generated) and persist "
        "it to disk immediately; subsequent /plan/route-plan calls can "
        "reference it right away."
    ),
)
def create_spot(
    request: CreateSpotRequest,
    spots: SpotRepository = Depends(get_spot_repository),
) -> SpotResponse:
    spot = spots.add(name=request.name, lat=request.lat, lng=request.lng)
    return SpotResponse(**vars(spot))
