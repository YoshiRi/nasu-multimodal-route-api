from __future__ import annotations

from app.services.taxi_fare import BASE_FARE_JPY, estimate_fare_jpy


def test_estimate_fare_returns_base_fare_within_base_distance() -> None:
    assert estimate_fare_jpy(500) == BASE_FARE_JPY
    assert estimate_fare_jpy(1000) == BASE_FARE_JPY


def test_estimate_fare_adds_per_km_rate_beyond_base_distance() -> None:
    # 2km total = 1km base + 1km extra @ 400 JPY/km
    assert estimate_fare_jpy(2000) == BASE_FARE_JPY + 400


def test_estimate_fare_is_never_negative_for_zero_distance() -> None:
    assert estimate_fare_jpy(0) == BASE_FARE_JPY
