# Nasu Multimodal Route API

PoC backend for multimodal route planning in the Nasu area using GTFS bus data, walking routes, and taxi-like driving estimates.

Current focus: bus search (`/transit/stops/nearby`, `/transit/routes`), walking/taxi point-to-point estimates (`GET /non-transit/route`), a bus-only batch cost graph (`POST /plan/graph-data`), and a bus+walking itinerary builder for an AI travel concierge (`POST /plan/route-plan`). Walking and taxi both go through OpenRouteService instead of Google Maps.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

## Phase 1 CLI

Search direct bus trips from GTFS CSV files:

```bash
python -m app.cli search-bus \
  --gtfs-dir tests/fixtures/minimal_gtfs \
  --from stop_yumoto \
  --to stop_ropeway \
  --time 10:00 \
  --date 2026-08-17
```

After installing the package, the console script is also available:

```bash
nasu-route search-bus \
  --gtfs-dir tests/fixtures/minimal_gtfs \
  --from stop_yumoto \
  --to stop_ropeway \
  --time 10:00 \
  --date 2026-08-17
```

## Phase 2 API

```bash
uvicorn app.main:app --reload
```

GTFS source is picked automatically, in this order:

1. `GTFS_DIR` env var, if set — a single directory, IDs used as-is.
2. Otherwise, every valid feed subdirectory under `data/gtfs/` (populated by `scripts/download_gtfs.py`). One feed is used as-is; more than one is merged with `"<feed_name>:"`-prefixed IDs (e.g. `nasushiobara:1_01`) to keep operators' IDs from colliding.
3. Otherwise, falls back to `tests/fixtures/minimal_gtfs` so the API runs with no setup.

Open `http://localhost:8000/docs` for Swagger UI, or call directly:

```bash
curl "http://localhost:8000/transit/routes?origin_stop_id=stop_yumoto&destination_stop_id=stop_ropeway&departure_time=10:00&departure_date=2026-08-17"
curl "http://localhost:8000/transit/stops/nearby?lat=37.095&lng=140.001&radius_m=1000"
curl -X POST http://localhost:8000/plan/graph-data \
  -H "Content-Type: application/json" \
  -d '{"stop_ids": ["stop_yumoto", "stop_ropeway"], "departure_time": "09:00"}'
```

`POST /plan/graph-data` returns direct bus options for every ordered pair among 2-15 given `stop_ids` in one call, so an AI agent doesn't need one `/transit/routes` request per pair. It's bus-only for now (`mode` is always `"bus"`); walking/taxi edges are not merged in yet even though `/non-transit/route` (below) is available.

## Walking/taxi estimates

```bash
curl "http://localhost:8000/non-transit/route?origin_lat=37.095&origin_lng=140.001&dest_lat=37.124&dest_lng=139.985&mode=walking"
curl "http://localhost:8000/non-transit/route?origin_lat=37.095&origin_lng=140.001&dest_lat=37.124&dest_lng=139.985&mode=taxi"
```

Both modes call OpenRouteService (`ORS_API_KEY` required); `taxi` adds a `cost_jpy` estimate from `app/services/taxi_fare.py` — **a placeholder formula, not confirmed against real Nasu-area taxi rates**. Every ORS call (from this endpoint and from `/plan/route-plan`) shares one in-process rate limit (`app/services/rate_limiter.py`, 30/min by default) to stay under ORS's free-tier quota; exceeding it returns HTTP 429, and an ORS-side failure returns HTTP 503.

## Phase 4: route-plan (AI travel concierge)

`POST /plan/route-plan` builds one bus+walking itinerary (base → each target spot in the given order → base), calling `GTFSEngine` for bus legs and [OpenRouteService](https://openrouteservice.org/) for walking legs. It's designed for an LLM agent, not a human: a well-formed request always returns HTTP 200 — `status: "success"` with a `plans` entry, or `status: "error"` with an `ai_hint` explaining what to change (e.g. too many spots for the remaining bus schedule) and no plans. Malformed requests (unknown spot ID, bad date) still return HTTP 4xx.

```bash
cp .env.example .env   # then set ORS_API_KEY (free signup at openrouteservice.org)
uvicorn app.main:app --reload
curl -X POST http://localhost:8000/plan/route-plan \
  -H "Content-Type: application/json" \
  -d '{"base_location_id": "spot_nasu_yumoto_onsen", "target_spot_ids": [{"spot_id": "spot_nasu_ropeway", "dwell_minutes": 90}], "departure_time": "10:00", "departure_date": "2026-08-17"}'
```

Spots (`base_location_id`/`target_spot_ids`) come from `data/spots/nasu_spots.json` (`spot_id`/`name`/`lat`/`lng`; override with `SPOTS_FILE`) — not GTFS `stop_id`s. Seeded with 29 real Nasu-area spots (Nasu Town's well-known attractions plus Nasushiobara City's official open-data tourism spots); add more via `POST /spots` or the debug UI. The nearest GTFS stop to each spot is resolved on the fly via `GTFSEngine.nearby_stops()`. Visit order currently follows `target_spot_ids` as given (no route optimization yet). Each target spot takes an optional `dwell_minutes` (defaults to 60 if omitted) for how long to stay there, and an optional `required_arrival_time` ("HH:MM") for a fixed reservation/meeting time: arriving early inserts a `"wait"` step so the stay starts exactly on time, arriving too late makes the whole plan infeasible (`status: "error"`) instead of silently missing it. `app/services/ors_client.py`'s response parsing has been verified against a live `ORS_API_KEY`.

## Debug UI

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/ui/` for a single-page Leaflet map (no build step, plain HTML/JS under `app/static/`): shows every currently-loaded GTFS stop and route shape (`GET /transit/network`), lets you search stops by name, shows spots (`GET /spots`), and lets you add a spot by clicking the map (`POST /spots`, persisted to `data/spots/*.json`).

Toggle "ルート作成モード" and click existing spot markers in order (1st = base, rest = targets) to build a `/plan/route-plan` request from the date/time inputs; the result opens in a new tab (`results.html`) with a left/right split — a map of the itinerary's legs and a horizontal timeline — sharing data via `localStorage`. Clicking a timeline row zooms the map to that step.

Each selected target spot has a "⚙" settings toggle for `dwell_minutes`, `required_arrival_time` (reservation), and `required` (マスト — if unchecked, the spot is dropped and the rest replanned when it can't be fitted in, instead of failing the whole request). A global "バス乗り継ぎの余裕" input adds `transfer_buffer_minutes` of slack before every bus boarding, to absorb real-world schedule variance.

## GTFS data

Real GTFS data is not committed (`data/gtfs/` is gitignored). The two public feeds (nasushiobara, nasutown) have a non-secret URL baked into `scripts/download_gtfs.py`, so no configuration is needed for them. The kanto_auto feed requires an API key:

```bash
cp .env.example .env   # then set KANTO_BUS_GTFS_API_KEY
python scripts/download_gtfs.py            # download every resolvable feed
python scripts/download_gtfs.py --feed nasushiobara
python scripts/download_gtfs.py --status    # last-updated time and freshness per feed
```

Only the API key is secret — the kanto_auto endpoint and `agency_id` live in source. Any feed's URL can still be overridden via its `<SLUG>_GTFS_URL` env var (e.g. to point at a local test server) if needed.

Each successful download records its `feed_info.txt` validity period and a checksum in `data/gtfs_manifest.json`, which is committed (unlike the raw GTFS files) so `git log` doubles as an update history.

## Documents

- `docs/project-instruction.md`: original project instruction
- `docs/spec-review.md`: implementation review and open questions
- `docs/implementation-plan.md`: living implementation plan
