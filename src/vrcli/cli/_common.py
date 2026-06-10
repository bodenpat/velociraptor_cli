"""Shared CLI plumbing: app context, output contract, option decorators.

Output contract (PLAN.md §3):
- stdout: exactly one JSON document per invocation (or a table with
  `--output table`, opt-in for humans).
- stderr: logs/progress only, never secrets.
Every command funnels its result through emit() exactly once.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

import click

from ..config import Config, load_config
from ..errors import UsageError
from ..transport import VRTransport

DEFAULT_WAIT_TIMEOUT = 600
DEFAULT_POLL_INTERVAL = 10


class AppCtx:
    """Per-invocation state shared down the click tree."""

    def __init__(self) -> None:
        self.output: str = "json"
        self.verbose: bool = False
        self._config: Config | None = None
        self._transport: VRTransport | None = None

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config()
        return self._config

    def transport(self, *, dry_run: bool = False) -> VRTransport:
        """Build the transport lazily so `vr --help` needs no env vars."""
        if self._transport is None:
            self._transport = VRTransport(self.config, dry_run=dry_run)
        else:
            self._transport.dry_run = dry_run
        return self._transport

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()


pass_app = click.make_pass_decorator(AppCtx)


# -- output --------------------------------------------------------------


def emit(app: AppCtx, data: Any) -> None:
    """Write the command's single output document to stdout."""
    if app.output == "table":
        click.echo(_render_table(data))
    else:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def emit_error(error_dict: dict) -> None:
    """Errors are still one JSON document on stdout (SOAR parses stdout);
    the human-readable line goes to stderr in main()."""
    click.echo(json.dumps({"error": error_dict}, indent=2, ensure_ascii=False, default=str))


def dump_jsonl(path: str, rows) -> None:
    """Write rows as JSONL to a local file; a filesystem failure is a usage
    error (exit 2), not an internal one."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        raise UsageError(f"Cannot write {path}: {exc.strerror}") from exc


def _render_table(data: Any) -> str:
    if isinstance(data, dict):
        rows = [(str(k), _cell(v)) for k, v in data.items()]
        return _format_columns(["FIELD", "VALUE"], rows)
    if isinstance(data, list):
        if not data:
            return "(no results)"
        if all(isinstance(item, dict) for item in data):
            headers: list[str] = []
            for item in data:
                for key in item:
                    if key not in headers:
                        headers.append(key)
            rows = [tuple(_cell(item.get(h)) for h in headers) for item in data]
            return _format_columns(headers, rows)
        return "\n".join(_cell(item) for item in data)
    return _cell(data)


def _cell(value: Any, limit: int = 80) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", " ")
    return text[: limit - 1] + "…" if len(text) > limit else text


def _format_columns(headers: list[str], rows: list[tuple]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*row) for row in rows]
    return "\n".join(lines)


# -- option decorators -----------------------------------------------------

dry_run_option = click.option(
    "--dry-run",
    is_flag=True,
    help="Print the exact API request without sending it.",
)

yes_option = click.option(
    "--yes",
    is_flag=True,
    help="Confirm this destructive action (required; there is no interactive prompt).",
)


def require_yes(yes: bool, action: str) -> None:
    if not yes:
        raise UsageError(f"Refusing to {action} without --yes")


def wait_options(fn):
    fn = click.option(
        "--poll-interval",
        type=click.IntRange(min=1),
        default=DEFAULT_POLL_INTERVAL,
        show_default=True,
        help="Seconds between polls while waiting.",
    )(fn)
    fn = click.option(
        "--timeout",
        "wait_timeout",
        type=click.IntRange(min=1),
        default=DEFAULT_WAIT_TIMEOUT,
        show_default=True,
        help="Give up waiting after this many seconds (exit code 5).",
    )(fn)
    fn = click.option("--wait", is_flag=True, help="Block until the operation completes.")(fn)
    return fn


def paging_options(fn):
    fn = click.option(
        "--page-budget",
        type=click.IntRange(min=1),
        default=100,
        show_default=True,
        help="Maximum pages to fetch with --all.",
    )(fn)
    fn = click.option("--all", "fetch_all", is_flag=True, help="Follow cursors and fetch every page.")(fn)
    fn = click.option("--limit", type=click.IntRange(min=1), default=None, help="Maximum results to return.")(fn)
    return fn


# -- argument parsing ------------------------------------------------------

_PARAM_RE = re.compile(r"^(?:(?P<artifact>[A-Za-z0-9._]+):)?(?P<key>[^=]+)=(?P<value>.*)$", re.S)


def parse_artifact_params(params: tuple[str, ...], artifacts: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """Parse --param values of the form `Artifact.Name:key=value`.

    The bare `key=value` shorthand is allowed only when exactly one
    --artifact was given (PLAN.md §4.1 conventions: the prefixed form keeps
    multi-artifact collections unambiguous).
    """
    parsed: dict[str, dict[str, str]] = {}
    for raw in params:
        match = _PARAM_RE.match(raw)
        if not match:
            raise UsageError(f"--param {raw!r} is not of the form artifact:key=value")
        artifact = match.group("artifact")
        if artifact is None:
            if len(artifacts) != 1:
                raise UsageError(
                    f"--param {raw!r} must be prefixed `artifact:key=value` when "
                    f"{'multiple artifacts are' if artifacts else 'no artifact is'} given"
                )
            artifact = artifacts[0]
        elif artifact not in artifacts:
            raise UsageError(f"--param {raw!r} references {artifact!r}, which is not in --artifact")
        parsed.setdefault(artifact, {})[match.group("key")] = match.group("value")
    return parsed


def build_collector_args(
    artifacts: tuple[str, ...],
    params: tuple[str, ...] = (),
    *,
    timeout: int | None = None,
    cpu_limit: float | None = None,
    max_upload_bytes: int | None = None,
    urgent: bool = False,
) -> dict:
    """Build an ArtifactCollectorArgs body (spec: artifacts + specs required)."""
    by_artifact = parse_artifact_params(params, artifacts)
    specs = [
        {
            "artifact": artifact,
            "parameters": {"env": [{"key": k, "value": v} for k, v in kv.items()]},
        }
        for artifact, kv in by_artifact.items()
    ]
    body: dict = {"artifacts": list(artifacts), "specs": specs}
    if timeout is not None:
        body["timeout"] = timeout
    if cpu_limit is not None:
        body["cpu_limit"] = cpu_limit
    if max_upload_bytes is not None:
        body["max_upload_bytes"] = max_upload_bytes
    if urgent:
        body["urgent"] = True
    return body


_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw]?)$", re.I)
_DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str) -> int:
    """`7d` / `24h` / `30m` / `3600` -> seconds."""
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise UsageError(f"{value!r} is not a duration (expected e.g. 3600, 30m, 24h, 7d)")
    return int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
