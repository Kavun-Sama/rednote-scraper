# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio

import pytest

from rednote_scraper.cli import build_parser, resolve_limits
from rednote_scraper.client import RednoteClient
from rednote_scraper.credentials import SEARCH_PATH, Credentials
from rednote_scraper.errors import LimitError
from rednote_scraper.limiter import (
    DEFAULT_LIMITS,
    LEGACY_LIMITS,
    Limits,
    RequestLimiter,
)


def limits_for(*argv: str) -> Limits:
    return resolve_limits(build_parser().parse_args(argv))


def test_search_uses_default_limits_without_flags():
    assert limits_for("search", "kw") == DEFAULT_LIMITS


def test_no_limits_restores_the_unrestricted_behaviour():
    limits = limits_for("search", "kw", "--no-limits")
    assert limits == LEGACY_LIMITS
    assert limits.delay == 0
    assert limits.max_results is None


def test_explicit_flags_override_the_defaults():
    assert limits_for(
        "search", "kw", "-c", "2", "--delay", "1.5", "--max-results", "50"
    ) == Limits(concurrency=2, delay=1.5, max_results=50)


def test_explicit_flags_override_no_limits():
    assert limits_for(
        "search", "kw", "--no-limits", "-c", "3", "--delay", "1", "--max-results", "9"
    ) == Limits(concurrency=3, delay=1.0, max_results=9)


def test_download_limits_cover_concurrency_only():
    assert limits_for("download", "dump.json") == DEFAULT_LIMITS
    assert limits_for("download", "dump.json", "--no-limits") == LEGACY_LIMITS
    assert limits_for("download", "dump.json", "-c", "2").concurrency == 2


@pytest.mark.parametrize(
    "flags",
    [
        ("-c", "0"),
        ("-c", "many"),
        ("--delay", "-1"),
        ("--delay", "nan"),
        ("--max-results", "0"),
    ],
)
def test_invalid_limit_values_are_rejected(flags):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "kw", *flags])


def test_check_results_enforces_the_ceiling():
    limits = Limits(concurrency=1, delay=0, max_results=50)
    limits.check_results(50)
    with pytest.raises(LimitError, match="51"):
        limits.check_results(51)
    Limits(concurrency=1, delay=0, max_results=None).check_results(10**6)


def test_limiter_spaces_out_request_starts():
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run() -> None:
        limiter = RequestLimiter(
            Limits(concurrency=4, delay=0.5, max_results=None),
            clock=lambda: 100.0,
            sleep=fake_sleep,
        )

        async def request() -> None:
            async with limiter.slot():
                pass

        await asyncio.gather(*(request() for _ in range(4)))

    asyncio.run(run())
    assert waits == [0.5, 1.0, 1.5]


def test_limiter_skips_pacing_for_unpaced_slots_and_zero_delay():
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run() -> None:
        paced = RequestLimiter(
            Limits(concurrency=4, delay=0.5, max_results=None),
            clock=lambda: 100.0,
            sleep=fake_sleep,
        )
        immediate = RequestLimiter(
            Limits(concurrency=4, delay=0, max_results=None),
            clock=lambda: 100.0,
            sleep=fake_sleep,
        )

        async def request(limiter: RequestLimiter, *, paced: bool) -> None:
            async with limiter.slot(paced=paced):
                pass

        await asyncio.gather(
            *(request(paced, paced=False) for _ in range(3)),
            *(request(immediate, paced=True) for _ in range(3)),
        )

    asyncio.run(run())
    assert waits == []


def test_limiter_caps_requests_in_flight():
    in_flight = 0
    peak = 0

    async def run() -> None:
        nonlocal in_flight, peak
        limiter = RequestLimiter(Limits(concurrency=2, delay=0, max_results=None))

        async def request() -> None:
            nonlocal in_flight, peak
            async with limiter.slot():
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                in_flight -= 1

        await asyncio.gather(*(request() for _ in range(6)))

    asyncio.run(run())
    assert peak == 2


def test_search_above_the_ceiling_fails_before_any_request():
    credentials = Credentials(
        cookie="web_session=s; x-rednote-datactry=AE",
        user_agent="test-agent",
        signatures={SEARCH_PATH: {"x-s": "s", "x-t": "1"}},
    )

    async def run() -> None:
        limits = Limits(concurrency=2, delay=0, max_results=50)
        async with RednoteClient(credentials, limits=limits) as client:
            with pytest.raises(LimitError, match="--max-results"):
                await client.search("kw", limit=51)

    asyncio.run(run())
