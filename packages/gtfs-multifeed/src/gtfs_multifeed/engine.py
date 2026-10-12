from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6_371_000

# --- Walkable transfers between nearby stops -------------------------------
# GTFS feeds frequently omit transfers.txt, so transfer points are inferred
# from geography instead. These defaults are tuned for regional bus networks.

# Two boardable stops this close are treated as connected on foot. Much
# beyond this and a "transfer" becomes a walk the rider would not expect.
DEFAULT_TRANSFER_RADIUS_M = 300

# Operators routinely register the same physical stop once per direction, a
# few metres apart. Pairs this close are one place, not a walk.
SAME_PLACE_RADIUS_M = 30

TRANSFER_WALK_SPEED_M_PER_MIN = 80.0
# Straight-line distance understates street distance; inflate it rather than
# calling out to a routing service for thousands of pairs.
TRANSFER_WALK_DETOUR_FACTOR = 1.3
# A bus will not wait, so even stepping across the road costs something.
MIN_TRANSFER_MINUTES = 2

# How many departures from the origin stop to follow when looking for a
# connection. Bounds the search without meaningfully narrowing it: later
# departures can only reach a transfer stop later, and arriving earlier at
# a transfer stop weakly dominates (see search_journeys).
MAX_FIRST_LEG_DEPARTURES = 10

# Columns holding GTFS IDs, per table. Different operators' feeds often
# reuse the same short IDs (e.g. stop_id "1_01") for unrelated stops, so
# GTFSEngine.from_feeds() prefixes these with "<feed_name>:" to keep merged
# feeds unambiguous.
ID_COLUMNS: dict[str, tuple[str, ...]] = {
    "stops": ("stop_id",),
    "routes": ("route_id",),
    "trips": ("route_id", "service_id", "trip_id", "shape_id"),
    "stop_times": ("trip_id", "stop_id"),
    "calendar": ("service_id",),
    "calendar_dates": ("service_id",),
    "shapes": ("shape_id",),
}


REQUIRED_GTFS_FILES = {
    "stops": "stops.txt",
    "routes": "routes.txt",
    "trips": "trips.txt",
    "stop_times": "stop_times.txt",
}

OPTIONAL_GTFS_FILES = {
    "calendar": "calendar.txt",
    "calendar_dates": "calendar_dates.txt",
    "shapes": "shapes.txt",
}

WEEKDAY_COLUMNS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


@dataclass(frozen=True)
class DirectBusOption:
    route_id: str
    route_short_name: str | None
    route_long_name: str | None
    line_name: str
    trip_id: str
    trip_headsign: str | None
    origin_stop_id: str
    destination_stop_id: str
    departure_time: str
    arrival_time: str
    duration_min: int
    shape_id: str | None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "route_id": self.route_id,
            "route_short_name": self.route_short_name,
            "route_long_name": self.route_long_name,
            "line_name": self.line_name,
            "trip_id": self.trip_id,
            "trip_headsign": self.trip_headsign,
            "origin_stop_id": self.origin_stop_id,
            "destination_stop_id": self.destination_stop_id,
            "departure_time": self.departure_time,
            "arrival_time": self.arrival_time,
            "duration_min": self.duration_min,
            "shape_id": self.shape_id,
        }


