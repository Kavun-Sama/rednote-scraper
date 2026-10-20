# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure how long the stored search signature keeps being accepted.

Reproduces Findings 1 (signature lifetime) in the README. The same search
request is replayed with the stored signature at the offsets of a schedule,
and the signature's age is recorded at each probe. The run stops at the first
probe whose API code is -1 (signature rejected) or whose code shows a dead
session, which bounds the lifetime from both sides. The report is rewritten
after every probe, so an interrupted run keeps what it measured.

For a meaningful result run ``rednote auth refresh`` immediately before, keep
the computer awake for the whole schedule and do not refresh meanwhile. The
age is counted from the signature's ``x-t`` header read as a millisecond
timestamp (an assumption), or from ``--signed-at``, or from the modification
time of the credentials file, in that order.

Sends one request per schedule entry to the live service with your session.
Use ``--dry-run`` to list them first.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from _common import (
    LiveSession,
    Run,
    base_parser,
    execute,
    format_duration,
    iso,
    md_table,
    search_payload,
    signature_signed_at,
)

from rednote_scraper.credentials import Credentials

SCRIPT = "measure_signature_lifetime.py"
DEFAULT_SCHEDULE = "0,300,900,1800,3600,7200,14400,28800,43200,86400"


def parse_schedule(text: str) -> list[float]:
    try:
        offsets = sorted({float(part) for part in text.split(",") if part.strip()})
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a list of seconds: {text!r}") from exc
    if not offsets or offsets[0] < 0:
        raise argparse.ArgumentTypeError("expected non-negative offsets in seconds")
    return offsets


def parse_moment(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"not an ISO 8601 timestamp: {text!r}"
        ) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def find_anchor(session: LiveSession, args: argparse.Namespace) -> tuple[float, str]:
    """Return the epoch second the signature's age is counted from, and its source."""
    if args.signed_at:
        return args.signed_at.timestamp(), "--signed-at"
    signed_at = signature_signed_at(session.base_headers())
    if signed_at:
        return signed_at.timestamp(), "x-t header (assumed millisecond timestamp)"
    return args.credentials.stat().st_mtime, "credentials file modification time"


def collect(
    session: LiveSession,
    run: Run,
    checkpoint: Callable[[], None],
    *,
    args: argparse.Namespace,
) -> None:
    anchor, source = find_anchor(session, args)
    run.context["age_anchor"] = iso(datetime.fromtimestamp(anchor, UTC))
    run.context["age_anchor_source"] = source
    payload = search_payload(args.keyword)
    started = time.time()
    for offset in args.schedule:
        remaining = started + offset - time.time()
        if remaining > 0:
            print(f"next probe in {format_duration(remaining)}", file=sys.stderr)
            time.sleep(remaining)
        probe = session.post(payload)
        now = time.time()
        run.results.append(
            {
                "offset_seconds": offset,
                "probed_at": iso(datetime.fromtimestamp(now, UTC)),
                "signature_age_seconds": int(now - anchor),
                **probe.fields(),
            }
        )
        print(
            f"offset {format_duration(offset)}, age "
            f"{format_duration(now - anchor)}: {probe.outcome}",
            file=sys.stderr,
        )
        checkpoint()
        if probe.outcome in ("signature_rejected", "session_rejected"):
            return


def conclusions(run: Run) -> list[str]:
    rows = run.results
    accepted = [r for r in rows if r["outcome"] == "accepted"]
    last_ok = max((r["signature_age_seconds"] for r in accepted), default=None)
    out: list[str] = []
    rejected = next((r for r in rows if r["outcome"] == "signature_rejected"), None)
    dead = next((r for r in rows if r["outcome"] == "session_rejected"), None)
    if rejected:
        age = rejected["signature_age_seconds"]
        if last_ok is None:
            out.append(
                "Signature rejected at the first probe, at age "
                f"{format_duration(age)}: lifetime is at most {format_duration(age)}."
            )
        else:
            out.append(
                f"Signature accepted up to age {format_duration(last_ok)} and "
                f"rejected at age {format_duration(age)}: lifetime lies in between."
            )
    elif dead:
        out.append(
            f"The session was rejected at age "
            f"{format_duration(dead['signature_age_seconds'])} (API code "
            f"{dead['api_code']}) before any signature rejection: the signature "
            f"lifetime is not determined"
            + (f"; it is at least {format_duration(last_ok)}." if last_ok else ".")
        )
    elif last_ok is not None:
        out.append(
            f"Signature still accepted at age {format_duration(last_ok)} "
            f"(last probe): lifetime is at least that long."
        )
    else:
        out.append("No probe completed.")
    errors = sum(1 for r in rows if r["outcome"] == "error")
    if errors:
        out.append(f"{errors} probe(s) failed with a transport error.")
    return out


def summarise(run: Run, status: str) -> tuple[dict[str, Any], str]:
    notes = conclusions(run)
    lines = [
        "# Signature lifetime",
        "",
        f"Status: {status}",
        "",
        f"Age counted from {run.context.get('age_anchor')} "
        f"({run.context.get('age_anchor_source')}).",
        "",
        md_table(
            ["offset", "signature age", "outcome", "HTTP", "API code"],
            [
                [
                    format_duration(r["offset_seconds"]),
                    format_duration(r["signature_age_seconds"]),
                    r["outcome"],
                    r["http_status"],
                    r["api_code"],
                ]
                for r in run.results
            ],
        ),
        "",
        "## Conclusions",
        "",
        *[f"- {line}" for line in notes],
    ]
    payload = {"context": run.context, "results": run.results, "conclusions": notes}
    return payload, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = base_parser(__doc__.split("\n\n")[0])
    parser.add_argument(
        "--schedule",
        type=parse_schedule,
        default=parse_schedule(DEFAULT_SCHEDULE),
        metavar="SECONDS,...",
        help=f"probe offsets from the start of the run (default {DEFAULT_SCHEDULE})",
    )
    parser.add_argument("--keyword", default="logo", help="search keyword")
    parser.add_argument(
        "--signed-at",
        type=parse_moment,
        metavar="ISO8601",
        help="when the signature was captured; naive times are read as UTC",
    )
    args = parser.parse_args(argv)

    def plan(_credentials: Credentials | None) -> list[str]:
        return [
            f"t+{format_duration(offset)}: replay the stored signature on a search"
            for offset in args.schedule
        ] + ["stop at the first signature or session rejection"]

    return execute(
        script=SCRIPT,
        name="signature-lifetime",
        args=args,
        plan=plan,
        collect=partial(collect, args=args),
        summarise=summarise,
    )


if __name__ == "__main__":
    raise SystemExit(main())
