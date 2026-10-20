# SPDX-License-Identifier: AGPL-3.0-or-later
"""Leave-one-out probing of the stored cookie.

Reproduces Findings 2 in the README: only ``web_session`` and
``x-rednote-datactry`` matter; the other cookies the browser sends do not.

The stored cookie is sent to ``POST /api/sns/web/v1/search/notes`` once in full,
once per cookie with that cookie removed, once with only the claimed required
cookies (a joint test, which leave-one-out alone cannot give), once with no
cookie at all (negative control), and once more in full at the end to show the
session stayed valid during the run. Reports contain cookie names only.

Sends one request per stored cookie plus four to the live service with your
session. Use ``--dry-run`` to list them first.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from typing import Any

from _common import (
    LiveSession,
    Run,
    base_parser,
    baseline_failure,
    execute,
    md_table,
    search_payload,
)

from rednote_scraper.credentials import (
    REQUIRED_COOKIES,
    Credentials,
    format_cookie,
    parse_cookie,
)

SCRIPT = "measure_cookie_minimality.py"

Step = tuple[str, str, str, dict[str, str]]  # kind, cookie, description, cookies sent


def build_steps(pairs: dict[str, str]) -> list[Step]:
    steps: list[Step] = [("baseline", "", "full stored cookie", pairs)]
    steps.extend(
        (
            "leave_one_out",
            name,
            f"without {name}",
            {k: v for k, v in pairs.items() if k != name},
        )
        for name in pairs
    )
    steps.append(
        (
            "only_required",
            "",
            "only " + ", ".join(REQUIRED_COOKIES),
            {k: v for k, v in pairs.items() if k in REQUIRED_COOKIES},
        )
    )
    steps.append(("no_cookie", "", "no cookie header", {}))
    steps.append(("baseline_end", "", "full stored cookie again", pairs))
    return steps


def collect(
    session: LiveSession,
    run: Run,
    checkpoint: Callable[[], None],
    *,
    keyword: str,
) -> None:
    base_headers = session.base_headers()
    pairs = parse_cookie(base_headers["cookie"])
    run.context["cookie_names"] = list(pairs)
    run.context["endpoint"] = "POST /api/sns/web/v1/search/notes"
    payload = search_payload(keyword)
    for kind, cookie, description, sent in build_steps(pairs):
        headers = dict(base_headers)
        if sent:
            headers["cookie"] = format_cookie(sent)
        else:
            headers.pop("cookie", None)
        probe = session.post(payload, headers=headers)
        run.results.append(
            {
                "kind": kind,
                "cookie": cookie,
                "description": description,
                "cookies_sent": list(sent),
                **probe.fields(),
            }
        )
        print(f"{description}: {probe.outcome}", file=sys.stderr)
        checkpoint()
        if kind == "baseline" and probe.outcome != "accepted":
            raise baseline_failure(probe.outcome)


def conclusions(run: Run) -> list[str]:
    rows = run.results
    out: list[str] = []
    leave = [r for r in rows if r["kind"] == "leave_one_out"]
    significant = [
        r["cookie"] for r in leave if r["outcome"] not in ("accepted", "error")
    ]
    inconclusive = [r["cookie"] for r in leave if r["outcome"] == "error"]
    claimed = set(REQUIRED_COOKIES)
    if leave:
        out.append(
            "Cookies whose removal changed the outcome: "
            + (", ".join(significant) or "none")
            + "."
        )
        if set(significant) == claimed:
            out.append("This equals the claimed set (REQUIRED_COOKIES).")
        else:
            missing = ", ".join(sorted(claimed - set(significant))) or "-"
            extra = ", ".join(sorted(set(significant) - claimed)) or "-"
            out.append(
                "This differs from the claimed set "
                f"({', '.join(sorted(claimed))}): only in the claim: {missing}; "
                f"only measured: {extra}."
            )
    if inconclusive:
        out.append("Inconclusive (transport error): " + ", ".join(inconclusive) + ".")
    for kind, label in (
        ("only_required", "Only the claimed required cookies"),
        ("no_cookie", "No cookie header (negative control)"),
        ("baseline_end", "Full cookie again at the end (session still valid)"),
    ):
        row = next((r for r in rows if r["kind"] == kind), None)
        if row:
            out.append(f"{label}: {row['outcome']}.")
    out.append(
        "Scope: one request per step to POST /api/sns/web/v1/search/notes; the "
        "note-page request is not probed."
    )
    return out


def summarise(run: Run, status: str) -> tuple[dict[str, Any], str]:
    notes = conclusions(run)
    lines = [
        "# Cookie minimality",
        "",
        f"Status: {status}",
        "",
        md_table(
            ["step", "outcome", "HTTP", "API code", "items"],
            [
                [
                    r["description"],
                    r["outcome"],
                    r.get("http_status", ""),
                    r.get("api_code", ""),
                    r.get("items", ""),
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
        "--keyword", default="logo", help="search keyword (default logo)"
    )
    args = parser.parse_args(argv)

    def plan(credentials: Credentials | None) -> list[str]:
        if credentials is None:
            return [
                "baseline, one step per stored cookie, only_required, "
                "no_cookie, baseline_end"
            ]
        pairs = parse_cookie(credentials.cookie)
        return [
            f"{kind}: {description}" for kind, _, description, _ in build_steps(pairs)
        ]

    return execute(
        script=SCRIPT,
        name="cookie-minimality",
        args=args,
        plan=plan,
        collect=partial(collect, keyword=args.keyword),
        summarise=summarise,
    )


if __name__ == "__main__":
    raise SystemExit(main())