class GTFSEngine:
    """Read GTFS CSV files and search direct bus options."""

    def __init__(self, gtfs_dir: str | Path) -> None:
        self.gtfs_dir = Path(gtfs_dir)
        self._validate_gtfs_dir()

        self.stops = self._read_gtfs_file("stops")
        self.routes = self._read_gtfs_file("routes")
        self.trips = self._read_gtfs_file("trips")
        self.stop_times = self._read_gtfs_file("stop_times")
        self.calendar = self._read_optional_gtfs_file("calendar")
        self.calendar_dates = self._read_optional_gtfs_file("calendar_dates")
        self.shapes = self._read_optional_gtfs_file("shapes")
        self._prepare_tables()

    @classmethod
    def from_feeds(cls, feeds: Mapping[str, str | Path]) -> "GTFSEngine":
        """Load and merge multiple GTFS feeds.

        Every ID column (see ID_COLUMNS) is prefixed with "<feed_name>:" so
        that unrelated stops/routes/trips from different operators can
        never collide, even if their raw GTFS IDs happen to match.
        """

        if not feeds:
            raise ValueError("from_feeds requires at least one feed")

        engines = {feed_name: cls(gtfs_dir) for feed_name, gtfs_dir in feeds.items()}

        merged = cls.__new__(cls)
        merged.gtfs_dir = None
        for table_name in (
            "stops", "routes", "trips", "stop_times", "calendar", "calendar_dates", "shapes"
        ):
            frames = [
                _prefix_id_columns(getattr(engine, table_name), ID_COLUMNS[table_name], feed_name)
                for feed_name, engine in engines.items()
                if not getattr(engine, table_name).empty
            ]
            setattr(
                merged,
                table_name,
                pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            )

        return merged

    def search_next_bus(
        self,
        origin_stop_id: str,
        dest_stop_id: str,
        departure_time: str,
        departure_date: str | date | None = None,
    ) -> list[dict[str, str | int | None]]:
        """Return up to 3 direct trips departing at or after departure_time."""

        if origin_stop_id == dest_stop_id:
            raise ValueError("origin_stop_id and dest_stop_id must be different")

        departure_min = parse_gtfs_time_to_minutes(departure_time)
        origin_rows = self.stop_times[self.stop_times["stop_id"] == origin_stop_id]
        dest_rows = self.stop_times[self.stop_times["stop_id"] == dest_stop_id]

        candidates = origin_rows.merge(
            dest_rows,
            on="trip_id",
            suffixes=("_origin", "_dest"),
        )
        candidates = candidates[
            (candidates["stop_sequence_origin"] < candidates["stop_sequence_dest"])
            & (candidates["departure_minutes_origin"] >= departure_min)
        ]

        if candidates.empty:
            return []

        candidates = candidates.merge(self.trips, on="trip_id", how="left")
        if departure_date is not None:
            active_service_ids = self.active_service_ids(departure_date)
            candidates = candidates[candidates["service_id"].isin(active_service_ids)]
            if candidates.empty:
                return []

        candidates = candidates.merge(self.routes, on="route_id", how="left")
        candidates = candidates.sort_values(
            ["departure_minutes_origin", "arrival_minutes_dest", "trip_id"]
        ).head(3)

        results: list[dict[str, str | int | None]] = []
        for row in candidates.itertuples(index=False):
            option = DirectBusOption(
                route_id=str(row.route_id),
                route_short_name=_nullable_str(row.route_short_name),
                route_long_name=_nullable_str(row.route_long_name),
                line_name=_line_name(row.route_short_name, row.route_long_name, row.route_id),
                trip_id=str(row.trip_id),
                trip_headsign=_nullable_str(row.trip_headsign),
                origin_stop_id=str(row.stop_id_origin),
                destination_stop_id=str(row.stop_id_dest),
                departure_time=str(row.departure_time_origin),
                arrival_time=str(row.arrival_time_dest),
                duration_min=int(row.arrival_minutes_dest - row.departure_minutes_origin),
                shape_id=_nullable_str(row.shape_id),
            )
            results.append(option.to_dict())

        return results

    def nearby_stops(
        self,
        lat: float,
        lng: float,
        radius_m: float = 500,
    ) -> list[dict[str, str | float]]:
        """Return GTFS stops within radius_m of (lat, lng), nearest first.

        Vectorized, and over cached coordinate arrays: route planning calls
        this once per endpoint of every leg it considers, which a row-wise
        pass over every stop made the dominant cost of a search.
        """

        if self.stops.empty:
            return []

        stop_lat, stop_lon = self._stop_radians()
        origin_lat, origin_lng = radians(lat), radians(lng)
        d_lat = stop_lat - origin_lat
        d_lon = stop_lon - origin_lng
        haversine = (
            np.sin(d_lat / 2) ** 2
            + np.cos(origin_lat) * np.cos(stop_lat) * np.sin(d_lon / 2) ** 2
        )
        distance_m = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(haversine))

        within = np.nonzero(distance_m <= radius_m)[0]
        nearest_first = within[np.argsort(distance_m[within], kind="stable")]

        stop_ids = self.stops["stop_id"].to_numpy()
        stop_names = self.stops["stop_name"].to_numpy()
        latitudes = self.stops["stop_lat"].to_numpy(dtype=float)
        longitudes = self.stops["stop_lon"].to_numpy(dtype=float)

        return [
            {
                "stop_id": str(stop_ids[index]),
                "stop_name": str(stop_names[index]),
                "stop_lat": float(latitudes[index]),
                "stop_lon": float(longitudes[index]),
                "distance_m": round(float(distance_m[index]), 1),
            }
            for index in nearest_first
        ]

    def _stop_radians(self) -> tuple[np.ndarray, np.ndarray]:
        """Stop coordinates in radians, computed once per engine."""

        cached = getattr(self, "_stop_radians_cache", None)
        if cached is None:
            # from_feeds() builds instances via __new__, so this cannot be
            # set up in __init__ alone.
            cached = self._stop_radians_cache = (
                np.radians(self.stops["stop_lat"].to_numpy(dtype=float)),
                np.radians(self.stops["stop_lon"].to_numpy(dtype=float)),
            )
        return cached

    def search_journeys(
        self,
        origin_stop_id: str,
        dest_stop_id: str,
        departure_time: str,
        departure_date: str | date | None = None,
        max_transfers: int = 1,
        max_distance_m: float = DEFAULT_TRANSFER_RADIUS_M,
        limit: int = 3,
    ) -> list[dict]:
        """Search journeys between two stops, optionally with one transfer.

        Unlike search_next_bus(), which only finds trips where a single
        vehicle serves both stops, this will change vehicles at a shared or
        walkable-nearby stop (see transfer_edges()).

        Returns journeys sorted by arrival time, each shaped as::

            {"legs": [<ride>, ...],          # same shape as search_next_bus
             "transfers": [<transfer>, ...], # len(legs) - 1 entries
             "departure_time", "arrival_time", "duration_min", "num_transfers"}

        max_transfers is currently capped at 1. Within that, the result is
        the genuine earliest arrival: only the earliest arrival at each
        candidate transfer stop is pursued, which is sound because arriving
        earlier at a stop can only widen the set of onward departures that
        are still catchable.
        """

        if origin_stop_id == dest_stop_id:
            raise ValueError("origin_stop_id and dest_stop_id must be different")
        if max_transfers > 1:
            raise ValueError("max_transfers above 1 is not supported yet")

        journeys = [
            self._journey(legs=[ride], transfers=[])
            for ride in self.search_next_bus(
                origin_stop_id, dest_stop_id, departure_time, departure_date
            )
        ]
        if max_transfers >= 1:
            journeys.extend(
                self._one_transfer_journeys(
                    origin_stop_id,
                    dest_stop_id,
                    parse_gtfs_time_to_minutes(departure_time),
                    departure_date,
                    max_distance_m,
                )
            )

        journeys.sort(
            key=lambda j: (
                parse_gtfs_time_to_minutes(j["arrival_time"]),
                j["num_transfers"],
                -parse_gtfs_time_to_minutes(j["departure_time"]),
            )
        )
        return journeys[:limit]

    def _one_transfer_journeys(
        self,
        origin_stop_id: str,
        dest_stop_id: str,
        departure_min: int,
        departure_date: str | date | None,
        max_distance_m: float,
    ) -> list[dict]:
        stop_times = self.stop_times
        if departure_date is not None:
            active = self.active_service_ids(departure_date)
            runs_today = set(self.trips.loc[self.trips["service_id"].isin(active), "trip_id"])
            stop_times = stop_times[stop_times["trip_id"].isin(runs_today)]

        boarding = stop_times[
            (stop_times["stop_id"] == origin_stop_id)
            & (stop_times["departure_minutes"] >= departure_min)
        ].nsmallest(MAX_FIRST_LEG_DEPARTURES, "departure_minutes")
        if boarding.empty:
            return []

        # Where one ride from the origin can put us, earliest arrival first.
        first = boarding[["trip_id", "stop_sequence", "departure_time", "departure_minutes"]].rename(
            columns={
                "stop_sequence": "board_seq",
                "departure_time": "leg1_departure_time",
                "departure_minutes": "leg1_departure_minutes",
            }
        )
        first = first.merge(stop_times, on="trip_id")
        first = first[first["stop_sequence"] > first["board_seq"]]
        first = first[first["stop_id"] != origin_stop_id]
        if first.empty:
            return []
        first = first.sort_values("arrival_minutes").drop_duplicates("stop_id", keep="first")
        first = first.rename(
            columns={
                "stop_id": "alight_stop_id",
                "arrival_time": "leg1_arrival_time",
                "arrival_minutes": "leg1_arrival_minutes",
                "trip_id": "leg1_trip_id",
            }
        )[
            [
                "leg1_trip_id",
                "leg1_departure_time",
                "alight_stop_id",
                "leg1_arrival_time",
                "leg1_arrival_minutes",
            ]
        ]

        # Transfer: stay put, or walk to a nearby stop.
        stay = first.assign(
            board_stop_id=first["alight_stop_id"],
            transfer_distance_m=0.0,
            transfer_minutes=MIN_TRANSFER_MINUTES,
        )
        edges = self.transfer_edges(max_distance_m)
        walk = first.merge(edges, left_on="alight_stop_id", right_on="from_stop_id").rename(
            columns={"to_stop_id": "board_stop_id", "distance_m": "transfer_distance_m"}
        )
        options = pd.concat([stay, walk[stay.columns]], ignore_index=True)
        options["ready_minutes"] = options["leg1_arrival_minutes"] + options["transfer_minutes"]
        options = options[options["board_stop_id"] != origin_stop_id]
        options = options.sort_values("ready_minutes").drop_duplicates(
            "board_stop_id", keep="first"
        )

        # Second ride: anything reaching the destination from those stops.
        arriving = stop_times[stop_times["stop_id"] == dest_stop_id][
            ["trip_id", "stop_sequence", "arrival_time", "arrival_minutes"]
        ].rename(
            columns={
                "stop_sequence": "dest_seq",
                "arrival_time": "leg2_arrival_time",
                "arrival_minutes": "leg2_arrival_minutes",
            }
        )
        if arriving.empty:
            return []
        second = stop_times.merge(arriving, on="trip_id")
        second = second[second["stop_sequence"] < second["dest_seq"]]
        second = second.merge(options, left_on="stop_id", right_on="board_stop_id")
        second = second[second["departure_minutes"] >= second["ready_minutes"]]
        # Riding the same vehicle through is a direct trip, not a transfer.
        second = second[second["trip_id"] != second["leg1_trip_id"]]
        if second.empty:
            return []

        second = second.sort_values(["leg2_arrival_minutes", "departure_minutes"])
        second = second.drop_duplicates("board_stop_id", keep="first").nsmallest(
            MAX_FIRST_LEG_DEPARTURES, "leg2_arrival_minutes"
        )

        journeys = []
        for row in second.itertuples(index=False):
            leg1 = self._ride(
                row.leg1_trip_id,
                origin_stop_id,
                row.alight_stop_id,
                row.leg1_departure_time,
                row.leg1_arrival_time,
            )
            leg2 = self._ride(
                row.trip_id,
                row.board_stop_id,
                dest_stop_id,
                row.departure_time,
                row.leg2_arrival_time,
            )
            transfer = {
                "from_stop_id": str(row.alight_stop_id),
                "from_stop_name": self._stop_name(row.alight_stop_id),
                "to_stop_id": str(row.board_stop_id),
                "to_stop_name": self._stop_name(row.board_stop_id),
                "distance_m": round(float(row.transfer_distance_m), 1),
                "transfer_minutes": int(row.transfer_minutes),
            }
            journeys.append(self._journey(legs=[leg1, leg2], transfers=[transfer]))
        return journeys

    @staticmethod
    def _journey(legs: list[dict], transfers: list[dict]) -> dict:
        departure_time = str(legs[0]["departure_time"])
        arrival_time = str(legs[-1]["arrival_time"])
        return {
            "legs": legs,
            "transfers": transfers,
            "departure_time": departure_time,
            "arrival_time": arrival_time,
            "duration_min": parse_gtfs_time_to_minutes(arrival_time)
            - parse_gtfs_time_to_minutes(departure_time),
            "num_transfers": len(transfers),
        }

    def stop_info(self, stop_id: str) -> dict[str, str | float] | None:
        """Return one stop's name and coordinates, or None if it is unknown."""

        match = self.stops[self.stops["stop_id"] == stop_id]
        if match.empty:
            return None
        row = match.iloc[0]
        return {
            "stop_id": str(row["stop_id"]),
            "stop_name": str(row["stop_name"]),
            "stop_lat": float(row["stop_lat"]),
            "stop_lon": float(row["stop_lon"]),
        }

    def _stop_name(self, stop_id: str) -> str:
        info = self.stop_info(stop_id)
        return info["stop_name"] if info else str(stop_id)

    def _ride(
        self,
        trip_id: str,
        origin_stop_id: str,
        dest_stop_id: str,
        departure_time: str,
        arrival_time: str,
    ) -> dict:
        """Build one ride in the same shape search_next_bus() returns."""

        trip = self.trips[self.trips["trip_id"] == trip_id].iloc[0]
        route = self.routes[self.routes["route_id"] == trip["route_id"]]
        route_short = route.iloc[0]["route_short_name"] if not route.empty else ""
        route_long = route.iloc[0]["route_long_name"] if not route.empty else ""

        return DirectBusOption(
            route_id=str(trip["route_id"]),
            route_short_name=_nullable_str(route_short),
            route_long_name=_nullable_str(route_long),
            line_name=_line_name(route_short, route_long, trip["route_id"]),
            trip_id=str(trip_id),
            trip_headsign=_nullable_str(trip.get("trip_headsign", "")),
            origin_stop_id=str(origin_stop_id),
            destination_stop_id=str(dest_stop_id),
            departure_time=str(departure_time),
            arrival_time=str(arrival_time),
            duration_min=parse_gtfs_time_to_minutes(str(arrival_time))
            - parse_gtfs_time_to_minutes(str(departure_time)),
            shape_id=_nullable_str(trip.get("shape_id", "")),
        ).to_dict()

    def transfer_edges(self, max_distance_m: float = DEFAULT_TRANSFER_RADIUS_M) -> pd.DataFrame:
        """Return walkable stop-to-stop transfers, as the basis for multi-leg search.

        GTFS feeds frequently omit transfers.txt (none of the regional feeds
        this was built against ship one), so transfer points are inferred
        from geography: any two boardable stops within max_distance_m are
        treated as connected on foot.

        One row per *ordered* pair, so both directions are present:
        from_stop_id, to_stop_id, distance_m, transfer_minutes.

        transfer_minutes inflates the straight-line walk by
        TRANSFER_WALK_DETOUR_FACTOR to stand in for street distance, floored
        at MIN_TRANSFER_MINUTES; pairs within SAME_PLACE_RADIUS_M are treated
        as one physical place and take the floor directly.

        Results are cached per radius — the whole computation is a few
        hundred milliseconds even for thousands of stops, but callers may
        ask for it inside a search loop.
        """

        cache = getattr(self, "_transfer_cache", None)
        if cache is None:
            # from_feeds() builds instances via __new__, so this cannot be
            # set up in __init__ alone.
            cache = self._transfer_cache = {}
        if max_distance_m in cache:
            return cache[max_distance_m]

        columns = ["from_stop_id", "to_stop_id", "distance_m", "transfer_minutes"]
        if self.stops.empty:
            cache[max_distance_m] = pd.DataFrame(columns=columns)
            return cache[max_distance_m]

        stop_ids = self.stops["stop_id"].to_numpy()
        lat_r = np.radians(self.stops["stop_lat"].to_numpy(dtype=float))
        lon_r = np.radians(self.stops["stop_lon"].to_numpy(dtype=float))
        count = len(stop_ids)

        # Coarse filter over every pair, in blocks to bound peak memory. The
        # equirectangular approximation is well under 1% off at the few-
        # hundred-metre scale being filtered at, and surviving pairs get an
        # exact haversine distance below.
        from_parts: list[np.ndarray] = []
        to_parts: list[np.ndarray] = []
        block = 512
        for start in range(0, count, block):
            end = min(start + block, count)
            d_lat = lat_r[start:end, None] - lat_r[None, :]
            d_lon = lon_r[start:end, None] - lon_r[None, :]
            mean_lat = (lat_r[start:end, None] + lat_r[None, :]) / 2
            approx = EARTH_RADIUS_M * np.sqrt(d_lat**2 + (np.cos(mean_lat) * d_lon) ** 2)

            local_rows, cols = np.nonzero(approx <= max_distance_m)
            rows = local_rows + start
            keep = rows != cols
            from_parts.append(rows[keep])
            to_parts.append(cols[keep])

        from_idx = np.concatenate(from_parts) if from_parts else np.empty(0, dtype=int)
        to_idx = np.concatenate(to_parts) if to_parts else np.empty(0, dtype=int)

        d_lat = lat_r[to_idx] - lat_r[from_idx]
        d_lon = lon_r[to_idx] - lon_r[from_idx]
        haversine = (
            np.sin(d_lat / 2) ** 2
            + np.cos(lat_r[from_idx]) * np.cos(lat_r[to_idx]) * np.sin(d_lon / 2) ** 2
        )
        distance_m = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(haversine))

        # The approximation can admit pairs a hair over the threshold.
        inside = distance_m <= max_distance_m
        from_idx, to_idx, distance_m = from_idx[inside], to_idx[inside], distance_m[inside]

        walk_minutes = np.ceil(
            distance_m * TRANSFER_WALK_DETOUR_FACTOR / TRANSFER_WALK_SPEED_M_PER_MIN
        )
        transfer_minutes = np.where(
            distance_m <= SAME_PLACE_RADIUS_M,
            MIN_TRANSFER_MINUTES,
            np.maximum(walk_minutes, MIN_TRANSFER_MINUTES),
        )

        edges = pd.DataFrame(
            {
                "from_stop_id": stop_ids[from_idx],
                "to_stop_id": stop_ids[to_idx],
                "distance_m": np.round(distance_m, 1),
                "transfer_minutes": transfer_minutes.astype(int),
            }
        )
        cache[max_distance_m] = edges
        return edges

    def route_shapes(self) -> list[dict[str, str | list[list[float]] | None]]:
        """Return one polyline per shape_id, with the line_name of a route that uses it."""

        if self.shapes.empty:
            return []

        shape_route = (
            self.trips.loc[self.trips["shape_id"] != "", ["shape_id", "route_id"]]
            .drop_duplicates(subset="shape_id")
            .merge(self.routes, on="route_id", how="left")
            .set_index("shape_id")
        )

        results: list[dict[str, str | list[list[float]] | None]] = []
        shapes = self.shapes.sort_values(["shape_id", "shape_pt_sequence"])
        for shape_id, group in shapes.groupby("shape_id", sort=False):
            points = self._shape_points(group)
            route_id: str | None = None
            line_name: str | None = None
            if shape_id in shape_route.index:
                route_row = shape_route.loc[shape_id]
                route_id = str(route_row["route_id"])
                line_name = _line_name(
                    route_row.get("route_short_name"),
                    route_row.get("route_long_name"),
                    route_row["route_id"],
                )
            results.append(
                {
                    "shape_id": str(shape_id),
                    "route_id": route_id,
                    "line_name": line_name,
                    "points": points,
                }
            )

        return results

    def bus_leg_path(
        self, trip_id: str, origin_stop_id: str, dest_stop_id: str
    ) -> list[list[float]] | None:
        """Return the shape polyline for one bus leg, sliced to the ridden
        segment (origin stop to destination stop), or None if the trip has
        no shape or the stops can't be matched onto it."""

        if self.shapes.empty:
            return None

        trip_rows = self.trips[self.trips["trip_id"] == trip_id]
        if trip_rows.empty:
            return None
        shape_id = trip_rows.iloc[0]["shape_id"]
        if not shape_id:
            return None

        shape_group = self.shapes[self.shapes["shape_id"] == shape_id]
        if shape_group.empty:
            return None
        points = self._shape_points(shape_group.sort_values("shape_pt_sequence"))
        if len(points) < 2:
            return None

        origin_stop = self.stops[self.stops["stop_id"] == origin_stop_id]
        dest_stop = self.stops[self.stops["stop_id"] == dest_stop_id]
        if origin_stop.empty or dest_stop.empty:
            return None

        origin_idx = self._nearest_point_index(
            points, float(origin_stop.iloc[0]["stop_lat"]), float(origin_stop.iloc[0]["stop_lon"])
        )
        dest_idx = self._nearest_point_index(
            points, float(dest_stop.iloc[0]["stop_lat"]), float(dest_stop.iloc[0]["stop_lon"])
        )
        if origin_idx >= dest_idx:
            return None

        return points[origin_idx : dest_idx + 1]

    @staticmethod
    def _shape_points(shape_group: pd.DataFrame) -> list[list[float]]:
        return [
            [float(row.shape_pt_lat), float(row.shape_pt_lon)]
            for row in shape_group.itertuples(index=False)
        ]

    @staticmethod
    def _nearest_point_index(points: list[list[float]], lat: float, lng: float) -> int:
        return min(
            range(len(points)),
            key=lambda i: haversine_distance_m(lat, lng, points[i][0], points[i][1]),
        )

    def active_service_ids(self, service_date: str | date) -> set[str]:
        """Return GTFS service_ids active on service_date."""

        if self.calendar.empty and self.calendar_dates.empty:
            return set(self.trips["service_id"])

        gtfs_date = parse_service_date_to_gtfs_date(service_date)
        weekday_column = WEEKDAY_COLUMNS[parse_service_date(service_date).weekday()]

        active_ids: set[str] = set()
        if not self.calendar.empty:
            base_services = self.calendar[
                (self.calendar["start_date"] <= gtfs_date)
                & (self.calendar["end_date"] >= gtfs_date)
                & (self.calendar[weekday_column] == "1")
            ]
            active_ids.update(base_services["service_id"])

        if not self.calendar_dates.empty:
            exceptions = self.calendar_dates[self.calendar_dates["date"] == gtfs_date]
            added = exceptions[exceptions["exception_type"] == "1"]
            removed = exceptions[exceptions["exception_type"] == "2"]
            active_ids.update(added["service_id"])
            active_ids.difference_update(removed["service_id"])

        return active_ids

    def _validate_gtfs_dir(self) -> None:
        if not self.gtfs_dir.exists():
            raise FileNotFoundError(f"GTFS directory does not exist: {self.gtfs_dir}")
        if not self.gtfs_dir.is_dir():
            raise NotADirectoryError(f"GTFS path is not a directory: {self.gtfs_dir}")

        missing_files = [
            file_name
            for file_name in REQUIRED_GTFS_FILES.values()
            if not (self.gtfs_dir / file_name).exists()
        ]
        if missing_files:
            missing = ", ".join(sorted(missing_files))
            raise FileNotFoundError(f"Missing required GTFS files: {missing}")

    def _read_gtfs_file(self, table_name: str) -> pd.DataFrame:
        return pd.read_csv(
            self.gtfs_dir / REQUIRED_GTFS_FILES[table_name],
            dtype=str,
            keep_default_na=False,
        )

    def _read_optional_gtfs_file(self, table_name: str) -> pd.DataFrame:
        file_path = self.gtfs_dir / OPTIONAL_GTFS_FILES[table_name]
        if not file_path.exists():
            return pd.DataFrame()
        return pd.read_csv(file_path, dtype=str, keep_default_na=False)

    def _prepare_tables(self) -> None:
        self._require_columns(
            self.stops,
            "stops.txt",
            {"stop_id", "stop_name", "stop_lat", "stop_lon"},
        )
        self._require_columns(
            self.routes,
            "routes.txt",
            {"route_id", "route_short_name", "route_long_name"},
        )
        self._require_columns(
            self.trips,
            "trips.txt",
            {"route_id", "service_id", "trip_id"},
        )
        self._require_columns(
            self.stop_times,
            "stop_times.txt",
            {"trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"},
        )

        if "trip_headsign" not in self.trips.columns:
            self.trips["trip_headsign"] = ""

        if "shape_id" not in self.trips.columns:
            self.trips["shape_id"] = ""

        # Some feeds include GTFS parent stations (location_type=1) or other
        # stops that no trip ever visits — e.g. kanto_auto has ~1,400 such
        # entries. nearby_stops() must never return one of these, since no
        # bus can actually be boarded there and search_next_bus would find
        # nothing from it.
        boardable_stop_ids = set(self.stop_times["stop_id"])
        self.stops = self.stops[self.stops["stop_id"].isin(boardable_stop_ids)].reset_index(drop=True)

        if not self.calendar.empty:
            self._require_columns(
                self.calendar,
                "calendar.txt",
                {"service_id", "start_date", "end_date", *WEEKDAY_COLUMNS},
            )

        if not self.calendar_dates.empty:
            self._require_columns(
                self.calendar_dates,
                "calendar_dates.txt",
                {"service_id", "date", "exception_type"},
            )

        if not self.shapes.empty:
            self._require_columns(
                self.shapes,
                "shapes.txt",
                {"shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"},
            )
            self.shapes = self.shapes.copy()
            self.shapes["shape_pt_sequence"] = pd.to_numeric(
                self.shapes["shape_pt_sequence"],
                errors="raise",
            )

        self.stop_times = self.stop_times.copy()
        self.stop_times["stop_sequence"] = pd.to_numeric(
            self.stop_times["stop_sequence"],
            errors="raise",
        )
        self.stop_times["arrival_minutes"] = self.stop_times["arrival_time"].map(
            parse_gtfs_time_to_minutes
        )
        self.stop_times["departure_minutes"] = self.stop_times["departure_time"].map(
            parse_gtfs_time_to_minutes
        )

    @staticmethod
    def _require_columns(
        frame: pd.DataFrame,
        file_name: str,
        required_columns: set[str],
    ) -> None:
        missing = sorted(required_columns - set(frame.columns))
        if missing:
            columns = ", ".join(missing)
            raise ValueError(f"{file_name} is missing required columns: {columns}")


