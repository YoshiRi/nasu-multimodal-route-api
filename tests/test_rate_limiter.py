from __future__ import annotations

import time

import pytest

from app.services.rate_limiter import RateLimitExceededError, SlidingWindowRateLimiter


def test_acquire_allows_calls_within_the_limit() -> None:
    limiter = SlidingWindowRateLimiter(max_calls=3, period_seconds=60)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()


def test_acquire_raises_once_limit_is_reached() -> None:
    limiter = SlidingWindowRateLimiter(max_calls=2, period_seconds=60)

    limiter.acquire()
    limiter.acquire()

    with pytest.raises(RateLimitExceededError):
        limiter.acquire()


def test_acquire_allows_calls_again_after_the_window_passes() -> None:
    limiter = SlidingWindowRateLimiter(max_calls=1, period_seconds=0.05)

    limiter.acquire()
    with pytest.raises(RateLimitExceededError):
        limiter.acquire()

    time.sleep(0.06)
    limiter.acquire()
