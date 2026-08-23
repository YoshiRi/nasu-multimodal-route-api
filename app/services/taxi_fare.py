"""Estimated taxi fare from distance/duration.

ORS gives distance and duration, not fare. The constants below are a
generic placeholder (typical small/mid Japanese taxi metered-fare shape:
a flat base for the first stretch, then a per-km rate) — NOT confirmed
against actual Nasu-area taxi rates. Treat estimate_fare_jpy()'s output
as illustrative only until real local rates are supplied, then adjust
these constants (or make them configurable) accordingly.
"""

from __future__ import annotations

BASE_FARE_JPY = 500
BASE_DISTANCE_KM = 1.0
RATE_PER_KM_JPY = 400


def estimate_fare_jpy(distance_m: float) -> int:
    """Return an estimated taxi fare in JPY for distance_m meters."""

    distance_km = distance_m / 1000
    extra_km = max(0.0, distance_km - BASE_DISTANCE_KM)
    return round(BASE_FARE_JPY + extra_km * RATE_PER_KM_JPY)
