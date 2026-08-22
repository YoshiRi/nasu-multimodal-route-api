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


class RoutePlanRequest(BaseModel):
    base_location_id: str = Field(
        description="Spot ID of the starting and ending point.",
        examples=["station_nasushiobara"],
    )
    target_spot_ids: list[TargetSpot] = Field(
        min_length=1,
        max_length=10,
        description=(
            "Spots to visit, each with an optional dwell_minutes. The "
            "current version visits them in the given array order — no "
            "route optimization yet, so list them in the order they "
            "should be visited."
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


class ItineraryStep(BaseModel):
    step_order: int = Field(description="1-based position of this step in the itinerary.")
    mode: Literal["bus", "walking", "stay", "wait"] = Field(
        description=(
            "How this step is carried out. 'wait' means idle time before "
            "a fixed reservation/meeting time (required_arrival_time)."
        )
    )
    start_time: str = Field(description='Step start time, "HH:MM".')
    end_time: str = Field(description='Step end time, "HH:MM".')
    title: str = Field(description="Short human-readable label for this step.")
    details: str = Field(description="Longer human-readable description of this step.")


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
