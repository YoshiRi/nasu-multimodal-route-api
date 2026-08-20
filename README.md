# Nasu Multimodal Route API

PoC backend for multimodal route planning in the Nasu area using GTFS bus data, walking routes, and taxi-like driving estimates.

Current focus: Phase 1 `GTFSEngine` for direct bus search from GTFS CSV files.

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

## Documents

- `docs/project-instruction.md`: original project instruction
- `docs/spec-review.md`: implementation review and open questions
- `docs/implementation-plan.md`: living implementation plan
