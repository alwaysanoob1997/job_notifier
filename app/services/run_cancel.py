"""Per-run cancel signals shared by scrape and LLM-scoring workers.

The web layer asks for cancellation by run_id; the worker threads check the
event between work units (per scraped listing or per scored listing). Events
are kept in-memory only — sufficient for a single-process desktop app.
"""

from __future__ import annotations

import threading


class RunCancelled(Exception):
    """Raised inside a worker callback to break out of the scraper library cleanly."""


_lock = threading.Lock()
_events: dict[int, threading.Event] = {}


def register(run_id: int) -> threading.Event:
    """Create (or replace) a fresh cancel event for ``run_id`` and return it."""
    ev = threading.Event()
    with _lock:
        _events[run_id] = ev
    return ev


def request_cancel(run_id: int) -> bool:
    """Set the cancel event if one is registered. Returns True if a worker is listening."""
    with _lock:
        ev = _events.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def is_cancelled(run_id: int) -> bool:
    with _lock:
        ev = _events.get(run_id)
    return bool(ev is not None and ev.is_set())


def discard(run_id: int) -> None:
    """Forget the event for ``run_id`` once the worker is done."""
    with _lock:
        _events.pop(run_id, None)
