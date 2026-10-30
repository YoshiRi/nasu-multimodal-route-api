from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from gtfs_multifeed import GTFSEngine, parse_gtfs_time_to_minutes, parse_service_date

from app.api.deps import get_gtfs_engine, get_spot_repository
from app.core.config import Settings, get_settings
from app.models.errors import ErrorResponse
from app.models.suggest import (
    SuggestedItinerary,
    SuggestedStopResponse,
    SuggestRequest,
    SuggestResponse,
)
from app.services.service_calendar import (
    FIXTURE_GTFS_HINT,
    no_service_hint,
    partial_coverage_note,
    with_note,
)
from app.services.spot_repository import SpotRepository
from app.services.suggester import (
    Candidate,
    RequiredSpotClosed,
    SuggestInputError,
    describe,
    estimate_taxi_fare_jpy,
    suggest_itineraries,
)
from app.services.taxi_fare import estimate_fare_jpy
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
    settings: Settings = Depends(get_settings),
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
            max_taxi_legs=request.max_taxi_legs,
        )
    except SuggestInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RequiredSpotClosed as exc:
        return SuggestResponse(
            status="error",
            ai_hint=f"{exc} 別の日付にするか、must_include_spot_ids から外すようユーザーに提案してください。",
            candidates=[],
        )

    base = spots.get(request.base_location_id)
    destination = spots.get(request.destination_spot_id)
    coverage_note = partial_coverage_note(engine, request.departure_date)
    if not candidates:
        if settings.using_fixture_gtfs:
            return SuggestResponse(
                status="error", ai_hint=FIXTURE_GTFS_HINT, candidates=[]
            )
        no_service = no_service_hint(engine, request.departure_date)
        if no_service:
            return SuggestResponse(status="error", ai_hint=no_service, candidates=[])
        return SuggestResponse(
            status="error",
            ai_hint=with_note(coverage_note, failed=True, hint=(
                f"{request.arrive_by}までに{destination.name}へ戻れる行程が"
                "見つかりませんでした。訪問地の数を減らすか、滞在時間を短くするか、"
                "出発を早めるか、タグの条件を広げるようユーザーに提案してください。"
                + ("" if request.max_taxi_legs else
                   " max_taxi_legs に1を指定すると、バスが繋がらない区間を"
                   "タクシーで補った場合に成立するかも確認できます。")
            )),
            candidates=[],
        )

    summaries = "／".join(describe(candidate, destination) for candidate in candidates)
    return SuggestResponse(
        status="success",
        ai_hint=with_note(coverage_note, (
            f"{len(candidates)}件の候補が見つかりました: {summaries}。"
            "時刻は概算です。ユーザーに違いを示して選んでもらい、選ばれた候補の "
            "spot_ids に destination_spot_id を足して POST /plan/route-plan "
            "を呼ぶと、実際の経路と正確な時刻が入った行程になります"
            "（各スポットの stay_minutes を dwell_minutes として渡してください）。"
            "営業時間は手で集めた概算なので、確定前に施設へ確認するよう添えてください。"
        )),
        candidates=[
            _to_response(
                candidate, request.departure_time, request.departure_date, base, destination
            )
            for candidate in candidates
        ],
    )


def _to_response(
    candidate: Candidate, departure_time: str, departure_date: str, base, destination
) -> SuggestedItinerary:
    day = parse_service_date(departure_date)
    return SuggestedItinerary(
        spot_ids=[stop.spot.spot_id for stop in candidate.stops],
        stops=[
            SuggestedStopResponse(
                spot_id=stop.spot.spot_id,
                name=stop.spot.name,
                lat=stop.spot.lat,
                lng=stop.spot.lng,
                tags=list(stop.spot.tags),
                by_taxi=stop.by_taxi,
                arrival_time=format_minutes(stop.arrival_minutes),
                departure_time=format_minutes(stop.departure_minutes),
                wait_minutes=stop.wait_minutes,
                stay_minutes=stop.departure_minutes - stop.arrival_minutes - stop.wait_minutes,
                opening_hours=stop.spot.hours.describe(day) if stop.spot.hours else None,
            )
            for stop in candidate.stops
        ],
        tags=candidate.tags,
        destination_arrival_time=format_minutes(candidate.destination_arrival_minutes),
        total_duration_min=(
            candidate.destination_arrival_minutes
            - parse_gtfs_time_to_minutes(departure_time)
        ),
        taxi_legs=candidate.taxi_legs,
        estimated_taxi_fare_jpy=estimate_taxi_fare_jpy(
            candidate, base, destination, estimate_fare_jpy
        ),
    )
