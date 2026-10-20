# SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay the stored search signature with a changed body, URI and time.

Reproduces Findings 1 in the README: the ``x-s``/``x-t`` signature covers the
request URI only, so one signature serves any keyword, page and sort order.

Every case reuses the signature stored in the credentials file. Two negative
controls (a corrupted and a missing signature) show whether the endpoint checks
signatures at all; if it accepts either, acceptance in the other cases proves
nothing. The ``x-t`` cases are recorded for information only.

Sends about a dozen requests to the live service with your session.
Use ``--dry-run`` to list them first.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from _common import (
    LiveSession,
    Run,
    base_parser,
    baseline_failure,
    execute,
    format_duration,
    iso,
    md_table,
    now_utc,
    search_payload,
    signature_signed_at,
)

from rednote_scraper.client import SEARCH_URL, new_search_id
from rednote_scraper.credentials import Credentials

SCRIPT = "measure_signature_scope.py"
SIGNATURE_HEADERS = ("x-s", "x-t", "x-s-common")
HOUR_MS = 3_600_000

Headers = dict[str, str]


class SkipCase(Exception):
    """The case cannot be built from the stored credentials."""


def keep(headers: Headers) -> Headers:
    return dict(headers)


def corrupt_signature(headers: Headers) -> Headers:
    out = dict(headers)
    value = out.get("x-s", "")
    if not value:
        raise SkipCase("no x-s header stored")
    out["x-s"] = value[:-1] + ("B" if value[-1] == "A" else "A")
    return out


def drop_signature(headers: Headers) -> Headers:
    return {k: v for k, v in headers.items() if k not in SIGNATURE_HEADERS}


def shift_x_t(delta_ms: int) -> Callable[[Headers], Headers]:
    def transform(headers: Headers) -> Headers:
        out = dict(headers)
        try:
            out["x-t"] = str(int(out["x-t"]) + delta_ms)
        except (KeyError, ValueError) as exc:
            raise SkipCase("x-t is not an integer") from exc
        return out

    return transform


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    description: str
    expect: str  # "accepted", "rejected" or "informational"
    payload: dict[str, Any]
    transform: Callable[[Headers], Headers]
    url: str = SEARCH_URL
    delay_before: float = 0.0


def build_cases(keyword: str, alt_keyword: str, wait: float) -> list[Case]:
    search_id = new_search_id()
    base = search_payload(keyword, 1, "general", search_id)
    cases = [
        Case("baseline", "stored signature, original body", "accepted", base, keep),
        Case(
            "body_keyword",
            f"keyword changed to {alt_keyword!r}",
            "accepted",
            search_payload(alt_keyword, 1, "general", search_id),
            keep,
        ),
        Case(
            "body_page",
            "page changed to 2",
            "accepted",
            search_payload(keyword, 2, "general", search_id),
            keep,
        ),
        Case(
            "body_sort",
            "sort changed to popularity_descending",
            "accepted",
            search_payload(keyword, 1, "popularity_descending", search_id),
            keep,
        ),
        Case(
            "body_all",
            "keyword, page, sort and search_id all changed",
            "accepted",
            search_payload(alt_keyword, 3, "time_descending", new_search_id()),
            keep,
        ),
        Case(
            "uri_with_query",
            "query string appended to the signed URI",
            "rejected",
            base,
            keep,
            url=f"{SEARCH_URL}?probe=1",
        ),
        Case(
            "control_corrupted_signature",
            "last character of x-s changed",
            "rejected",
            base,
            corrupt_signature,
        ),
        Case(
            "control_no_signature",
            "x-s, x-t and x-s-common removed",
            "rejected",
            base,
            drop_signature,
        ),
        Case(
            "x_t_plus_1h",
            "x-t moved one hour ahead, x-s unchanged",
            "informational",
            base,
            shift_x_t(HOUR_MS),
        ),
        Case(
            "x_t_minus_1h",
            "x-t moved one hour back, x-s unchanged",
            "informational",
            base,
            shift_x_t(-HOUR_MS),
        ),
    ]
    if wait > 0:
        cases.append(
            Case(
                "delayed_replay",
                f"original request replayed after waiting {wait:g} s",
                "accepted",
                base,
                keep,
                delay_before=wait,
            )
        )
    return cases


def verdict(expect: str, outcome: str) -> str:
    if outcome in ("skipped", "error"):
        return "inconclusive"
    if expect == "informational":
        return "informational"
    if (outcome == "accepted") == (expect == "accepted"):
        return "as expected"
    return "unexpected"


