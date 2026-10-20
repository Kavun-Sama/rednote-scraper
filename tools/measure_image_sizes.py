# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure preview and original image sizes for the same images.

Reproduces Findings 4 in the README: rewriting a CDN preview URL into its
original makes the file much larger (README: about 30x; the docstring of
``original_image_url``: an order of magnitude). A search supplies the preview
URLs (the variant the scraper picks), ``original_image_url`` derives the
original for each, and both are downloaded and counted without being saved.

Downloads send only a User-Agent header, as ``RednoteClient.download`` does.
Sends one search plus two image requests per image to the live service and CDN
with your session. Use ``--dry-run`` to list them first.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from collections import Counter
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
from _common import (
    LiveSession,
    MeasurementAborted,
    Run,
    base_parser,
    execute,
    md_table,
)

from rednote_scraper.client import RednoteClient
from rednote_scraper.credentials import Credentials
from rednote_scraper.errors import RednoteError
from rednote_scraper.parsing import original_image_url

SCRIPT = "measure_image_sizes.py"
README_CLAIM = 30
DOCSTRING_CLAIM = 10


async def fetch_preview_urls(
    credentials: Credentials, keyword: str, notes: int, sort: str
) -> list[str]:
    async with RednoteClient(credentials) as client:
        found = await client.search(keyword, limit=notes, sort=sort, originals=False)
    return [url for note in found for url in note.images]


def measure_bytes(
    client: httpx.Client, url: str, headers: dict[str, str], method: str
) -> dict[str, Any]:
    """Size of ``url`` in bytes: from HEAD when asked and available, else counted."""
    result: dict[str, Any] = {
        "method": method,
        "http_status": None,
        "bytes": None,
        "content_type": "",
        "error": "",
    }
    try:
        if method == "head":
            response = client.head(url, headers=headers, follow_redirects=True)
            length = response.headers.get("content-length", "")
            if response.status_code == 200 and length.isdigit():
                result.update(
                    http_status=200,
                    bytes=int(length),
                    content_type=response.headers.get("content-type", ""),
                )
                return result
            result["method"] = "get"
        with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
            result["http_status"] = r.status_code
            result["content_type"] = r.headers.get("content-type", "")
            total = sum(len(chunk) for chunk in r.iter_bytes(64 * 1024))
            if r.status_code == 200:
                result["bytes"] = total
    except httpx.HTTPError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def collect(
    session: LiveSession,
    run: Run,
    checkpoint: Callable[[], None],
    *,
    keyword: str,
    sort: str,
    notes: int,
    count: int,
    method: str,
) -> None:
    try:
        previews = asyncio.run(
            fetch_preview_urls(session.credentials, keyword, notes, sort)
        )
    except (RednoteError, httpx.HTTPError) as exc:
        raise MeasurementAborted(f"search failed: {exc}") from exc
    if not previews:
        raise MeasurementAborted("the search returned no images")
    headers = {"user-agent": session.credentials.user_agent}
    for index, preview in enumerate(previews[:count], start=1):
        original = original_image_url(preview)
        row: dict[str, Any] = {
            "index": index,
            "variant": preview.rsplit("!", 1)[1] if "!" in preview else "",
            "preview_url": preview,
            "original_url": original,
        }
        for label, url in (("preview", preview), ("original", original)):
            session.pace()
            row[label] = measure_bytes(session.client, url, headers, method)
        small, large = row["preview"]["bytes"], row["original"]["bytes"]
        row["ratio"] = round(large / small, 2) if small and large else None
        run.results.append(row)
        print(f"image {index}: ratio {row['ratio']}", file=sys.stderr)
        checkpoint()


def statistics_of(run: Run) -> dict[str, Any]:
    measured = [r for r in run.results if r["ratio"]]
    ratios = [r["ratio"] for r in measured]
    stats: dict[str, Any] = {
        "images_total": len(run.results),
        "images_measured": len(measured),
        "images_failed": len(run.results) - len(measured),
        "variants": dict(Counter(r["variant"] for r in run.results)),
    }
    if ratios:
        stats.update(
            median_ratio=round(statistics.median(ratios), 2),
            mean_ratio=round(statistics.fmean(ratios), 2),
            geometric_mean_ratio=round(statistics.geometric_mean(ratios), 2),
            min_ratio=min(ratios),
            max_ratio=max(ratios),
            total_bytes_ratio=round(
                sum(r["original"]["bytes"] for r in measured)
                / sum(r["preview"]["bytes"] for r in measured),
                2,
            ),
        )
    return stats


def summarise(run: Run, status: str) -> tuple[dict[str, Any], str]:
    stats = statistics_of(run)
    claims = {"readme": README_CLAIM, "original_image_url_docstring": DOCSTRING_CLAIM}
    lines = [
        "# Image sizes: preview vs original",
        "",
        f"Status: {status}",
        "",
        f"Images measured: {stats['images_measured']} of {stats['images_total']}",
        "",
    ]
    if "median_ratio" in stats:
        lines += [
            md_table(
                ["statistic", "original / preview"],
                [
                    ["median", stats["median_ratio"]],
                    ["mean", stats["mean_ratio"]],
                    ["geometric mean", stats["geometric_mean_ratio"]],
                    ["min", stats["min_ratio"]],
                    ["max", stats["max_ratio"]],
                    ["total bytes", stats["total_bytes_ratio"]],
                ],
            ),
            "",
            f"Claims for comparison: README about {README_CLAIM}x; "
            f"`original_image_url` docstring: an order of magnitude "
            f"(about {DOCSTRING_CLAIM}x).",
            "",
        ]
    lines += [
        md_table(
            ["#", "variant", "preview bytes", "original bytes", "ratio"],
            [
                [
                    r["index"],
                    r["variant"],
                    r["preview"]["bytes"],
                    r["original"]["bytes"],
                    r["ratio"],
                ]
                for r in run.results
            ],
        ),
    ]
    payload = {
        "context": run.context,
        "claims": claims,
        "statistics": stats,
        "results": run.results,
    }
    return payload, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = base_parser(__doc__.split("\n\n")[0])
    parser.add_argument("--keyword", default="logo", help="search keyword")
    parser.add_argument("--sort", default="general", help="search sort order")
    parser.add_argument(
        "--notes", type=int, default=20, help="notes to request (default 20)"
    )
    parser.add_argument(
        "--count", type=int, default=20, help="images to measure (default 20)"
    )
    parser.add_argument(
        "--method",
        choices=("get", "head"),
        default="get",
        help="count bytes from a full GET (default) or use HEAD Content-Length "
        "with a GET fallback",
    )
    args = parser.parse_args(argv)

    def plan(_credentials: Credentials | None) -> list[str]:
        return [
            f"search {args.keyword!r}: {args.notes} notes, sort {args.sort}",
            f"for up to {args.count} images: {args.method.upper()} the preview URL "
            "and the original URL derived by original_image_url",
        ]

    return execute(
        script=SCRIPT,
        name="image-sizes",
        args=args,
        plan=plan,
        collect=partial(
            collect,
            keyword=args.keyword,
            sort=args.sort,
            notes=args.notes,
            count=args.count,
            method=args.method,
        ),
        summarise=summarise,
    )


if __name__ == "__main__":
    raise SystemExit(main())
