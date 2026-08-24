"""Credential storage and extraction from browser HAR captures.

The rednote web API signs every request with an ``x-s``/``x-t`` header pair.
The signature covers the request URI only -- neither the request body nor the
wall clock is validated -- so a single signature captured for a POST endpoint
stays valid for arbitrary payloads until the session cookie expires.  That is
what makes a browserless scraper possible.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import CredentialsError

DEFAULT_PATH = Path(
    os.environ.get("REDNOTE_CREDENTIALS")
    or Path.home() / ".config" / "rednote-scraper" / "credentials.json"
)

API_HOST = "webapi.rednote.com"
SEARCH_PATH = "/api/sns/web/v1/search/notes"

_SIGNATURE_HEADERS = ("x-s", "x-t", "x-s-common")
_REQUIRED_COOKIES = ("web_session", "a1")


@dataclass(slots=True)
class Credentials:
    """Session cookies plus per-endpoint request signatures."""

    cookie: str
    user_agent: str
    signatures: dict[str, dict[str, str]] = field(default_factory=dict)

    def headers_for(self, path: str) -> dict[str, str]:
        """Return the full header set for a signed API request to ``path``."""
        signature = self.signatures.get(path)
        if signature is None:
            raise CredentialsError(
                f"no captured signature for {path}; re-run "
                f"'rednote auth import-har' with a capture that contains it"
            )
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.rednote.com",
            "referer": "https://www.rednote.com/",
            "user-agent": self.user_agent,
            "cookie": self.cookie,
        }
        headers.update(signature)
        return headers

    def web_headers(self) -> dict[str, str]:
        """Return headers for unsigned requests to the public web front end."""
        return {
            "accept": "text/html,application/xhtml+xml",
            "user-agent": self.user_agent,
            "cookie": self.cookie,
        }

    def save(self, path: Path = DEFAULT_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cookie": self.cookie,
            "user_agent": self.user_agent,
            "signatures": self.signatures,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        path.chmod(0o600)
        return path

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Credentials":
        if not path.exists():
            raise CredentialsError(
                f"credentials not found at {path}; run "
                f"'rednote auth import-har <capture.har>' first"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                cookie=data["cookie"],
                user_agent=data["user_agent"],
                signatures=data["signatures"],
            )
        except (json.JSONDecodeError, KeyError) as exc:
            raise CredentialsError(f"malformed credentials file {path}: {exc}") from exc


def from_har(paths: list[Path]) -> Credentials:
    """Extract cookies and endpoint signatures from one or more HAR captures.

    Only signatures for POST endpoints without a query string are retained:
    GET signatures cover the query string too and are therefore worthless for
    replay with different parameters.
    """
    cookie = ""
    user_agent = ""
    signatures: dict[str, dict[str, str]] = {}

    for path in paths:
        try:
            har = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialsError(f"cannot read HAR {path}: {exc}") from exc

        for entry in har.get("log", {}).get("entries", []):
            request = entry.get("request", {})
            url = urlsplit(request.get("url", ""))
            if url.hostname != API_HOST:
                continue

            headers = _collect_headers(request.get("headers", []))
            if "cookie" in headers:
                cookie = headers["cookie"]
            if "user-agent" in headers:
                user_agent = headers["user-agent"]

            if request.get("method") != "POST" or url.query:
                continue
            signature = {k: headers[k] for k in _SIGNATURE_HEADERS if k in headers}
            if "x-s" in signature and "x-t" in signature:
                signatures[url.path] = signature

    if not cookie:
        raise CredentialsError("no cookies found in the supplied HAR files")
    missing = [name for name in _REQUIRED_COOKIES if f"{name}=" not in cookie]
    if missing:
        raise CredentialsError(
            f"cookie is missing {', '.join(missing)} -- capture the HAR while "
            f"logged in to rednote.com"
        )
    if SEARCH_PATH not in signatures:
        raise CredentialsError(
            f"no signature for {SEARCH_PATH} in the supplied HAR files -- "
            f"capture a search request from the browser DevTools network tab"
        )

    return Credentials(cookie=cookie, user_agent=user_agent, signatures=signatures)


def _collect_headers(raw: list[dict[str, Any]]) -> dict[str, str]:
    """Fold HAR header entries into a dict, joining repeated cookie headers."""
    headers: dict[str, str] = {}
    cookies: list[str] = []
    for header in raw:
        name = header.get("name", "").lower()
        value = header.get("value", "")
        if name.startswith(":"):
            continue
        if name == "cookie":
            cookies.append(value)
        else:
            headers[name] = value
    if cookies:
        headers["cookie"] = "; ".join(cookies)
    return headers
