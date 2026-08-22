from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.transit import BusTripOption


class GraphDataRequest(BaseModel):
    stop_ids: list[str] = Field(
        min_length=2,
        max_length=15,
        description=(
            "GTFS stop_ids to compute direct bus edges between, for every "
            "ordered pair. Use exact IDs from /transit/stops/nearby or "
            "/transit/routes (feed-prefixed when multiple feeds are merged)."
        ),
        examples=[["nasushiobara:1_01", "nasushiobara:112_01"]],
    )
    departure_time: str = Field(
        description=(
            "Earliest departure time to consider for every edge, "
            '"HH:MM" or GTFS "HH:MM:SS".'
        ),
        examples=["09:00"],
    )
    departure_date: date | None = Field(
        None, description="Optional service date used for calendar filtering."
    )


class GraphEdge(BaseModel):
    from_stop_id: str = Field(description="Origin GTFS stop_id for this edge.")
    to_stop_id: str = Field(description="Destination GTFS stop_id for this edge.")
    mode: str = Field(
        "bus",
        description=(
            "Transport mode for this edge. Always 'bus' currently — "
            "walking/taxi edges are not implemented yet."
        ),
    )
    bus_options: list[BusTripOption] = Field(
        description=(
            "Direct bus trips found for this pair, earliest departure "
            "first (up to 3). Empty if no direct bus connects them."
        )
    )


class GraphDataResponse(BaseModel):
    edges: list[GraphEdge] = Field(
        description="One entry per ordered (from_stop_id, to_stop_id) pair from stop_ids."
    )