def collect(
    session: LiveSession, run: Run, checkpoint: Callable[[], None], *, cases: list[Case]
) -> None:
    base_headers = session.base_headers()
    signed_at = signature_signed_at(base_headers)
    run.context["signature_signed_at_from_x_t"] = iso(signed_at)
    run.context["signature_age_seconds_at_start"] = (
        int((now_utc() - signed_at).total_seconds()) if signed_at else None
    )
    for case in cases:
        row: dict[str, Any] = {
            "name": case.name,
            "description": case.description,
            "expect": case.expect,
        }
        if case.delay_before > 0:
            print(
                f"waiting {case.delay_before:g} s before {case.name}", file=sys.stderr
            )
            time.sleep(case.delay_before)
        try:
            headers = case.transform(base_headers)
        except SkipCase as exc:
            row.update(outcome="skipped", note=str(exc))
        else:
            row.update(
                session.post(case.payload, headers=headers, url=case.url).fields()
            )
        row["verdict"] = verdict(case.expect, row["outcome"])
        run.results.append(row)
        print(f"{case.name}: {row['outcome']}", file=sys.stderr)
        checkpoint()
        if case.name == "baseline" and row["outcome"] != "accepted":
            raise baseline_failure(row["outcome"])


def conclusions(run: Run) -> list[str]:
    by_name = {row["name"]: row for row in run.results}
    out: list[str] = []
    controls = [
        by_name.get("control_corrupted_signature"),
        by_name.get("control_no_signature"),
    ]
    if any(c and c["outcome"] == "accepted" for c in controls):
        out.append(
            "A negative control was accepted: the endpoint did not reject a corrupted "
            "or missing signature, so acceptance in the other cases says nothing "
            "about what the signature covers."
        )
        return out
    if not all(controls):
        out.append(
            "Negative controls did not complete; read the cases below with care."
        )
    body = [
        by_name[n]
        for n in ("body_keyword", "body_page", "body_sort", "body_all")
        if n in by_name
    ]
    if body:
        if all(r["outcome"] == "accepted" for r in body):
            out.append(
                "Changing keyword, page, sort and search_id with the same "
                "signature was accepted."
            )
        else:
            failed = ", ".join(r["name"] for r in body if r["outcome"] != "accepted")
            out.append(f"Not accepted with a changed body: {failed}.")
    delayed = by_name.get("delayed_replay")
    if delayed:
        out.append(f"Replay after the wait: {delayed['outcome']}.")
    uri = by_name.get("uri_with_query")
    if uri:
        if uri["outcome"] == "accepted":
            out.append(
                "A query string appended to the signed path was accepted: binding of "
                "the signature to the query string is not demonstrated by this run."
            )
        else:
            out.append(
                f"A query string appended to the signed path was not accepted "
                f"({uri['outcome']}), consistent with the signature covering the URI."
            )
    return out


def summarise(run: Run, status: str) -> tuple[dict[str, Any], str]:
    notes = conclusions(run)
    age = run.context.get("signature_age_seconds_at_start")
    lines = [
        "# Signature scope",
        "",
        f"Status: {status}",
        "",
        "Signature age at start (x-t read as a millisecond timestamp): "
        + (format_duration(age) if age is not None else "unknown"),
        "",
        md_table(
            ["case", "expected", "outcome", "HTTP", "API code", "items", "verdict"],
            [
                [
                    r["name"],
                    r["expect"],
                    r["outcome"],
                    r.get("http_status", ""),
                    r.get("api_code", ""),
                    r.get("items", ""),
                    r["verdict"],
                ]
                for r in run.results
            ],
        ),
        "",
        "## Conclusions",
        "",
        *[f"- {line}" for line in notes],
        "",
        "Cases: "
        + "; ".join(f"`{r['name']}` - {r['description']}" for r in run.results),
    ]
    payload = {"context": run.context, "results": run.results, "conclusions": notes}
    return payload, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = base_parser(__doc__.split("\n\n")[0])
    parser.add_argument(
        "--keyword", default="logo", help="keyword of the original body"
    )
    parser.add_argument(
        "--alt-keyword", default="coffee", help="keyword of the changed bodies"
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="wait before the delayed replay; 0 skips it (default 120)",
    )
    args = parser.parse_args(argv)
    cases = build_cases(args.keyword, args.alt_keyword, args.wait)

    def plan(_credentials: Credentials | None) -> list[str]:
        return [f"{c.name}: {c.description} (expect {c.expect})" for c in cases]

    return execute(
        script=SCRIPT,
        name="signature-scope",
        args=args,
        plan=plan,
        collect=partial(collect, cases=cases),
        summarise=summarise,
    )


if __name__ == "__main__":
    raise SystemExit(main())
