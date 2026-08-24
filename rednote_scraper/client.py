# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asynchronous rednote client: search, detail resolution and media download."""

from __future__ import annotations

import asyncio
import random
import string
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

from .credentials import API_HOST, SEARCH_PATH, Credentials
from .errors import ApiError, SessionExpiredError, SignatureExpiredError
from .models import Note
from .parsing import apply_video_detail, parse_note_page, parse_search_item

SEARCH_URL = f"https://{API_HOST}{SEARCH_PATH}"
ITEM_URL = "https://www.rednote.com/discovery/item/{note_id}"
PAGE_SIZE = 20

#: -101 no session, -100 session expired, -3 session present but unusable.
SESSION_ERROR_CODES = frozenset({-3, -100, -101})

SORTS = (
    "general",
    "time_descending",
    "popularity_descending",
    "comment_descending",
    "collect_descending",
)


def new_search_id() -> str:
    """Mimic the web client's per-session search identifier."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=21))


class RednoteClient:
    """Thin async wrapper around the endpoints the scraper needs."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        concurrency: int = 8,
        timeout: float = 20.0,
    ) -> None:
        self._credentials = credentials
        self._semaphore = asyncio.Semaphore(concurrency)
        limits = httpx.Limits(
            max_connections=concurrency * 2, max_keepalive_connections=concurrency * 2
        )
        self._http = httpx.AsyncClient(
            http2=True, timeout=timeout, limits=limits, follow_redirects=True
        )

    async def __aenter__(self) -> "RednoteClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(
        self,
        keyword: str,
        *,
        limit: int = 40,
        sort: str = "general",
        originals: bool = True,
    ) -> list[Note]:
        """Fetch up to ``limit`` notes, requesting all result pages in parallel."""
        pages = max(1, -(-limit // PAGE_SIZE))
        search_id = new_search_id()
        results = await asyncio.gather(
            *(
                self._search_page(keyword, page, search_id, sort)
                for page in range(1, pages + 1)
            )
        )

        notes: list[Note] = []
        seen: set[str] = set()
        for items in results:
            for item in items:
                note = parse_search_item(item, originals=originals)
                if note is None or note.note_id in seen:
                    continue
                seen.add(note.note_id)
                notes.append(note)
        return notes[:limit]

    async def resolve_videos(self, notes: Sequence[Note]) -> list[Note]:
        """Populate video stream URLs for every video note, in parallel."""
        targets = [note for note in notes if note.type == "video" and not note.video]
        await asyncio.gather(*(self._resolve_video(note) for note in targets))
        return list(notes)

    async def download(self, url: str, destination: Path) -> Path | None:
        """Stream a media file to disk, skipping files already downloaded."""
        if destination.exists() and destination.stat().st_size > 0:
            return destination
        partial = destination.with_suffix(destination.suffix + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self._semaphore:
            try:
                async with self._http.stream(
                    "GET", url, headers={"user-agent": self._credentials.user_agent}
                ) as response:
                    response.raise_for_status()
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            handle.write(chunk)
            except (httpx.HTTPError, OSError):
                partial.unlink(missing_ok=True)
                return None
        partial.replace(destination)
        return destination

    async def _search_page(
        self, keyword: str, page: int, search_id: str, sort: str
    ) -> list[dict[str, Any]]:
        payload = {
            "keyword": keyword,
            "page": page,
            "page_size": PAGE_SIZE,
            "search_id": search_id,
            "sort": sort,
            "note_type": 0,
            "ext_flags": [],
            "geo": "",
            "image_formats": ["jpg", "webp", "avif"],
        }
        async with self._semaphore:
            response = await self._http.post(
                SEARCH_URL,
                json=payload,
                headers=self._credentials.headers_for(SEARCH_PATH),
            )
        response.raise_for_status()
        body = response.json()
        code = body.get("code", 0)
        if code == -1:
            raise SignatureExpiredError(
                "request rejected -- the captured x-s signature is no longer "
                "accepted; refresh it with 'rednote auth refresh'"
            )
        if code in SESSION_ERROR_CODES:
            raise SessionExpiredError(
                f"session cookie rejected (code {code}: {body.get('msg', '')}) -- "
                f"refresh it with 'rednote auth refresh' or 'rednote auth cookie'"
            )
        if not body.get("success"):
            raise ApiError(code, body.get("msg", "unknown error"))
        return body.get("data", {}).get("items") or []

    async def _resolve_video(self, note: Note) -> None:
        params = {"xsec_token": note.xsec_token, "xsec_source": "pc_search"}
        async with self._semaphore:
            try:
                response = await self._http.get(
                    ITEM_URL.format(note_id=note.note_id),
                    params=params,
                    headers=self._credentials.web_headers(),
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return
        detail = parse_note_page(response.text, note.note_id)
        if detail:
            apply_video_detail(note, detail)


def media_targets(note: Note) -> Iterable[tuple[str, str]]:
    """Yield ``(url, filename)`` pairs for every downloadable asset of a note."""
    if note.video:
        yield note.video, f"{note.note_id}.mp4"
    for index, url in enumerate(note.images, start=1):
        yield url, f"{note.note_id}_{index:02d}.jpg"
    if not note.images and not note.video and note.cover:
        yield note.cover, f"{note.note_id}_cover.jpg"
