from __future__ import annotations

from pydantic import BaseModel, Field


class NearbyStop(BaseModel):
    stop_id: str = Field(description="GTFS stop_id.")
    stop_name: str = Field(description="Stop name.")
    stop_lat: float = Field(description="Stop latitude.")
    stop_lon: float = Field(description="Stop longitude.")
    distance_m: float = Field(description="Distance from the query point in meters.")


class BusTripOption(BaseModel):
    route_id: str = Field(description="GTFS route_id.")
    route_short_name: str | None = Field(description="GTFS route_short_name, if any.")
    route_long_name: str | None = Field(description="GTFS route_long_name, if any.")
    line_name: str = Field(
        description="Display name for the line: short name, long name, or route_id."
    )
    trip_id: str = Field(description="GTFS trip_id.")
    trip_headsign: str | None = Field(description="GTFS trip_headsign, if any.")
    origin_stop_id: str = Field(description="Origin GTFS stop_id.")
    destination_stop_id: str = Field(description="Destination GTFS stop_id.")
    departure_time: str = Field(description="Departure time at the origin stop.")
    arrival_time: str = Field(description="Arrival time at the destination stop.")
    duration_min: int = Field(description="Travel time in minutes.")
