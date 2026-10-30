from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
    dwell_minutes: int | None = Field(
        None,
        gt=0,
        description=(
            "Time to spend at every suggested spot. Leave unset to use each "
            "spot's own typical stay (a safari park gets longer than a "
            "bridge; see dwell_minutes in GET /spots), falling back to 60."
        ),
        examples=[None],
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
    max_taxi_legs: int = Field(
        0,
        ge=0,
        le=2,
        description=(
            "How many hops may be taken by taxi when no bus gets through. "
            "Answers 'would this trip work if I took one taxi?' — taxi is "
            "only ever used where buses fail, never to shave time off a "
            "leg that already works, and all-bus candidates still rank "
            "first. Times and fares for taxi hops are rough estimates "
            "from straight-line distance."
        ),
        examples=[1],
    )


class SuggestedStopResponse(BaseModel):
    spot_id: str = Field(description="Spot ID to visit.")
    name: str = Field(description="Spot name.")
    lat: float = Field(description="Spot latitude.")
    lng: float = Field(description="Spot longitude.")
    tags: list[str] = Field(description="The spot's tags.")
    by_taxi: bool = Field(
        description="True when no bus reached this spot in time and a taxi was assumed."
    )
    arrival_time: str = Field(description='Estimated arrival, "HH:MM".')
    departure_time: str = Field(description='Estimated departure after the stay, "HH:MM".')
    wait_minutes: int = Field(
        description="Minutes spent waiting for it to open, included before the stay."
    )
    stay_minutes: int = Field(
        description=(
            "Length of the stay. Pass it as dwell_minutes for this spot "
            "when building the candidate with POST /plan/route-plan."
        )
    )
    opening_hours: str | None = Field(
        description=(
            "That day's hours as a short Japanese summary, or null when "
            "they were never checked. Hand-collected and approximate: "
            "worth confirming with the venue before going."
        )
    )


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
    taxi_legs: int = Field(
        description="How many hops in this candidate need a taxi. 0 means all bus."
    )
    estimated_taxi_fare_jpy: int = Field(
        description=(
            "Rough total fare for those hops, 0 when there are none. From "
            "straight-line distance and an unverified fare formula — "
            "indicative only."
        )
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
