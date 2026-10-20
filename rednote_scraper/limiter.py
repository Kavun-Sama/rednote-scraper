# SPDX-License-Identifier: AGPL-3.0-or-later
"""Request limits: concurrency cap, pacing between API requests, result ceiling."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .errors import LimitError


@dataclass(frozen=True, slots=True)
class Limits:
    """Bounds applied to outgoing requests.

    ``concurrency`` caps the requests in flight. ``delay`` is the minimum
    interval in seconds between the starts of two API requests (search pages
    and note-page lookups); media downloads are not paced. ``max_results``
    caps the notes a single search may request, ``None`` removes the cap.
    """

    concurrency: int
    delay: float
    max_results: int | None

    def check_results(self, requested: int) -> None:
        """Raise :class:`LimitError` if ``requested`` exceeds the result cap."""
        if self.max_results is not None and requested > self.max_results:
            raise LimitError(
                f"requested {requested} results, above the limit of "
                f"{self.max_results}; raise it with --max-results or lift all "
                f"limits with --no-limits"
            )


DEFAULT_LIMITS = Limits(concurrency=4, delay=0.5, max_results=200)

#: The behaviour before limits existed: eight requests in flight, no pacing,
#: no result cap.
LEGACY_LIMITS = Limits(concurrency=8, delay=0.0, max_results=None)


class RequestLimiter:
    """Bounds concurrency and spaces out request starts."""

    def __init__(
        self,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self._semaphore = asyncio.Semaphore(limits.concurrency)
        self._delay = limits.delay
        self._clock = clock
        self._sleep = sleep
        self._next_start = float("-inf")

    @asynccontextmanager
    async def slot(self, *, paced: bool = True) -> AsyncIterator[None]:
        """Hold a concurrency slot; ``paced`` also waits for this request's turn."""
        async with self._semaphore:
            if paced:
                await self._wait_turn()
            yield

    async def _wait_turn(self) -> None:
        if self._delay <= 0:
            return
        now = self._clock()
        start = max(now, self._next_start)
        self._next_start = start + self._delay
        if start > now:
            await self._sleep(start - now)