def _prefix_id_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    feed_name: str,
) -> pd.DataFrame:
    frame = frame.copy()
    for column in columns:
        if column in frame.columns:
            frame[column] = feed_name + ":" + frame[column]
    return frame


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in meters between two lat/lon points."""

    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def parse_gtfs_time_to_minutes(value: str) -> int:
    """Parse GTFS HH:MM or HH:MM:SS into minutes, allowing hours over 24."""

    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Invalid GTFS time: {value}")

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"Invalid GTFS time: {value}") from exc

    if hours < 0 or not 0 <= minutes <= 59 or not 0 <= seconds <= 59:
        raise ValueError(f"Invalid GTFS time: {value}")

    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


def parse_service_date(value: str | date) -> date:
    if isinstance(value, date):
        return value

    for format_string in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, format_string).date()
        except ValueError:
            continue

    raise ValueError(f"Invalid service date: {value}")


def parse_service_date_to_gtfs_date(value: str | date) -> str:
    return parse_service_date(value).strftime("%Y%m%d")


def discover_gtfs_feeds(data_root: Path) -> dict[str, Path]:
    """Return {subdirectory name: path} for every valid GTFS feed under data_root.

    A subdirectory counts as a feed only if it contains every file in
    REQUIRED_GTFS_FILES, so partially-extracted or unrelated directories
    are skipped. The result is ready to hand to GTFSEngine.from_feeds().
    """

    if not data_root.is_dir():
        return {}

    return {
        entry.name: entry
        for entry in sorted(data_root.iterdir())
        if entry.is_dir()
        and all((entry / file_name).exists() for file_name in REQUIRED_GTFS_FILES.values())
    }


def _nullable_str(value: object) -> str | None:
    text = str(value)
    return text or None


def _line_name(
    route_short_name: object,
    route_long_name: object,
    route_id: object,
) -> str:
    return (
        _nullable_str(route_short_name)
        or _nullable_str(route_long_name)
        or str(route_id)
    )
