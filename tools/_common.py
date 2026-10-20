# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helpers for the measurement scripts in this directory.

The scripts send requests to the live service with the stored credentials of the
person running them. Reports record HTTP statuses, API codes, sizes, timings and
cookie names; cookie values and signatures are never written to a report.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from rednote_scraper.client import (
    PAGE_SIZE,
    SEARCH_URL,
    SESSION_ERROR_CODES,
    new_search_id,
)
from rednote_scraper.credentials import DEFAULT_PATH, SEARCH_PATH, Credentials
from rednote_scraper.errors import CredentialsError

MEASUREMENTS_DIR = Path(__file__).resolve().parents[1] / "docs" / "measurements"

#: HTTP statuses that stop a run instead of being recorded and retried.
HALT_STATUSES = frozenset({429})
MAX_CONSECUTIVE_ERRORS = 3

#: Arguments that are not copied into a report.
_PRIVATE_ARGUMENTS = frozenset({"credentials", "out_dir", "dry_run"})


class MeasurementAborted(RuntimeError):
    """The run cannot continue; the message says why."""


def baseline_failure(outcome: str) -> MeasurementAborted:
    """Abort reason for a baseline request that was not accepted."""
    hint = (
        "the request did not complete; check the connection"
        if outcome == "error"
        else "run 'rednote auth refresh' and try again"
    )
    return MeasurementAborted(
        f"baseline request was not accepted (outcome {outcome}); {hint}"
    )


@dataclass(slots=True)
class Probe:
    """Outcome of one request to the search endpoint."""

    http_status: int | None
    api_code: int | None
    success: bool | None
    message: str
    items: int | None
    elapsed_ms: int
    error: str = ""

    @property
    def outcome(self) -> str:
        """One of accepted, signature_rejected, session_rejected, rejected, error."""
        if self.error:
            return "error"
        if self.http_status == 200 and self.success is True:
            return "accepted"
        if self.api_code == -1:
            return "signature_rejected"
        if self.api_code in SESSION_ERROR_CODES:
            return "session_rejected"
        return "rejected"

    def fields(self) -> dict[str, Any]:
        return {**asdict(self), "outcome": self.outcome}


@dataclass(slots=True)
class Run:
    """Mutable record of one run, shared by a script's collect and summarise steps."""

    results: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


def search_payload(
    keyword: str,
    page: int = 1,
    sort: str = "general",
    search_id: str | None = None,
) -> dict[str, Any]:
    """Request body built by ``RednoteClient._search_page``, reproduced for replay."""
    return {
        "keyword": keyword,
        "page": page,
        "page_size": PAGE_SIZE,
        "search_id": search_id or new_search_id(),
        "sort": sort,
        "note_type": 0,
        "ext_flags": [],
        "geo": "",
        "image_formats": ["jpg", "webp", "avif"],
    }


class LiveSession:
    """Sequential, paced requests with the stored credentials."""

    def __init__(
        self, credentials: Credentials, client: httpx.Client, *, pause: float
    ) -> None:
        self.credentials = credentials
        self.client = client
        self._pause = pause
        self._sent = 0
        self._consecutive_errors = 0

    def base_headers(self) -> dict[str, str]:
        """Full header set of a signed search request, as the scraper sends it."""
        return self.credentials.headers_for(SEARCH_PATH)

    def pace(self) -> None:
        """Wait out the pause between two requests; call before every request."""
        if self._sent and self._pause > 0:
            time.sleep(self._pause)
        self._sent += 1

    def post(
        self,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        url: str = SEARCH_URL,
    ) -> Probe:
        """POST to the search endpoint and record the outcome without raising."""
        self.pace()
        probe = self._send(
            payload, self.base_headers() if headers is None else headers, url
        )
        if probe.http_status in HALT_STATUSES:
            raise MeasurementAborted(
                f"HTTP {probe.http_status}: the service is throttling requests; "
                f"wait before running again"
            )
        self._consecutive_errors = (
            self._consecutive_errors + 1 if probe.outcome == "error" else 0
        )
        if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            raise MeasurementAborted(
                f"{MAX_CONSECUTIVE_ERRORS} consecutive transport errors: {probe.error}"
            )
        return probe

    def _send(
        self, payload: dict[str, Any], headers: Mapping[str, str], url: str
    ) -> Probe:
        started = time.monotonic()
        try:
            response = self.client.post(url, json=payload, headers=dict(headers))
        except httpx.HTTPError as exc:
            return Probe(
                None,
                None,
                None,
                "",
                None,
                _elapsed_ms(started),
                error=f"{type(exc).__name__}: {exc}",
            )
        elapsed = _elapsed_ms(started)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = {}
        data = body.get("data")
        items = len(data.get("items") or []) if isinstance(data, dict) else None
        return Probe(
            response.status_code,
            body.get("code"),
            body.get("success"),
            str(body.get("msg", "")),
            items,
            elapsed,
        )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def signature_signed_at(headers: Mapping[str, str]) -> datetime | None:
    """Read ``x-t`` as a millisecond epoch timestamp.

    This is an assumption about the header, not a documented fact; ``None`` is
    returned when the value does not parse or falls outside a plausible range.
    """
    try:
        moment = datetime.fromtimestamp(int(headers["x-t"]) / 1000, UTC)
    except (KeyError, ValueError, OverflowError, OSError):
        return None
    if datetime(2020, 1, 1, tzinfo=UTC) <= moment <= now_utc() + timedelta(days=1):
        return moment
    return None


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


