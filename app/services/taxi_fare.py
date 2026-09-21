"""Estimated taxi fare from distance.

ORS gives distance and duration, not fare, so the fare has to be modelled.

**The bundled numbers are an unverified reference value, not real rates.**
They describe a generic Japanese metered-fare shape (a flat base for the
first stretch, then a per-km rate) and have NOT been confirmed against
actual Nasu-area taxi tariffs. Treat estimate_fare_jpy()'s output as
illustrative only.

To use real rates, pass your own callable as `fare_estimator` to
build_route_plan() (app/services/route_planner.py) — any
`(distance_m: float) -> int` works. Use make_fare_estimator() to keep this
formula's shape with different constants, or write your own for a tariff
that doesn't fit it (e.g. time-based charging, night surcharges).
"""

from __future__ import annotations

from collections.abc import Callable

BASE_FARE_JPY = 500
BASE_DISTANCE_KM = 1.0
RATE_PER_KM_JPY = 400


def estimate_fare_jpy(distance_m: float) -> int:
    """Return an estimated taxi fare in JPY for distance_m meters.

    Uses the module's unverified default constants — see module docstring.
    """

    return make_fare_estimator()(distance_m)


def make_fare_estimator(
    base_fare_jpy: int = BASE_FARE_JPY,
    base_distance_km: float = BASE_DISTANCE_KM,
    rate_per_km_jpy: int = RATE_PER_KM_JPY,
) -> Callable[[float], int]:
    """Build a fare estimator for a "flat base, then per-km" tariff."""

    def estimator(distance_m: float) -> int:
        distance_km = distance_m / 1000
        extra_km = max(0.0, distance_km - base_distance_km)
        return round(base_fare_jpy + extra_km * rate_per_km_jpy)

    return estimator
