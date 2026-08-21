# Nasu Multimodal Route API

PoC backend for multimodal route planning in the Nasu area using GTFS bus data, walking routes, and taxi-like driving estimates.

Current focus: Phase 2 FastAPI routers for bus search (`/transit/stops/nearby`, `/transit/routes`), backed by the Phase 1 `GTFSEngine`. Walking/taxi (`/non-transit/route`) is deliberately locked out until a Google Maps API key is available.

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
```

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
