"""Run realistic trips through the planner and print what comes back.

Not a test — it needs the real GTFS feeds in data/gtfs/, which are not
committed, so it cannot run in CI. It exists because every bug found since
the planner started working was found this way: a same-stop transfer
leaving a two-minute hole in the itinerary, required spots being pruned out
of the beam, a 21km hop estimated as a five-hour walk. None of those showed
up against the synthetic fixtures, because none of them are about one
function being wrong — they are about search limits and user constraints
interacting over a real timetable.

The scenarios are the ones that keep coming up in practice: arriving at the
station and getting to a hotel, and leaving a hotel to catch a train.

    python scripts/check_scenarios.py              # suggestions only (no ORS)
    python scripts/check_scenarios.py --build      # also build each winner
    python scripts/check_scenarios.py --date 2026-11-03
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from gtfs_multifeed import GTFSEngine, discover_gtfs_feeds

from app.gtfs_feeds import GTFS_DATA_ROOT, REPO_ROOT
from app.models.route_plan import RoutePlanRequest
from app.services.route_planner import build_route_plan
from app.services.spot_repository import SpotRepository
from app.services.suggester import estimate_taxi_fare_jpy, suggest_itineraries
from app.services.taxi_fare import estimate_fare_jpy

STATION = "station_nasushiobara"
EPINARD = "spot_hotel_epinard"
MERCURE = "spot_grand_mercure"


@dataclass(frozen=True)
class Scenario:
    title: str
    base: str
    destination: str
    departure_time: str
    arrive_by: str
    spot_count: int = 2
    tags: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    dwell_minutes: int = 60


def scenarios(date: str) -> list[Scenario]:
    """Arriving-at-the-station and leaving-the-hotel trips, both hotels."""

    out: list[Scenario] = []
    for hotel, label in ((EPINARD, "エピナール"), (MERCURE, "グランドメルキュール")):
        out += [
            Scenario(
                title=f"駅11:00着 → 動物か牧場＋1件 → 18:00までに{label}",
                base=STATION, destination=hotel,
                departure_time="11:00", arrive_by="18:00",
                spot_count=2, tags=["動物", "牧場"],
            ),
            Scenario(
                title=f"{label}9:30発 → 牧場＋1件 → 16:00までに駅",
                base=hotel, destination=STATION,
                departure_time="09:30", arrive_by="16:00",
                spot_count=2, must_include=["spot_minamigaoka"],
            ),
            Scenario(
                title=f"{label}9:30発 → 自然・登山を2件 → 17:00までに駅",
                base=hotel, destination=STATION,
                departure_time="09:30", arrive_by="17:00",
                spot_count=2, tags=["自然", "登山"],
            ),
        ]
    return out


def run_suggestions(scenario, spots, engine, date, max_taxi_legs):
    base, destination = spots.get(scenario.base), spots.get(scenario.destination)
    started = time.time()
    candidates = suggest_itineraries(
        spots=spots, engine=engine,
        base_location_id=scenario.base, destination_spot_id=scenario.destination,
        departure_time=scenario.departure_time, departure_date=date,
        arrive_by=scenario.arrive_by, spot_count=scenario.spot_count,
        tags=scenario.tags, must_include_spot_ids=scenario.must_include,
        dwell_minutes=scenario.dwell_minutes, max_candidates=3,
        max_taxi_legs=max_taxi_legs,
    )
    elapsed = time.time() - started

    label = "バスのみ" if max_taxi_legs == 0 else f"タクシー{max_taxi_legs}回まで"
    if not candidates:
        print(f"    {label:<14} 候補なし            ({elapsed:.1f}s)")
        return None

    for index, candidate in enumerate(candidates):
        names = " → ".join(stop.spot.name for stop in candidate.stops)
        extra = ""
        if candidate.taxi_legs:
            fare = estimate_taxi_fare_jpy(candidate, base, destination, estimate_fare_jpy)
            extra = f"  [タクシー{candidate.taxi_legs}区間 概算¥{fare:,}]"
        prefix = f"    {label:<14}" if index == 0 else " " * 18
        hhmm = f"{candidate.destination_arrival_minutes // 60:02d}:{candidate.destination_arrival_minutes % 60:02d}"
        print(f"{prefix} {hhmm}着  {names}{extra}")
    print(f"{' ' * 18} ({elapsed:.1f}s)")
    return candidates[0]


async def build_winner(scenario, candidate, spots, engine, date) -> None:
    request = RoutePlanRequest(
        base_location_id=scenario.base,
        target_spot_ids=[
            *({"spot_id": stop.spot.spot_id, "dwell_minutes": scenario.dwell_minutes}
              for stop in candidate.stops),
            {"spot_id": scenario.destination},
        ],
        departure_time=scenario.departure_time,
        departure_date=date,
    )
    response = await build_route_plan(request, engine, spots)
    if not response.plans:
        print(f"      実行程: 組めず — {response.ai_hint[:50]}")
        return

    steps = response.plans[0].steps
    gaps = sum(
        1 for a, b in zip(steps, steps[1:]) if a.end_time != b.start_time
    )
    print(f"      実行程: {steps[0].start_time}→{steps[-1].end_time} "
          f"{len(steps)}ステップ"
          + ("" if gaps == 0 else f"  ★時刻に{gaps}箇所の空白"))


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-10-15", help="Travel date (YYYY-MM-DD).")
    parser.add_argument("--build", action="store_true",
                        help="Also build each winning candidate for real (needs ORS_API_KEY).")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    feeds = discover_gtfs_feeds(GTFS_DATA_ROOT)
    if not feeds:
        print("No GTFS feeds under data/gtfs/ — run scripts/download_gtfs.py first.",
              file=sys.stderr)
        return 1

    engine = GTFSEngine.from_feeds(feeds)
    engine.transfer_edges()
    spots = SpotRepository(REPO_ROOT / "data" / "spots" / "nasu_spots.json")
    print(f"GTFS {len(feeds)}フィード / 停留所 {len(engine.stops)}件 / "
          f"スポット {len(spots.list_all())}件 / {args.date}\n")

    for scenario in scenarios(args.date):
        print(f"── {scenario.title}")
        winner = run_suggestions(scenario, spots, engine, args.date, 0)
        with_taxi = run_suggestions(scenario, spots, engine, args.date, 1)
        if args.build:
            chosen = winner or with_taxi
            if chosen:
                await build_winner(scenario, chosen, spots, engine, args.date)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