def format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def md_table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(cell(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def base_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_PATH,
        help="credentials file written by 'rednote auth' (default: the standard path)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=MEASUREMENTS_DIR,
        help="directory for the JSON and Markdown reports (default: docs/measurements)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="pause between requests (default 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned requests and exit without sending anything",
    )
    return parser


class Reporter:
    """Writes the JSON and Markdown report of one run, overwriting on each write."""

    def __init__(self, script: str, name: str, args: argparse.Namespace) -> None:
        self._script = script
        self._name = name
        self._out_dir: Path = args.out_dir
        self._parameters = {
            key: value
            for key, value in vars(args).items()
            if key not in _PRIVATE_ARGUMENTS
        }
        self._started = now_utc()
        self._stamp = self._started.strftime("%Y%m%dT%H%M%SZ")

    def write(self, payload: dict[str, Any], markdown: str) -> tuple[Path, Path]:
        self._out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "script": f"tools/{self._script}",
            "started_at": iso(self._started),
            "written_at": iso(now_utc()),
            "environment": {
                "python": platform.python_version(),
                "httpx": httpx.__version__,
                "system": platform.system(),
            },
            "parameters": self._parameters,
            **payload,
        }
        stem = self._out_dir / f"{self._name}-{self._stamp}"
        json_path = stem.with_suffix(".json")
        markdown_path = stem.with_suffix(".md")
        json_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        markdown_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        return json_path, markdown_path


def execute(
    *,
    script: str,
    name: str,
    args: argparse.Namespace,
    plan: Callable[[Credentials | None], list[str]],
    collect: Callable[[LiveSession, Run, Callable[[], None]], None],
    summarise: Callable[[Run, str], tuple[dict[str, Any], str]],
) -> int:
    """Run one measurement: dry run, live collection, report.

    ``collect`` appends to the run and may call the checkpoint callback to save a
    partial report; ``summarise`` turns the run and a status string into the JSON
    payload and the Markdown text.
    """
    if args.dry_run:
        try:
            credentials: Credentials | None = Credentials.load(args.credentials)
        except CredentialsError:
            credentials = None
        print(f"{name}: planned requests (nothing is sent)")
        for line in plan(credentials):
            print(f"  {line}")
        return 0

    try:
        credentials = Credentials.load(args.credentials)
        credentials.headers_for(SEARCH_PATH)
    except CredentialsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    reporter = Reporter(script, name, args)
    run = Run()

    def checkpoint() -> None:
        payload, markdown = summarise(run, "in progress")
        reporter.write({"status": "in progress", **payload}, markdown)

    status, exit_code = "complete", 0
    with httpx.Client(http2=True, timeout=20.0) as client:
        session = LiveSession(credentials, client, pause=args.pause)
        try:
            collect(session, run, checkpoint)
        except MeasurementAborted as exc:
            status, exit_code = f"aborted: {exc}", 1
        except KeyboardInterrupt:
            status, exit_code = "interrupted by user", 130

    payload, markdown = summarise(run, status)
    json_path, markdown_path = reporter.write({"status": status, **payload}, markdown)
    print(f"status: {status}", file=sys.stderr)
    print(f"wrote {json_path}\nwrote {markdown_path}", file=sys.stderr)
    return exit_code
