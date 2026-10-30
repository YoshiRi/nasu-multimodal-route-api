from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.spot_repository import Spot


class SpotResponse(BaseModel):
    spot_id: str = Field(description="Spot ID.", examples=["spot_a1b2c3d4"])
    name: str = Field(description="Spot name.", examples=["那須高原 展望台"])
    lat: float = Field(description="Spot latitude.", examples=[37.0912])
    lng: float = Field(description="Spot longitude.", examples=[139.9987])
    tags: list[str] = Field(
        default_factory=list,
        description="Labels describing what kind of place this is.",
        examples=[["牧場", "動物"]],
    )
    dwell_minutes: int | None = Field(
        None,
        description=(
            "Typical length of a visit, used when a plan doesn't say how "
            "long to stay. null means no estimate (60 is used)."
        ),
        examples=[150],
    )
    hours: dict[str, Any] | None = Field(
        None,
        description=(
            "Opening hours: open/close/last_entry, closed_weekdays, seasons "
            "(month-day ranges that change the hours or close it), or "
            "always_open for outdoor places; with source, checked and note. "
            "null means never checked — not 'always open'. Hand-collected "
            "from travel sites and approximate."
        ),
    )

    @classmethod
    def from_spot(cls, spot: Spot) -> "SpotResponse":
        return cls(
            spot_id=spot.spot_id, name=spot.name, lat=spot.lat, lng=spot.lng,
            tags=list(spot.tags), dwell_minutes=spot.dwell_minutes,
            hours=spot.hours.to_json() if spot.hours else None,
        )


class CreateSpotRequest(BaseModel):
    name: str = Field(min_length=1, description="Spot name.", examples=["那須高原 展望台"])
    lat: float = Field(ge=-90, le=90, description="Spot latitude.", examples=[37.0912])
    lng: float = Field(ge=-180, le=180, description="Spot longitude.", examples=[139.9987])
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Labels describing what kind of place this is, used to narrow "
            "down suggestions (see POST /plan/suggest)."
        ),
        examples=[["牧場", "動物"]],
    )
