from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NonTransitRouteResponse(BaseModel):
    mode: Literal["walking", "taxi"] = Field(description="Transport mode used for this estimate.")
    distance_m: float = Field(description="Route distance in meters.", examples=[4815.0])
    duration_min: int = Field(description="Estimated travel time in minutes.", examples=[58])
    cost_jpy: int | None = Field(
        description=(
            "Estimated cost in JPY. Null for walking. For taxi, this is a "
            "rough distance-based estimate (see app/services/taxi_fare.py) "
            "not confirmed against real local rates — treat as illustrative."
        ),
        examples=[2100],
    )
