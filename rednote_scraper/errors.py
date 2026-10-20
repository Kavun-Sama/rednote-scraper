# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exception hierarchy for the scraper."""


class RednoteError(Exception):
    """Base class for all scraper errors."""


class CredentialsError(RednoteError):
    """Credentials are missing, malformed or incomplete."""


class SignatureExpiredError(RednoteError):
    """The stored request signature was rejected (API code -1)."""


class SessionExpiredError(RednoteError):
    """The session cookie is missing or no longer valid (API code -100/-101)."""


class BrowserUnavailableError(RednoteError):
    """Playwright is not installed or no logged-in browser profile exists."""


class ApiError(RednoteError):
    """The API replied with a non-zero status code."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"API error {code}: {msg}")
        self.code = code
        self.msg = msg


class LimitError(RednoteError):
    """A request exceeds a configured limit."""
