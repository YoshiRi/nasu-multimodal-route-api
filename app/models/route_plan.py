from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RoutePlanRequest(BaseModel):
    base_location_id: str = Field(
        description="Spot ID of the starting and ending point.",
        examples=["station_nasushiobara"],
    )
    target_spot_ids: list[str] = Field(
        min_length=1,
        max_length=10,
        description=(
            "Spot IDs to visit. The current version visits them in the "
            "given array order — no route optimization yet, so list them "
            "in the order they should be visited."
        ),
        examples=[["spot_nasu_ropeway", "spot_nasu_yumoto_onsen"]],
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
    mode: Literal["bus", "walking", "stay"] = Field(description="How this step is carried out.")
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
            "no bus available for a leg). Malformed requests (unknown "
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
