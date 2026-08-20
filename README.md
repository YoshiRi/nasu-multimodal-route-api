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

Run the API (defaults to `tests/fixtures/minimal_gtfs` until real GTFS data is available; override with `GTFS_DIR`):

```bash
uvicorn app.main:app --reload
```

Then open `http://localhost:8000/docs` for Swagger UI, or call directly:

```bash
curl "http://localhost:8000/transit/routes?origin_stop_id=stop_yumoto&destination_stop_id=stop_ropeway&departure_time=10:00&departure_date=2026-08-17"
curl "http://localhost:8000/transit/stops/nearby?lat=37.095&lng=140.001&radius_m=1000"
```

## Documents

- `docs/project-instruction.md`: original project instruction
- `docs/spec-review.md`: implementation review and open questions
- `docs/implementation-plan.md`: living implementation plan
