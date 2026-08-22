from __future__ import annotations

from pydantic import BaseModel, Field


class NearbyStop(BaseModel):
    stop_id: str = Field(
        description="GTFS stop_id. Feed-prefixed (e.g. 'nasushiobara:1_01') when "
        "multiple GTFS feeds are merged; use this exact value in other endpoints.",
        examples=["nasushiobara:1_01"],
    )
    stop_name: str = Field(description="Stop name.", examples=["西那須野駅西口"])
    stop_lat: float = Field(description="Stop latitude.", examples=[36.8847])
    stop_lon: float = Field(description="Stop longitude.", examples=[139.9866])
    distance_m: float = Field(
        description="Distance from the query point in meters.", examples=[2.7]
    )


class BusTripOption(BaseModel):
    route_id: str = Field(description="GTFS route_id.", examples=["nasushiobara:route_w2"])
    route_short_name: str | None = Field(
        description="GTFS route_short_name, if any.", examples=[None]
    )
    route_long_name: str | None = Field(
        description="GTFS route_long_name, if any.", examples=["W2"]
    )
    line_name: str = Field(
        description="Display name for the line: short name, long name, or route_id.",
        examples=["W2"],
    )
    trip_id: str = Field(description="GTFS trip_id.", examples=["nasushiobara:trip_4040_0700"])
    trip_headsign: str | None = Field(
        description="GTFS trip_headsign, if any.", examples=["西那須野駅東口"]
    )
    origin_stop_id: str = Field(
        description="Origin GTFS stop_id.", examples=["nasushiobara:1_01"]
    )
    destination_stop_id: str = Field(
        description="Destination GTFS stop_id.", examples=["nasushiobara:112_01"]
    )
    departure_time: str = Field(
        description="Departure time at the origin stop, GTFS HH:MM:SS (hours may exceed 24).",
        examples=["07:00:00"],
    )
    arrival_time: str = Field(
        description="Arrival time at the destination stop, GTFS HH:MM:SS.",
        examples=["07:43:00"],
    )
    duration_min: int = Field(description="Travel time in minutes.", examples=[43])
