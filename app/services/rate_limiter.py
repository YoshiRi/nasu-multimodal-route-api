"""Generic in-process sliding-window rate limiter.

Single-process only (an in-memory deque) — good enough for a PoC running
as one uvicorn worker. Would need a shared store (e.g. Redis) to hold
across multiple workers/processes.
"""

from __future__ import annotations

import time
from collections import deque


class RateLimitExceededError(RuntimeError):
    """Raised when a call would exceed the configured rate limit."""


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, period_seconds: float) -> None:
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._timestamps: deque[float] = deque()

    def acquire(self) -> None:
        """Raise RateLimitExceededError if the limit is already reached;
        otherwise record this call and return."""

        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] >= self.period_seconds:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_calls:
            raise RateLimitExceededError(
                f"Rate limit exceeded: {self.max_calls} calls per {self.period_seconds:.0f}s"
            )

        self._timestamps.append(now)
