"""`vr ops enrich` — one-shot enrichment blob for InsightIDR (PLAN.md §4.2)."""

from __future__ import annotations

import click

from ..ops import enrich as ops
from ._common import AppCtx, emit, pass_app


@click.command()
@click.argument("host")
@click.option(
    "--flows",
    type=click.IntRange(min=1),
    default=ops.DEFAULT_RECENT_FLOWS,
    show_default=True,
    help="Maximum recent flows to include.",
)
@pass_app
def enrich(app: AppCtx, host: str, flows: int) -> None:
    """Enrich HOST (client ID or hostname) for an investigation.

    One JSON document: full client record, labels, last-seen, and the most
    recent flows. Read-only; writes an audit record to $R7_VR_AUDIT_LOG
    when set.
    """
    emit(app, ops.enrich_host(app.transport(), host, flows=flows))


COMMANDS = [enrich]
