from __future__ import annotations

from pydantic import BaseModel, Field


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
