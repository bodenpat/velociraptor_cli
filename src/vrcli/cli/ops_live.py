"""`vr ops live` — targeted live response with curated artifact presets."""

from __future__ import annotations

import click

from ..ops.live_response import PRESETS, live_response
from ._common import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_WAIT_TIMEOUT,
    AppCtx,
    dry_run_option,
    emit,
    pass_app,
)


@click.command()
@click.argument("host")
@click.option(
    "--preset",
    "presets",
    multiple=True,
    required=True,
    type=click.Choice(list(PRESETS), case_sensitive=False),
    help="Curated artifact preset (repeatable; artifact lists are unioned into one flow).",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False),
    default=None,
    metavar="DIR",
    help=(
        "Evidence directory (default: ./evidence/live-<client_id>-<flow_id>; "
        "evidence/ is gitignored)."
    ),
)
@click.option(
    "--timeout",
    "wait_timeout",
    type=click.IntRange(min=1),
    default=DEFAULT_WAIT_TIMEOUT,
    show_default=True,
    help="Give up waiting after this many seconds (exit code 5).",
)
@click.option(
    "--poll-interval",
    type=click.IntRange(min=1),
    default=DEFAULT_POLL_INTERVAL,
    show_default=True,
    help="Seconds between polls while waiting.",
)
@click.option("--urgent", is_flag=True, help="Mark the collection urgent.")
@dry_run_option
@pass_app
def live(
    app: AppCtx,
    host: str,
    presets: tuple[str, ...],
    out_dir: str | None,
    wait_timeout: int,
    poll_interval: int,
    urgent: bool,
    dry_run: bool,
) -> None:
    """Targeted live response: collect curated presets from one host.

    HOST accepts a client ID or hostname. Always waits for the collection to
    finish, then writes result rows + logs + SHA-256 manifest + audit record
    to the evidence directory.
    """
    transport = app.transport(dry_run=dry_run)
    emit(
        app,
        live_response(
            transport,
            host,
            presets,
            out_dir=out_dir,
            timeout=wait_timeout,
            poll_interval=poll_interval,
            urgent=urgent,
        ),
    )


COMMANDS = [live]
