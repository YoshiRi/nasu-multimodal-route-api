from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from gtfs_multifeed import GTFSEngine, parse_gtfs_time_to_minutes

from app.api.deps import get_gtfs_engine, get_spot_repository
from app.models.errors import ErrorResponse
from app.models.suggest import (
    SuggestedItinerary,
    SuggestedStopResponse,
    SuggestRequest,
    SuggestResponse,
)
from app.services.spot_repository import SpotRepository
from app.services.suggester import (
    Candidate,
    SuggestInputError,
    describe,
    suggest_itineraries,
)
from app.services.travel_estimate import format_minutes

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post(
    "/suggest",
    response_model=SuggestResponse,
    summary="Suggest which spots to visit between a start and a destination",
    description=(
        "Answers the question before /plan/route-plan: given only where "
        "the trip starts, where it must end, and by when, propose several "
        "sets of spots that fit — optionally narrowed by tags such as 牧場 "
        "or 登山, or pinned with must_include_spot_ids. Candidates differ "
        "in which spots they include, so an agent can lay them out for a "
        "person to choose between.\n\n"
        "This is deliberately cheap: it estimates walking times instead of "
        "routing them, so it returns quickly and spends no routing-service "
        "quota. Take the chosen candidate's spot_ids, append "
        "destination_spot_id, and pass them to POST /plan/route-plan to "
        "get the real itinerary.\n\n"
        "A well-formed request always returns HTTP 200: status='error' "
        "with an ai_hint when nothing fits. Malformed requests (unknown "
        "spot_id, bad time format) return HTTP 4xx."
    ),
    responses={400: {"model": ErrorResponse, "description": "Invalid request."}},
)
def post_suggest(
    request: SuggestRequest,
    engine: GTFSEngine = Depends(get_gtfs_engine),
    spots: SpotRepository = Depends(get_spot_repository),
) -> SuggestResponse:
    try:
        candidates = suggest_itineraries(
            spots=spots,
            engine=engine,
            base_location_id=request.base_location_id,
            destination_spot_id=request.destination_spot_id,
            departure_time=request.departure_time,
            departure_date=request.departure_date,
            arrive_by=request.arrive_by,
            spot_count=request.spot_count,
            tags=request.tags,
            must_include_spot_ids=request.must_include_spot_ids,
            dwell_minutes=request.dwell_minutes,
            max_candidates=request.max_candidates,
            max_transfers=request.max_transfers,
        )
    except SuggestInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    destination = spots.get(request.destination_spot_id)
    if not candidates:
        return SuggestResponse(
            status="error",
            ai_hint=(
                f"{request.arrive_by}までに{destination.name}へ戻れる行程が"
                "見つかりませんでした。訪問地の数を減らすか、滞在時間を短くするか、"
                "出発を早めるか、タグの条件を広げるようユーザーに提案してください。"
            ),
            candidates=[],
        )

    summaries = "／".join(describe(candidate, destination) for candidate in candidates)
    return SuggestResponse(
        status="success",
        ai_hint=(
            f"{len(candidates)}件の候補が見つかりました: {summaries}。"
            "時刻は概算です。ユーザーに違いを示して選んでもらい、選ばれた候補の "
            "spot_ids に destination_spot_id を足して POST /plan/route-plan "
            "を呼ぶと、実際の経路と正確な時刻が入った行程になります。"
        ),
        candidates=[
            _to_response(candidate, request.departure_time) for candidate in candidates
        ],
    )


def _to_response(candidate: Candidate, departure_time: str) -> SuggestedItinerary:
    return SuggestedItinerary(
        spot_ids=[stop.spot.spot_id for stop in candidate.stops],
        stops=[
            SuggestedStopResponse(
                spot_id=stop.spot.spot_id,
                name=stop.spot.name,
                lat=stop.spot.lat,
                lng=stop.spot.lng,
                tags=list(stop.spot.tags),
                arrival_time=format_minutes(stop.arrival_minutes),
                departure_time=format_minutes(stop.departure_minutes),
            )
            for stop in candidate.stops
        ],
        tags=candidate.tags,
        destination_arrival_time=format_minutes(candidate.destination_arrival_minutes),
        total_duration_min=(
            candidate.destination_arrival_minutes
            - parse_gtfs_time_to_minutes(departure_time)
        ),
    )
