# Nasu Multimodal Route API

Multimodal route planning for Japan's Nasu area — bus (GTFS) plus walking and
taxi — exposed as an OpenAPI service designed to be driven by an AI travel
agent rather than a human.

This repository holds two independently useful pieces:

| | What it is | Where |
|---|---|---|
| **`gtfs-multifeed`** | A general-purpose library for querying GTFS feeds from multiple operators as one network, including journeys that change vehicles. Nothing in it is specific to Nasu — it works with any GTFS data. | [`packages/gtfs-multifeed/`](packages/gtfs-multifeed/) |
| **The Nasu app** | The FastAPI service, itinerary builder, and debug UI. Nasu-specific: feed registry, spot master, fare assumptions. | `app/` |

The app depends on the library, not the other way around. If you only want to
query GTFS data, you want the library — see
[its README](packages/gtfs-multifeed/README.md).

## Status and limitations

A working proof of concept, honest about what it doesn't do:

- **Bus routing allows at most one transfer.** A leg between two spots may
  change buses once, at a shared stop or one a short walk away (set
  `max_transfers: 0` to restrict it to direct trips). Journeys needing two
  or more changes are still out of reach.
- **Taxi fares are an unverified reference formula**, not real Nasu tariffs.
  Substitute your own via `build_route_plan(..., fare_estimator=...)` — see
  `app/services/taxi_fare.py`.
- **Suggested itineraries are a heuristic.** `/plan/suggest` runs a beam
  search over estimated travel times, so candidates are plausible options
  to choose between rather than the provably best ones.
- **Visit order optimization is a heuristic, and off by default.** Set
  `optimize_visit_order: true` to have the intermediate stops reordered;
  it is a greedy pass plus a couple of swap rounds, not a guaranteed
  optimum, and it estimates walking times rather than routing them.
- The spot master is 30 hand-collected places, not an exhaustive dataset.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e packages/gtfs-multifeed
python -m pip install -e ".[dev]"
python -m pytest
```

Walking and taxi legs call [OpenRouteService](https://openrouteservice.org/),
so `POST /plan/route-plan` and `GET /non-transit/route` need an API key:

```bash
cp .env.example .env   # then set ORS_API_KEY (free signup)
```

## GTFS data

Real GTFS data is not committed (`data/gtfs/` is gitignored). Three feeds are
configured in `app/gtfs_feeds.py`: Nasushiobara City, Nasu Town, and Kanto
Jidousha. The first two are public and need no configuration; Kanto Jidousha
requires an API key.

```bash
cp .env.example .env   # then set KANTO_BUS_GTFS_API_KEY
python scripts/download_gtfs.py            # download every resolvable feed
python scripts/download_gtfs.py --feed nasushiobara
python scripts/download_gtfs.py --status   # last-updated time and freshness
```

Only the API key is secret — endpoints and `agency_id` live in source. Each
download records its `feed_info.txt` validity period and a checksum in
`data/gtfs_manifest.json`, which *is* committed, so `git log` doubles as an
update history.

## Running the API

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for Swagger UI.

GTFS source is picked automatically: `GTFS_DIR` if set, otherwise every valid
feed under `data/gtfs/` (merged with `"<feed_name>:"`-prefixed IDs), otherwise
`tests/fixtures/minimal_gtfs` so the API runs with no setup at all.

### Endpoints

```bash
curl "http://localhost:8000/transit/routes?origin_stop_id=stop_yumoto&destination_stop_id=stop_ropeway&departure_time=10:00&departure_date=2026-08-17"
curl "http://localhost:8000/transit/stops/nearby?lat=37.095&lng=140.001&radius_m=1000"
curl "http://localhost:8000/non-transit/route?origin_lat=37.095&origin_lng=140.001&dest_lat=37.124&dest_lng=139.985&mode=walking"
```

### `POST /plan/suggest` — which spots to visit

Answers the question before route-plan. Give it a start, a destination, a
deadline and optionally some tags, and it proposes several sets of spots
that fit, for a person to compare and choose between.

```bash
curl -X POST http://localhost:8000/plan/suggest \
  -H "Content-Type: application/json" \
  -d '{"base_location_id": "station_nasushiobara",
       "destination_spot_id": "spot_hotel_epinard",
       "departure_time": "09:00", "departure_date": "2026-10-15",
       "arrive_by": "18:00", "spot_count": 2, "tags": ["牧場", "動物"]}'
