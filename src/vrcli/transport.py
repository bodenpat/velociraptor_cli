"""HTTP transport: auth, timeouts, retries, redaction, dry-run (PLAN.md §3, §5).

Design constraints, all load-bearing:

- TLS verification cannot be disabled. There is no flag, parameter, or env
  var in this codebase that turns it off. Corporate proxy CAs go through the
  standard SSL_CERT_FILE / REQUESTS_CA_BUNDLE mechanisms httpx honors.
- The API key only ever appears in the X-Api-Key request header. Every log
  line and every raised exception passes through redact() first.
- 429 and 5xx responses are retried with capped exponential backoff +
  full jitter, honoring Retry-After when present, so a runaway SOAR loop
  cannot hammer the tenant.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

import httpx

from . import USER_AGENT
from .config import REDACTED, Config
from .errors import NetworkError, error_for_status

logger = logging.getLogger("vrcli.transport")

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE = 0.5  # seconds
BACKOFF_CAP = 30.0  # seconds
RETRY_AFTER_CAP = 120.0  # never sleep longer than this on a Retry-After header
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


_ACTIVE_SECRETS: set[str] = set()


def active_secrets() -> frozenset[str]:
    """The set of registered secret values, for redaction outside the logging
    path (e.g. the audit JSONL writer). Source-independent."""
    return frozenset(_ACTIVE_SECRETS)


class SecretRedactingFilter(logging.Filter):
    """Scrubs every registered secret from any record that escapes."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        dirty = False
        for secret in _ACTIVE_SECRETS:
            if secret and secret in msg:
                msg = msg.replace(secret, REDACTED)
                dirty = True
        if dirty:
            record.msg = msg
            record.args = ()
        return True


_REDACTING_FILTER = SecretRedactingFilter()


def register_secret(value: str) -> None:
    """Make `value` unloggable.

    Logger filters do not apply to child-logger records (propagation only
    runs ancestor handlers), so the filter is installed on every existing
    vrcli.* logger. All vrcli modules create their loggers at import time,
    so by the time a transport exists every logger is covered.
    """
    _ACTIVE_SECRETS.add(value)
    names = ["vrcli"] + [
        name for name in logging.Logger.manager.loggerDict if name.startswith("vrcli.")
    ]
    for name in names:
        logger_ = logging.getLogger(name)
        if _REDACTING_FILTER not in logger_.filters:
            logger_.addFilter(_REDACTING_FILTER)


class VRTransport:
    """Thin, fully-redacted wrapper around httpx.Client.

    request() returns parsed JSON (None for 204/empty bodies, raw text for
    non-JSON responses) or raises a typed vrcli error. With dry_run=True,
    mutating requests are described instead of sent (see DryRun result).
    """

    def __init__(self, config: Config, *, dry_run: bool = False, transport: Any = None):
        self.config = config
        self.dry_run = dry_run
        self._key = config.api_key.reveal()
        # Belt-and-braces: scrub the key from anything logged under vrcli.*
        register_secret(self._key)
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "X-Api-Key": self._key,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=httpx.Timeout(config.timeout_read, connect=config.timeout_connect),
            transport=transport,
        )

    # -- redaction ---------------------------------------------------------

    def redact(self, text: str) -> str:
        return text.replace(self._key, REDACTED) if self._key else text

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VRTransport:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- core --------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        content: bytes | str | None = None,
        headers: dict | None = None,
    ) -> Any:
        method = method.upper()
        params = {k: v for k, v in (params or {}).items() if v is not None}

        if self.dry_run and method in MUTATING_METHODS:
            return self._describe_dry_run(method, path, params, json_body, content, headers)

        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params or None,
                    json=json_body,
                    content=content,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                # httpx exception text never contains headers, but redact anyway.
                message = self.redact(f"{type(exc).__name__}: {exc}")
                if attempt < self.config.max_retries and _is_retryable_exception(exc):
                    self._sleep_backoff(attempt, None, f"network error: {message}")
                    attempt += 1
                    continue
                raise NetworkError(f"Request failed: {message}") from None

            if response.status_code in RETRY_STATUSES and attempt < self.config.max_retries:
                self._sleep_backoff(
                    attempt,
                    response.headers.get("Retry-After"),
                    f"HTTP {response.status_code} from {method} {path}",
                )
                attempt += 1
                continue

            return self._handle_response(method, path, response)

    # -- helpers -----------------------------------------------------------

    def _describe_dry_run(self, method, path, params, json_body, content, headers) -> dict:
        url = str(self._client.base_url.join(path.lstrip("/")))
        described: dict = {"dry_run": True, "method": method, "url": url}
        if params:
            described["params"] = params
        if json_body is not None:
            described["body"] = json_body
        if content is not None:
            text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
            described["body_raw"] = self.redact(text)
        if headers:
            described["headers"] = {
                k: (REDACTED if k.lower() == "x-api-key" else v) for k, v in headers.items()
            }
        logger.info("dry-run: %s %s", method, path)
        return described

    def _handle_response(self, method: str, path: str, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            detail = _parse_body(response)
            message = self.redact(
                f"{method} {path} -> HTTP {response.status_code}"
                + (f": {_summarize(detail)}" if detail else "")
            )
            raise error_for_status(response.status_code, message, detail=_redact_any(detail, self))
        if response.status_code == 204 or not response.content:
            return None
        return _parse_body(response)

    def _sleep_backoff(self, attempt: int, retry_after: str | None, reason: str) -> None:
        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2**attempt)) * random.random()  # noqa: S311
        if retry_after:
            try:
                delay = min(float(retry_after), RETRY_AFTER_CAP)
            except ValueError:
                pass  # HTTP-date Retry-After: fall back to computed backoff
        logger.warning("retrying in %.1fs (attempt %d): %s", delay, attempt + 1, self.redact(reason))
        time.sleep(delay)


def _is_retryable_exception(exc: httpx.HTTPError) -> bool:
    """Connect/read/pool timeouts and dropped connections are retryable;
    TLS verification failures and protocol errors are not."""
    if isinstance(exc, httpx.ConnectError) and "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return False
    return isinstance(exc, httpx.TimeoutException | httpx.NetworkError)


def _parse_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text
    return response.text


def _summarize(detail: Any, limit: int = 200) -> str:
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    return text[:limit] + ("…" if len(text) > limit else "")


def _redact_any(value: Any, transport: VRTransport) -> Any:
    if isinstance(value, str):
        return transport.redact(value)
    if isinstance(value, dict):
        return {k: _redact_any(v, transport) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_any(v, transport) for v in value]
    return value
