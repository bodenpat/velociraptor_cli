"""Typed exceptions mapped to CLI exit codes.

Exit-code contract (PLAN.md §3) — SOAR workflows branch on these:

    0  success
    1  unexpected internal error (a bug; stdout still carries a JSON error doc)
    2  usage / configuration error
    3  auth or permission denied (401/403)
    4  not found (404)
    5  wait-timeout (--wait exceeded --timeout)
    6  API error (other 4xx/5xx)
    7  network / TLS failure

Code 1 is the catch-all: any exception that is not a VRError still yields a
single JSON document on stdout (never a bare traceback), so a SOAR step can
always parse the result.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_WAIT_TIMEOUT = 5
EXIT_API = 6
EXIT_NETWORK = 7


class VRError(Exception):
    """Base for all vrcli errors. Carries the CLI exit code."""

    exit_code = EXIT_API

    def __init__(self, message: str, *, detail: object = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        body: dict = {"type": type(self).__name__, "message": self.message,
                      "exit_code": self.exit_code}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class UsageError(VRError):
    """Bad arguments or invalid local input."""

    exit_code = EXIT_USAGE


class ConfigError(UsageError):
    """Missing or invalid environment configuration."""


class AuthError(VRError):
    """401/403 from the API — bad key or insufficient permissions."""

    exit_code = EXIT_AUTH


class NotFoundError(VRError):
    """404 from the API, or a hostname that resolved to zero clients."""

    exit_code = EXIT_NOT_FOUND


class WaitTimeout(VRError):
    """A --wait poll loop exceeded its --timeout."""

    exit_code = EXIT_WAIT_TIMEOUT


class APIError(VRError):
    """Any other 4xx/5xx API response."""

    exit_code = EXIT_API

    def __init__(self, message: str, *, status: int | None = None, detail: object = None):
        super().__init__(message, detail=detail)
        self.status = status

    def to_dict(self) -> dict:
        body = super().to_dict()
        if self.status is not None:
            body["status"] = self.status
        return body


class NetworkError(VRError):
    """Connection, DNS, TLS, or timeout failure before an HTTP response."""

    exit_code = EXIT_NETWORK


def error_for_status(status: int, message: str, *, detail: object = None) -> VRError:
    """Map an HTTP status code to the typed exception for it."""
    if status in (401, 403):
        return AuthError(message, detail=detail)
    if status == 404:
        return NotFoundError(message, detail=detail)
    return APIError(message, status=status, detail=detail)
