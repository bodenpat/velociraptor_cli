"""`vr ops triage` — KapeFiles triage collection with evidence + audit (PLAN.md §4.2)."""

from __future__ import annotations

import click

from ..errors import UsageError
from ..ops import triage as ops
from ._common import AppCtx, dry_run_option, emit, pass_app, wait_options


@click.command()
@click.argument("host", required=False)
@click.option(
    "--artifact",
    default=ops.DEFAULT_ARTIFACT,
    show_default=True,
    metavar="NAME",
    help="Collector artifact to run.",
)
@click.option(
    "--targets",
    default=ops.DEFAULT_TARGETS,
    show_default=True,
    metavar="GROUP",
    help="KapeFiles target group to enable (spec env parameter set to 'Y').",
)
@click.option(
    "--out",
    type=click.Path(file_okay=False),
    default=None,
    metavar="DIR",
    help="Evidence directory (default: ./evidence/triage-<client_id>-<flow_id>; "
    "evidence/ is gitignored). Must not already contain files.",
)
@click.option(
    "--max-upload-bytes",
    type=click.IntRange(min=1),
    default=None,
    help="Upload byte cap for the collection.",
)
@click.option("--urgent", is_flag=True, help="Mark the collection urgent.")
@click.option(
    "--start",
    is_flag=True,
    help="Start the flow and return a resume token instead of blocking "
    "(default when neither --wait nor --check is given).",
)
@click.option(
    "--check",
    "check_token",
    default=None,
    metavar="TOKEN",
    help="Resume token from --start: poll once; when the flow is FINISHED, "
    "write the evidence directory exactly as --wait would.",
)
@wait_options
@dry_run_option
@pass_app
def triage(
    app: AppCtx,
    host,
    artifact,
    targets,
    out,
    max_upload_bytes,
    urgent,
    start,
    check_token,
    wait,
    wait_timeout,
    poll_interval,
    dry_run,
) -> None:
    """Collect a KapeFiles triage from HOST (client ID or hostname) with
    a SHA-256 evidence manifest and audit record."""
    if sum([wait, start, check_token is not None]) > 1:
        raise UsageError("--wait, --start, and --check are mutually exclusive")

    if check_token is not None:
        if host:
            raise UsageError("--check takes only the resume token; do not pass HOST")
        if dry_run:
            raise UsageError("--dry-run does not apply to --check (no flow is created)")
        emit(app, ops.check_triage(app.transport(), check_token))
        return

    if not host:
        raise UsageError("HOST (client ID or hostname) is required unless --check is given")

    transport = app.transport(dry_run=dry_run)
    if wait:
        result = ops.run_triage(
            transport,
            host,
            artifact=artifact,
            targets=targets,
            out=out,
            max_upload_bytes=max_upload_bytes,
            urgent=urgent,
            timeout=wait_timeout,
            poll_interval=poll_interval,
        )
    else:  # --start, or the default fire-and-resume mode
        result = ops.start_triage(
            transport,
            host,
            artifact=artifact,
            targets=targets,
            out=out,
            max_upload_bytes=max_upload_bytes,
            urgent=urgent,
        )
    emit(app, result)


COMMANDS = [triage]
