# SPDX-License-Identifier: AGPL-3.0-or-later
"""Credential harvesting from a real browser session.

Neither the session cookie nor a signature can be minted offline: every
``webapi.rednote.com`` endpoint -- the QR login flow included -- is signed, and
the QR status poll is a GET whose signature covers its query string, so it
cannot be replayed. Renewal therefore has to go through a browser exactly once
per session lifetime; this module keeps that browser out of the scraping path.

A dedicated Playwright profile is used rather than the user's own browser
profile: Chromium-based browsers on Windows bind their cookie store to the
application (app-bound encryption), so reading their cookies from outside is
not dependable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .credentials import PROFILE_DIR, SEARCH_PATH, Credentials, format_cookie
from .errors import BrowserUnavailableError, CredentialsError

SEARCH_PAGE = "https://www.rednote.com/search_result?keyword={keyword}"
LOGIN_PAGE = "https://www.rednote.com/explore"
COOKIE_DOMAIN = "https://www.rednote.com"

_SIGNATURE_HEADERS = ("x-s", "x-t", "x-s-common")


async def harvest(
    *,
    profile_dir: Path = PROFILE_DIR,
    headless: bool = True,
    keyword: str = "logo",
    timeout: float = 60.0,
    user_agent: str = "",
) -> Credentials:
    """Drive a browser through one search and capture cookies and signature.

    With ``headless=False`` the window stays open until the login completes,
    which is how the profile is seeded the first time.
    """
    async_playwright = _import_playwright()
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=headless
        )
        try:
            page = await context.new_page()
            captured: dict[str, str] = {}

            def on_request(request: object) -> None:
                if SEARCH_PATH in request.url and not captured:  # type: ignore[attr-defined]
                    captured.update(request.headers)  # type: ignore[attr-defined]

            page.on("request", on_request)
            await page.goto(
                SEARCH_PAGE.format(keyword=keyword), wait_until="domcontentloaded"
            )
            await _wait_for(lambda: "x-s" in captured, timeout)

            if "x-s" not in captured:
                raise CredentialsError(
                    "the search request was never issued -- the page is most "
                    "likely showing a login wall; run 'rednote auth login' once "
                    "to sign in to the scraper's browser profile"
                )

            cookies = await context.cookies(COOKIE_DOMAIN)
            cookie = format_cookie({c["name"]: c["value"] for c in cookies})
            signature = {k: captured[k] for k in _SIGNATURE_HEADERS if k in captured}
            agent = user_agent or captured.get("user-agent", "")
            return Credentials(
                cookie=cookie,
                user_agent=agent,
                signatures={SEARCH_PATH: signature},
            )
        finally:
            await context.close()


async def login(*, profile_dir: Path = PROFILE_DIR, timeout: float = 300.0) -> None:
    """Open a visible browser and wait until the session cookie appears."""
    async_playwright = _import_playwright()
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=False
        )
        try:
            page = await context.new_page()
            await page.goto(LOGIN_PAGE, wait_until="domcontentloaded")
            print("sign in to rednote.com in the opened window...")

            async def logged_in() -> bool:
                cookies = await context.cookies(COOKIE_DOMAIN)
                return any(c["name"] == "web_session" and c["value"] for c in cookies)

            await _wait_for(logged_in, timeout)
            if not await logged_in():
                raise CredentialsError("login timed out; no web_session cookie was set")
        finally:
            await context.close()


async def _wait_for(predicate: object, timeout: float, interval: float = 0.5) -> None:
    """Poll ``predicate`` (sync or async) until it is true or time runs out."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = predicate()  # type: ignore[operator]
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)


def _import_playwright():  # type: ignore[no-untyped-def]
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise BrowserUnavailableError(
            "playwright is required for browser-based credential refresh: "
            "pip install 'rednote-scraper[browser]' && playwright install chromium"
        ) from exc
    return async_playwright
