from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DEFAULT_DWELL_MINUTES = 60


class TargetSpot(BaseModel):
    spot_id: str = Field(description="Spot ID to visit.", examples=["spot_nasu_ropeway"])
    dwell_minutes: int | None = Field(
        None,
        gt=0,
        description=(
            "Expected stay duration at this spot, in minutes. Omit to use "
            f"the default ({DEFAULT_DWELL_MINUTES} minutes)."
        ),
        examples=[90],
    )
    required_arrival_time: str | None = Field(
        None,
        description=(
            'Fixed meeting/reservation time at this spot, "HH:MM". If the '
            "computed arrival is earlier, a wait step is inserted so the "
            "stay starts exactly at this time. If the computed arrival "
            "would be later, the plan is infeasible (status='error') "
            "rather than silently missing the reservation."
        ),
        examples=["13:00"],
    )
    required: bool = Field(
        True,
        description=(
            "If true (default), this spot must be included or the whole "
            "plan is infeasible. If false, this spot is dropped (and the "
            "remaining spots re-planned) when it can't be fitted in, "
            "instead of failing the whole request."
        ),
    )


class RoutePlanRequest(BaseModel):
    base_location_id: str = Field(
        description="Spot ID of the starting point.",
        examples=["station_nasushiobara"],
    )
    target_spot_ids: list[TargetSpot] = Field(
        min_length=1,
        max_length=10,
        description=(
            "Spots to visit, each with an optional dwell_minutes. Visited "
            "in the given array order — no route optimization yet, so "
            "list them in the order they should be visited. The trip ends "
            "at the LAST entry; there is no automatic return to "
            "base_location_id. For a round trip, list base_location_id "
            "again as the final entry (dwell_minutes on it is ignored — "
            "no stay step is generated for the last target — but "
            "required_arrival_time still applies, e.g. 'must be back by "
            "20:00')."
        ),
        examples=[
            [
                {"spot_id": "spot_nasu_ropeway", "dwell_minutes": 90},
                {"spot_id": "spot_nasu_yumoto_onsen"},
            ]
        ],
    )
    departure_time: str = Field(
        description='Departure time from base_location_id, "HH:MM".',
        examples=["13:00"],
    )
    departure_date: str = Field(
        description='Travel date, "YYYY-MM-DD", used for GTFS calendar filtering.',
        examples=["2026-08-22"],
    )
    transfer_buffer_minutes: int = Field(
        0,
        ge=0,
        le=60,
        description=(
            "Extra minutes of slack required before boarding any bus, to "
            "absorb real-world schedule variance (e.g. a slower-than-"
            "estimated walk, or a bus running early). Applied to every "
            "leg in the plan; 0 means no buffer (bus search uses the "
            "computed arrival time as-is)."
        ),
        examples=[5],
    )


class ItineraryStep(BaseModel):
    step_order: int = Field(description="1-based position of this step in the itinerary.")
    mode: Literal["bus", "walking", "taxi", "stay", "wait"] = Field(
        description=(
            "How this step is carried out. 'wait' means idle time before "
            "a fixed reservation/meeting time (required_arrival_time). "
            "'taxi' is used instead of 'walking' when a spot has no GTFS "
            "stop within reach, and the direct walking distance would be "
            "unreasonably long."
        )
    )
    start_time: str = Field(description='Step start time, "HH:MM".')
    end_time: str = Field(description='Step end time, "HH:MM".')
    title: str = Field(description="Short human-readable label for this step.")
    details: str = Field(description="Longer human-readable description of this step.")
    from_lat: float = Field(description="Latitude at the start of this step.")
    from_lng: float = Field(description="Longitude at the start of this step.")
    to_lat: float = Field(description="Latitude at the end of this step.")
    to_lng: float = Field(description="Longitude at the end of this step.")
    path: list[list[float]] = Field(
        default_factory=list,
        description=(
            "Ordered [lat, lng] points of the actual route geometry (road "
            "path for walking/taxi, GTFS shape for bus), for map display. "
            "Empty for 'stay'/'wait' steps and when no real path is known "
            "(e.g. bus trip has no GTFS shape) — draw a straight line "
            "between from_lat/from_lng and to_lat/to_lng in that case."
        ),
    )
    cost_jpy: int | None = Field(
        None,
        description=(
            "Estimated cost in JPY for this step. Only set for 'taxi' "
            "steps; null otherwise. See app/services/taxi_fare.py — a "
            "placeholder formula, not confirmed against real local rates."
        ),
    )


class Plan(BaseModel):
    title: str = Field(description="Short human-readable label for the whole plan.")
    steps: list[ItineraryStep] = Field(description="Ordered itinerary steps.")


class RoutePlanResponse(BaseModel):
    status: Literal["success", "error"] = Field(
        description=(
            "'success' if a feasible plan was found; 'error' if the "
            "request was well-formed but no feasible plan exists (e.g. "
            "no bus available for a leg, or a required_arrival_time "
            "reservation cannot be met). Malformed requests (unknown "
            "spot_id, bad time format) use HTTP 4xx instead of this field."
        )
    )
    ai_hint: str = Field(
        description=(
            "Natural-language guidance for the calling AI agent. On "
            "success, a summary to relay to the user. On error, a "
            "suggestion for how to adjust the request and retry."
        )
    )
    plans: list[Plan] = Field(description="Candidate plans. Empty when status is 'error'.")
