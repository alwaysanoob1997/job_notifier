"""Reusable client-side rate limiting for LLM providers.

The :class:`SlidingWindowLimiter` enforces ``max_requests`` per ``window_seconds``
using a deque of timestamps (the oldest entry expires once it falls outside the
window). :class:`CompositeLimiter` chains several windows so a single ``acquire``
respects whichever limit is currently the tightest — used for OpenRouter's free
tier which combines ``20 req/min`` with ``50``- or ``1000``-``req/day``.

These are intentionally synchronous + blocking: the LLM scoring loop already runs
on a worker thread (APScheduler / FastAPI background) and a quiet ``time.sleep``
inside a per-job iteration is the simplest way to stay under the limit without
restructuring the loop. Both classes accept an injectable ``clock`` and ``sleep``
so tests can run them deterministically without real time passing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Deque

_logger = logging.getLogger(__name__)

ClockFn = Callable[[], float]
SleepFn = Callable[[float], None]


def _default_clock() -> float:
    """Late-binding clock so tests can patch ``time.monotonic`` after construction."""
    return time.monotonic()


def _default_sleep(seconds: float) -> None:
    """Late-binding sleep so tests can patch ``time.sleep`` after construction."""
    time.sleep(seconds)


class SlidingWindowLimiter:
    """Thread-safe sliding-window limiter: at most ``max_requests`` in any ``window_seconds``."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        name: str = "limiter",
        clock: ClockFn | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max = int(max_requests)
        self._window = float(window_seconds)
        self._name = name
        self._clock = clock if clock is not None else _default_clock
        self._sleep = sleep if sleep is not None else _default_sleep
        self._lock = threading.Lock()
        self._timestamps: Deque[float] = deque()
        # When the server tells us "wait at least N seconds" we honour it before any local check.
        self._next_allowed: float = 0.0

    @property
    def name(self) -> str:
        return self._name

    @property
    def max_requests(self) -> int:
        return self._max

    @property
    def window_seconds(self) -> float:
        return self._window

    def _purge(self, now: float) -> None:
        cutoff = now - self._window
        ts = self._timestamps
        while ts and ts[0] <= cutoff:
            ts.popleft()

    def time_until_available(self) -> float:
        """Seconds until the next ``acquire`` could pass without waiting (0 when free now)."""
        with self._lock:
            now = self._clock()
            self._purge(now)
            wait_for_server_cooldown = max(0.0, self._next_allowed - now)
            if len(self._timestamps) < self._max:
                return wait_for_server_cooldown
            oldest = self._timestamps[0]
            wait_for_window = (oldest + self._window) - now
            return max(0.0, wait_for_server_cooldown, wait_for_window)

    def acquire(self) -> float:
        """Block until a slot is free, record one usage, and return how long we waited."""
        slept_total = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._purge(now)
                if now < self._next_allowed:
                    wait = self._next_allowed - now
                else:
                    wait = 0.0
                if wait <= 0 and len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return slept_total
                if wait <= 0:
                    oldest = self._timestamps[0]
                    wait = (oldest + self._window) - now
                    if wait <= 0:
                        # Race with another thread purging; loop and retry.
                        continue
            # Release the lock while sleeping so other threads can probe time_until_available.
            _logger.info(
                "rate-limit %s: waiting %.2fs (capacity %d/%ds)",
                self._name,
                wait,
                self._max,
                int(self._window),
            )
            self._sleep(wait)
            slept_total += wait

    def note_429(self, retry_after_seconds: float) -> None:
        """Server told us to back off; reject acquires for ``retry_after_seconds`` from now."""
        if retry_after_seconds is None or retry_after_seconds <= 0:
            return
        with self._lock:
            now = self._clock()
            target = now + float(retry_after_seconds)
            if target > self._next_allowed:
                self._next_allowed = target

    def reset(self) -> None:
        with self._lock:
            self._timestamps.clear()
            self._next_allowed = 0.0


class CompositeLimiter:
    """Acquires every child limiter; the slowest child sets the wait."""

    def __init__(self, limiters: list[SlidingWindowLimiter], *, name: str = "composite") -> None:
        self._limiters = list(limiters)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def limiters(self) -> list[SlidingWindowLimiter]:
        return list(self._limiters)

    def acquire(self) -> float:
        # Acquire each in order. If a later one waits, that wait time also counts toward the
        # earlier limiter's window — but those slots stay "spent" because we already recorded
        # them. That's intentional: client-side throttling that under-counts is safer than
        # over-counting since the server is the source of truth.
        slept_total = 0.0
        for limiter in self._limiters:
            slept_total += limiter.acquire()
        return slept_total

    def note_429(self, retry_after_seconds: float) -> None:
        for limiter in self._limiters:
            limiter.note_429(retry_after_seconds)

    def reset(self) -> None:
        for limiter in self._limiters:
            limiter.reset()

    def time_until_available(self) -> float:
        return max((lim.time_until_available() for lim in self._limiters), default=0.0)
