"""`vr ops contain` / `vr ops release` — containment with quarantine guardrails (PLAN.md §4.2, §9.7)."""

from __future__ import annotations

import click

from ..ops import containment
from ._common import AppCtx, dry_run_option, emit, pass_app


@click.command()
@click.argument("host")
@click.option(
    "--label",
    default=containment.DEFAULT_CONTAIN_LABEL,
    show_default=True,
    help="Containment label to add.",
)
@click.option(
    "--quarantine",
    is_flag=True,
    help="Additionally launch Windows.Remediation.Quarantine and verify it scheduled.",
)
@click.option(
    "--deny-label",
    "deny_labels",
    multiple=True,
    metavar="LABEL",
    help="Extra label that blocks --quarantine (added to the built-in deny set "
    "and $R7_VR_QUARANTINE_DENY_LABELS).",
)
@dry_run_option
@pass_app
def contain(app: AppCtx, host, label, quarantine, deny_labels, dry_run) -> None:
    """Tag HOST (client ID or hostname) with an investigation label; optionally quarantine.

    With --quarantine the client's current labels are checked CLIENT-SIDE
    against the quarantine deny set before any request mutates anything
    (PLAN.md §9.7); a match refuses the whole operation with exit code 2.
    """
    transport = app.transport(dry_run=dry_run)
    emit(
        app,
        containment.contain(
            transport,
            host,
            label=label,
            quarantine=quarantine,
            extra_deny_labels=deny_labels,
        ),
    )


@click.command()
@click.argument("host")
@click.option(
    "--label",
    default=containment.DEFAULT_CONTAIN_LABEL,
    show_default=True,
    help="Containment label to remove.",
)
@dry_run_option
@pass_app
def release(app: AppCtx, host, label, dry_run) -> None:
    """Remove HOST's containment label and, if it was quarantined, unquarantine it."""
    transport = app.transport(dry_run=dry_run)
    emit(app, containment.release(transport, host, label=label))


COMMANDS = [contain, release]
