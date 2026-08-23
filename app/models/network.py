from __future__ import annotations

from pydantic import BaseModel, Field


class StopSummary(BaseModel):
    stop_id: str = Field(description="GTFS stop_id (feed-prefixed if multiple feeds are merged).")
    stop_name: str = Field(description="Stop name.")
    stop_lat: float = Field(description="Stop latitude.")
    stop_lon: float = Field(description="Stop longitude.")


class RouteShape(BaseModel):
    shape_id: str = Field(description="GTFS shape_id (feed-prefixed if multiple feeds are merged).")
    route_id: str | None = Field(description="A route_id that uses this shape, if any.")
    line_name: str | None = Field(description="Display name of that route, if any.")
    points: list[list[float]] = Field(
        description="Polyline points in [lat, lon] order, in shape_pt_sequence order."
    )


class NetworkResponse(BaseModel):
    stops: list[StopSummary] = Field(description="Every boardable stop in the currently loaded GTFS data.")
    shapes: list[RouteShape] = Field(description="Every route shape in the currently loaded GTFS data.")
