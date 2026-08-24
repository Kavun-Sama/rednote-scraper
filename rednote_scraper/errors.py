"""Exception hierarchy for the scraper."""


class RednoteError(Exception):
    """Base class for all scraper errors."""


class CredentialsError(RednoteError):
    """Credentials are missing, malformed or incomplete."""


class SignatureExpiredError(RednoteError):
    """The stored request signature or session cookie is no longer accepted."""


class ApiError(RednoteError):
    """The API replied with a non-zero status code."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"API error {code}: {msg}")
        self.code = code
        self.msg = msg
