from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_SUGGESTION_DWELL_MINUTES = 60


class SuggestRequest(BaseModel):
    base_location_id: str = Field(
        description="Spot ID the trip starts from.",
        examples=["station_nasushiobara"],
    )
    destination_spot_id: str = Field(
        description="Spot ID the trip must end at.",
        examples=["spot_hotel_epinard"],
    )
    departure_time: str = Field(
        description='Departure time from base_location_id, "HH:MM".',
        examples=["09:00"],
    )
    departure_date: str = Field(
        description='Travel date, "YYYY-MM-DD", used for GTFS calendar filtering.',
        examples=["2026-10-15"],
    )
    arrive_by: str = Field(
        description=(
            'Latest acceptable arrival at the destination, "HH:MM". '
            "Candidates that cannot make it are not returned."
        ),
        examples=["18:00"],
    )
    spot_count: int = Field(
        3,
        ge=1,
        le=5,
        description="How many spots to try to fit in between start and destination.",
        examples=[3],
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Only suggest spots carrying at least one of these tags (see "
            "GET /spots for what each spot has). Empty means no filter."
        ),
        examples=[["牧場", "動物"]],
    )
    must_include_spot_ids: list[str] = Field(
        default_factory=list,
        description="Spot IDs every candidate must contain.",
        examples=[["spot_minamigaoka"]],
    )
    dwell_minutes: int = Field(
        DEFAULT_SUGGESTION_DWELL_MINUTES,
        gt=0,
        description="Time to spend at each suggested spot.",
        examples=[60],
    )
    max_candidates: int = Field(
        3, ge=1, le=10, description="How many candidates to return.", examples=[3]
    )
    max_transfers: int = Field(
        1,
        ge=0,
        le=1,
        description="Bus changes allowed within a single leg (see POST /plan/route-plan).",
    )


class SuggestedStopResponse(BaseModel):
    spot_id: str = Field(description="Spot ID to visit.")
    name: str = Field(description="Spot name.")
    lat: float = Field(description="Spot latitude.")
    lng: float = Field(description="Spot longitude.")
    tags: list[str] = Field(description="The spot's tags.")
    arrival_time: str = Field(description='Estimated arrival, "HH:MM".')
    departure_time: str = Field(description='Estimated departure after the stay, "HH:MM".')


class SuggestedItinerary(BaseModel):
    spot_ids: list[str] = Field(
        description=(
            "Spots in visit order, ready to pass to POST /plan/route-plan "
            "as target_spot_ids (append destination_spot_id as the final "
            "entry to build the full itinerary)."
        )
    )
    stops: list[SuggestedStopResponse] = Field(description="The spots, in visit order.")
    tags: list[str] = Field(description="Every tag this candidate covers.")
    destination_arrival_time: str = Field(
        description='Estimated arrival at the destination, "HH:MM".'
    )
    total_duration_min: int = Field(
        description="Estimated minutes from departure to arriving at the destination."
    )


class SuggestResponse(BaseModel):
    status: Literal["success", "error"] = Field(
        description=(
            "'success' when at least one candidate fits; 'error' when the "
            "request was well-formed but nothing fits (too little time, "
            "too many spots, or a tag that matches nowhere reachable)."
        )
    )
    ai_hint: str = Field(
        description=(
            "Natural-language guidance for the calling agent: what the "
            "candidates are and how they differ, or what to relax."
        )
    )
    candidates: list[SuggestedItinerary] = Field(
        description=(
            "Candidate itineraries, most spots first then soonest arrival. "
            "Each has a distinct set of spots — orderings of the same "
            "places are not offered as separate choices. Times are "
            "estimates; build the chosen one with POST /plan/route-plan "
            "for real walking routes and exact timings."
        )
    )
