# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import browser
from .client import SORTS, RednoteClient, media_targets
from .credentials import (
    DEFAULT_PATH,
    PROFILE_DIR,
    Credentials,
    check_cookie,
    from_har,
)
from .errors import RednoteError, SessionExpiredError, SignatureExpiredError
from .models import Note

TYPE_FILTERS = {"all": None, "video": "video", "image": "normal"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rednote", description="Fast CLI scraper for rednote.com"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="manage session credentials")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    import_har = auth_sub.add_parser(
        "import-har", help="extract cookies and signatures from HAR captures"
    )
    import_har.add_argument("har", nargs="+", type=Path, help="HAR file or files")
    import_har.add_argument("-o", "--output", type=Path, default=DEFAULT_PATH)
    cookie = auth_sub.add_parser(
        "cookie", help="replace the session cookie, keeping stored signatures"
    )
    cookie.add_argument(
        "value",
        help="cookie header copied from the browser, or just 'web_session=...'",
    )
    auth_sub.add_parser(
        "login", help="open a browser once to sign in to the scraper profile"
    )
    refresh = auth_sub.add_parser(
        "refresh", help="re-harvest cookie and signature from the browser profile"
    )
    refresh.add_argument(
        "--headful", action="store_true", help="show the browser window"
    )
    auth_sub.add_parser("show", help="print the stored credential summary")

    search = subparsers.add_parser("search", help="search notes and list media links")
    search.add_argument("keyword")
    search.add_argument("-n", "--limit", type=int, default=40, help="max notes")
    search.add_argument("-s", "--sort", choices=SORTS, default="general")
    search.add_argument("-t", "--type", choices=TYPE_FILTERS, default="all")
    search.add_argument(
        "-c", "--concurrency", type=int, default=8, help="parallel requests"
    )
    search.add_argument(
        "--preview-images",
        action="store_true",
        help="keep CDN preview URLs instead of full-resolution originals",
    )
    search.add_argument(
        "--no-video-urls",
        action="store_true",
        help="skip the detail requests that resolve video stream URLs",
    )
    search.add_argument("--json", type=Path, help="write results to a JSON file")
    search.add_argument(
        "-d", "--download", type=Path, metavar="DIR", help="download media into DIR"
    )
    search.add_argument("--credentials", type=Path, default=DEFAULT_PATH)
    search.add_argument(
        "--auto-refresh",
        action="store_true",
        help="re-harvest credentials from the browser profile and retry once "
        "when the API rejects them",
    )

    download = subparsers.add_parser("download", help="download media from a JSON dump")
    download.add_argument("source", type=Path, help="JSON file produced by search")
    download.add_argument("-o", "--output", type=Path, default=Path("downloads"))
    download.add_argument("-c", "--concurrency", type=int, default=8)
    download.add_argument("--credentials", type=Path, default=DEFAULT_PATH)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "auth":
            return _auth(args)
        if args.command == "search":
            return asyncio.run(_search(args))
        return asyncio.run(_download(args))
    except RednoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _auth(args: argparse.Namespace) -> int:
    if args.auth_command == "import-har":
        credentials = from_har(args.har)
        path = credentials.save(args.output)
        print(f"credentials saved to {path}")
        print(f"signed endpoints: {', '.join(sorted(credentials.signatures))}")
        return 0

    if args.auth_command == "cookie":
        credentials = Credentials.load(DEFAULT_PATH).with_cookie(args.value)
        check_cookie(credentials.cookie)
        print(f"cookie updated in {credentials.save(DEFAULT_PATH)}")
        return 0

    if args.auth_command == "login":
        asyncio.run(browser.login())
        print(f"browser profile ready at {PROFILE_DIR}")
        return 0

    if args.auth_command == "refresh":
        credentials = asyncio.run(_refresh(DEFAULT_PATH, headless=not args.headful))
        print(f"credentials refreshed in {DEFAULT_PATH}")
        print(f"signed endpoints: {', '.join(sorted(credentials.signatures))}")
        return 0

    credentials = Credentials.load(DEFAULT_PATH)
    cookies = [pair.split("=", 1)[0] for pair in credentials.cookie.split("; ")]
    print(f"cookies: {', '.join(cookies)}")
    print(f"signed endpoints: {', '.join(sorted(credentials.signatures))}")
    return 0


async def _refresh(path: Path, *, headless: bool = True) -> Credentials:
    """Harvest fresh credentials, preserving signatures the browser did not see."""
    harvested = await browser.harvest(headless=headless)
    try:
        signatures = dict(Credentials.load(path).signatures)
    except RednoteError:
        signatures = {}
    signatures.update(harvested.signatures)
    harvested.signatures = signatures
    check_cookie(harvested.cookie)
    harvested.save(path)
    return harvested


async def _search(args: argparse.Namespace) -> int:
    credentials = Credentials.load(args.credentials)
    try:
        return await _run_search(credentials, args)
    except (SessionExpiredError, SignatureExpiredError) as exc:
        if not args.auto_refresh:
            raise
        print(f"{exc}\nrefreshing credentials from the browser...", file=sys.stderr)
        credentials = await _refresh(args.credentials)
        return await _run_search(credentials, args)


async def _run_search(credentials: Credentials, args: argparse.Namespace) -> int:
    async with RednoteClient(credentials, concurrency=args.concurrency) as client:
        notes = await client.search(
            args.keyword,
            limit=args.limit,
            sort=args.sort,
            originals=not args.preview_images,
        )
        wanted = TYPE_FILTERS[args.type]
        if wanted:
            notes = [note for note in notes if note.type == wanted]
        if not args.no_video_urls:
            await client.resolve_videos(notes)

        _report(notes)
        if args.json:
            _write_json(args.json, args.keyword, notes)
        if args.download:
            await _fetch_media(client, notes, args.download)
    return 0


async def _download(args: argparse.Namespace) -> int:
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    notes = [
        Note(**{k: v for k, v in item.items() if k in Note.__slots__})
        for item in payload["notes"]
    ]
    credentials = Credentials.load(args.credentials)
    async with RednoteClient(credentials, concurrency=args.concurrency) as client:
        await _fetch_media(client, notes, args.output)
    return 0


async def _fetch_media(
    client: RednoteClient, notes: list[Note], directory: Path
) -> None:
    tasks = [
        client.download(url, directory / name)
        for note in notes
        for url, name in media_targets(note)
    ]
    if not tasks:
        return
    results = await asyncio.gather(*tasks)
    failed = sum(1 for result in results if result is None)
    print(
        f"downloaded {len(results) - failed}/{len(results)} files into {directory}"
        + (f" ({failed} failed)" if failed else ""),
        file=sys.stderr,
    )


def _report(notes: list[Note]) -> None:
    for note in notes:
        marker = "video" if note.type == "video" else "photo"
        print(f"{note.url}\t[{marker} x{note.media_count}]\t{note.title}")
        for url in note.images:
            print(f"  img  {url}")
        if note.video:
            print(f"  vid  {note.video}")
        elif note.type == "video":
            print(f"  vid  <unresolved> cover: {note.cover}")

    images = sum(len(note.images) for note in notes)
    videos = sum(1 for note in notes if note.video)
    print(f"\n{len(notes)} notes, {images} images, {videos} videos", file=sys.stderr)


def _write_json(path: Path, keyword: str, notes: list[Note]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "keyword": keyword,
        "count": len(notes),
        "notes": [note.to_dict() for note in notes],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)
