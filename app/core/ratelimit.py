"""A small in-process sliding-window rate limiter.

This protects single-process deployments and test environments without adding
an external dependency. For multi-worker or multi-replica deployments put a
shared limiter (Redis, API gateway, or WAF) in front of the service -- see
``docs/DEPLOYMENT.md``.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Allow at most ``limit`` events per ``window_seconds`` for a given key."""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._last_sweep = 0.0

    async def hit(self, key: str) -> tuple[bool, int]:
        """Record an event.

        Returns ``(allowed, retry_after_seconds)``. When the limit is exceeded
        ``retry_after`` is the number of seconds until the oldest event expires.
        """
        now = time.monotonic()
        async with self._lock:
            queue = self._events[key]
            cutoff = now - self.window_seconds
            while queue and queue[0] <= cutoff:
                queue.popleft()

            if len(queue) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - queue[0])) + 1)
                return False, retry_after

            queue.append(now)
            await self._sweep(now)
            return True, 0

    async def reset(self) -> None:
        async with self._lock:
            self._events.clear()

    async def _sweep(self, now: float) -> None:
        """Periodically drop empty keys to bound memory usage."""
        if now - self._last_sweep < self.window_seconds:
            return
        self._last_sweep = now
        cutoff = now - self.window_seconds
        for key in list(self._events):
            queue = self._events[key]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if not queue:
                del self._events[key]