```

Spots carry `tags` (牧場 / 登山 / 動物 / 温泉 / 美術館 …, see `GET /spots`);
`tags` keeps only spots with at least one of them, and
`must_include_spot_ids` pins particular places in regardless. Candidates
always differ in *which* spots they contain — re-orderings of the same
places are not offered as separate choices.

This deliberately never calls OpenRouteService: it estimates walking from
straight-line distance, which keeps a suggestion to a few seconds and
spends no routing quota. Take a candidate's `spot_ids`, append
`destination_spot_id`, and pass them to `/plan/route-plan` for the real
itinerary.

`POST /plan/graph-data` returns direct bus options for every ordered pair
among 2–15 `stop_ids` in one call, so an agent doesn't need one request per
pair. Bus-only for now — walking/taxi edges are not merged in.

Every ORS call shares one in-process rate limit
(`app/services/rate_limiter.py`, 30/min by default) to stay under the free
tier; exceeding it returns HTTP 429, and an ORS-side failure returns 503.

### `POST /plan/route-plan` — the itinerary builder

Builds one bus+walking itinerary from a base location through a list of
spots. Designed for an LLM agent: a well-formed request always returns HTTP
200 — either `status: "success"` with a plan, or `status: "error"` with an
`ai_hint` explaining what to change. Only malformed requests (unknown spot ID,
bad date) return 4xx.

```bash
curl -X POST http://localhost:8000/plan/route-plan \
  -H "Content-Type: application/json" \
  -d '{"base_location_id": "station_nasushiobara",
       "target_spot_ids": [{"spot_id": "spot_nasu_ropeway", "dwell_minutes": 90}],
       "departure_time": "10:00", "departure_date": "2026-08-17"}'
```

The trip **ends at the last entry** in `target_spot_ids` — one-way by default.
For a round trip, list `base_location_id` again as the final target (its
`dwell_minutes` is ignored, but `required_arrival_time` still applies, e.g.
"must be back by 20:00").

Each target accepts:

- `dwell_minutes` — how long to stay (default 60)
- `required_arrival_time` — a fixed reservation/meeting time. Arriving early
  inserts a `wait` step; arriving late makes the plan infeasible rather than
  silently missing it.
- `required` — if `false`, the spot is dropped and the rest replanned when it
  can't be fitted in, instead of failing the whole request.

Request-level options: `transfer_buffer_minutes` adds slack before every bus
boarding, `max_transfers` (default 1) controls whether a leg may change buses,
and `optimize_visit_order` (default false) reorders the intermediate targets to
finish sooner — the last entry stays put, since it is the destination.

Spots come from `data/spots/nasu_spots.json` (`spot_id`/`name`/`lat`/`lng`;
override with `SPOTS_FILE`) — not GTFS `stop_id`s. The nearest usable GTFS
stop is resolved on the fly. Each step carries a `path` of `[lat, lng]`
points: real road geometry from ORS for walking/taxi, the GTFS shape sliced to
the ridden segment for bus.

## Debug UI

```bash
uvicorn app.main:app --reload   # then open http://localhost:8000/ui/
```

A single-page Leaflet map (no build step, plain HTML/JS in `app/static/`):
shows every loaded GTFS stop and route shape, searches stops by name, and
displays and adds spots.

Build a route by typing spot names into the ルート作成 box and clicking matches
(1st = base, rest = targets in order) — or toggle 地図クリックで経由地を追加 to
click markers on the map instead. The last target shows as 終点 unless
往復にする is checked. Results open in a new tab: a map of the itinerary's real
geometry alongside a timeline, with rows that zoom the map when clicked.

The おすすめ行程を提案 panel covers the other direction: pick a start, a
destination, a deadline and some tags, and candidate itineraries open in
their own tab as cards — a sketch map plus a schedule each — with a button
on each to build it for real and land in the full results view.

Tick 訪問順をおまかせにする to let the planner choose the order of the
intermediate stops; their badges switch from numbers to 経由 to show the order
is no longer yours. Per-spot ⚙ settings cover `dwell_minutes`,
`required_arrival_time`, and `required`; a global input sets
`transfer_buffer_minutes`.

## Documents

- `docs/project-instruction.md` — original project instruction
- `docs/spec-review.md` — implementation review and open questions
- `docs/implementation-plan.md` — living implementation plan and change log

## License

MIT — see [LICENSE](LICENSE).
