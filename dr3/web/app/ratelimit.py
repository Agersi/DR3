"""Tiny in-memory fixed-window rate limiter (no external dependency — the
VPS deploy does not pip install).

State is per worker: gunicorn runs several workers, so effective limits are
per worker. That is plenty for brute-force protection on a community server;
windows also reset on process restart, which is acceptable since the goal is
slowing automated abuse, not metering legitimate traffic."""
from __future__ import annotations

import time
from threading import Lock


class _FixedWindow:
    def __init__(self) -> None:
        self._lock = Lock()
        self._hits: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, max_hits: int, window_s: int) -> bool:
        """True when the call is allowed (and counted), False when over budget."""
        now = time.monotonic()
        with self._lock:
            count, reset_at = self._hits.get(key, (0, 0.0))
            if now >= reset_at:
                count, reset_at = 0, now + window_s
            if count >= max_hits:
                return False
            self._hits[key] = (count + 1, reset_at)
            if len(self._hits) > 10_000:
                stale = [k for k, (_c, r) in self._hits.items() if now >= r]
                for k in stale:
                    del self._hits[k]
            return True


limiter = _FixedWindow()
