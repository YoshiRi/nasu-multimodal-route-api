from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from app.services.gtfs_engine import GTFSEngine


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "search-bus":
        return run_search_bus(args)

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nasu-route",
        description="GTFS-based route planning PoC CLI for the Nasu area.",
    )
    subparsers = parser.add_subparsers(dest="command")

    search_bus = subparsers.add_parser(
        "search-bus",
        help="Search direct bus trips between two GTFS stops.",
        description=(
            "Search direct bus trips that depart from the origin stop at or after "
            "the specified GTFS time."
        ),
    )
    search_bus.add_argument(
        "--gtfs-dir",
        required=True,
        help="Directory containing GTFS CSV files such as stops.txt and stop_times.txt.",
    )
    search_bus.add_argument(
        "--from",
        dest="origin_stop_id",
        required=True,
        help="Origin GTFS stop_id.",
    )
    search_bus.add_argument(
        "--to",
        dest="dest_stop_id",
        required=True,
        help="Destination GTFS stop_id.",
    )
    search_bus.add_argument(
        "--time",
        dest="departure_time",
        required=True,
        help='Departure time in "HH:MM" or GTFS "HH:MM:SS" format.',
    )
    search_bus.add_argument(
        "--date",
        dest="departure_date",
        help='Optional service date in "YYYY-MM-DD" or "YYYYMMDD" format.',
    )
    search_bus.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON results instead of a human-readable table.",
    )

    return parser


def run_search_bus(args: argparse.Namespace) -> int:
    try:
        engine = GTFSEngine(args.gtfs_dir)
        results = engine.search_next_bus(
            origin_stop_id=args.origin_stop_id,
            dest_stop_id=args.dest_stop_id,
            departure_time=args.departure_time,
            departure_date=args.departure_date,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    print(format_bus_results(results))
    return 0


def format_bus_results(results: list[dict[str, str | int | None]]) -> str:
    if not results:
        return "No direct bus trips found."

    lines = [
        "Direct bus trips:",
        "line_name | trip_headsign | departure_time | arrival_time | duration_min",
        "-" * 72,
    ]
    for result in results:
        lines.append(
            " | ".join(
                [
                    _format_cell(result["line_name"]),
                    _format_cell(result["trip_headsign"]),
                    _format_cell(result["departure_time"]),
                    _format_cell(result["arrival_time"]),
                    _format_cell(result["duration_min"]),
                ]
            )
        )

    return "\n".join(lines)


def _format_cell(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
