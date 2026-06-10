"""`vr labels` — 1:1 with the Label API."""

from __future__ import annotations

import sys
from collections.abc import Iterable

import click

from ..api import labels as api
from ..api.clients import resolve_client_arg
from ..errors import UsageError
from ._common import AppCtx, dry_run_option, emit, pass_app


@click.group()
def labels() -> None:
    """Manage client labels."""


@labels.command(name="list")
@click.argument("client_id")
@pass_app
def list_(app: AppCtx, client_id: str) -> None:
    """List labels on a client (accepts a client ID or hostname)."""
    transport = app.transport()
    emit(app, api.get_labels(transport, resolve_client_arg(transport, client_id)))


@labels.command()
@click.argument("client_id")
@click.argument("label")
@dry_run_option
@pass_app
def add(app: AppCtx, client_id: str, label: str, dry_run: bool) -> None:
    """Add a label to a client."""
    transport = app.transport(dry_run=dry_run)
    resolved = resolve_client_arg(transport, client_id)
    result = api.add_label(transport, resolved, label)
    emit(
        app,
        result
        if result is not None
        else {"client_id": resolved, "label": label, "action": "added"},
    )


@labels.command()
@click.argument("client_id")
@click.argument("label")
@dry_run_option
@pass_app
def remove(app: AppCtx, client_id: str, label: str, dry_run: bool) -> None:
    """Remove a label from a client."""
    transport = app.transport(dry_run=dry_run)
    resolved = resolve_client_arg(transport, client_id)
    result = api.remove_label(transport, resolved, label)
    emit(
        app,
        result
        if result is not None
        else {"client_id": resolved, "label": label, "action": "removed"},
    )


def _parse_id_lines(lines: Iterable[str]) -> list[str]:
    """One ID/hostname per line; blank lines and `#` comments are ignored."""
    values: list[str] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        values.append(text)
    return values


@labels.command(name="bulk-add")
@click.argument("label")
@click.option(
    "--client-id",
    "client_ids",
    multiple=True,
    metavar="ID",
    help="Client ID or hostname to label; repeatable.",
)
@click.option(
    "--from-file",
    type=click.Path(exists=True, dir_okay=False),
    help="File with one client ID/hostname per line (blanks and # comments ignored).",
)
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="Read client IDs/hostnames from stdin, one per line.",
)
@dry_run_option
@pass_app
def bulk_add(
    app: AppCtx,
    label: str,
    client_ids: tuple[str, ...],
    from_file: str | None,
    read_stdin: bool,
    dry_run: bool,
) -> None:
    """Add one label to many clients (sources combine: options, file, stdin)."""
    raw: list[str] = list(client_ids)
    if from_file:
        with open(from_file, encoding="utf-8") as fh:
            raw.extend(_parse_id_lines(fh))
    if read_stdin:
        raw.extend(_parse_id_lines(sys.stdin))
    if not raw:
        raise UsageError("No client IDs given: pass --client-id, --from-file, and/or --stdin")
    transport = app.transport(dry_run=dry_run)
    resolved: list[str] = []
    seen: set[str] = set()
    for value in raw:
        client_id = resolve_client_arg(transport, value)
        if client_id not in seen:
            seen.add(client_id)
            resolved.append(client_id)
    emit(app, api.bulk_add_labels(transport, resolved, label))
