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

## Documents

- `docs/project-instruction.md`: original project instruction
- `docs/spec-review.md`: implementation review and open questions
- `docs/implementation-plan.md`: living implementation plan
