from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import pandas as pd

EARTH_RADIUS_M = 6_371_000

# Columns holding GTFS IDs, per table. Different operators' feeds often
# reuse the same short IDs (e.g. stop_id "1_01") for unrelated stops, so
# GTFSEngine.from_feeds() prefixes these with "<feed_name>:" to keep merged
# feeds unambiguous.
ID_COLUMNS: dict[str, tuple[str, ...]] = {
    "stops": ("stop_id",),
    "routes": ("route_id",),
    "trips": ("route_id", "service_id", "trip_id"),
    "stop_times": ("trip_id", "stop_id"),
    "calendar": ("service_id",),
    "calendar_dates": ("service_id",),
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
        for table_name in ("stops", "routes", "trips", "stop_times", "calendar", "calendar_dates"):
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
            )
            results.append(option.to_dict())

        return results

    def nearby_stops(
        self,
        lat: float,
        lng: float,
        radius_m: float = 500,
    ) -> list[dict[str, str | float]]:
        """Return GTFS stops within radius_m of (lat, lng), nearest first."""

        stops = self.stops.copy()
        stops["distance_m"] = stops.apply(
            lambda row: haversine_distance_m(
                lat, lng, float(row["stop_lat"]), float(row["stop_lon"])
            ),
            axis=1,
        )
        stops = stops[stops["distance_m"] <= radius_m].sort_values("distance_m")

        return [
            {
                "stop_id": str(row.stop_id),
                "stop_name": str(row.stop_name),
                "stop_lat": float(row.stop_lat),
                "stop_lon": float(row.stop_lon),
                "distance_m": round(float(row.distance_m), 1),
            }
            for row in stops.itertuples(index=False)
        ]

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
