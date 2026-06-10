"""`vr ops ioc-hunt` — fleet-wide IOC sweep (PLAN.md §4.2)."""

from __future__ import annotations

from pathlib import Path

import click

from ..ops import ioc_hunt as ops
from ..ops._audit import write_audit
from ._common import AppCtx, dry_run_option, emit, parse_duration, pass_app, wait_options


@click.command(name="ioc-hunt")
@click.option(
    "--hash",
    "hashes",
    multiple=True,
    metavar="SHA256",
    help="SHA-256 to hunt for (repeatable; matched client-side against result rows).",
)
@click.option(
    "--filename",
    "filenames",
    multiple=True,
    metavar="GLOB",
    help="Filename glob to hunt for (repeatable).",
)
@click.option(
    "--yara",
    "yara_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="YARA rule file; its contents become the hunt's YaraRule parameter.",
)
@click.option(
    "--glob",
    "scope_glob",
    default=ops.DEFAULT_SCOPE_GLOB,
    show_default=True,
    help="Filesystem scope searched for --hash/--yara IOCs (ignored when --filename sets it).",
)
@click.option(
    "--artifact",
    default=ops.DEFAULT_ARTIFACT,
    show_default=True,
    help="Artifact the hunt collects.",
)
@click.option(
    "--label", "labels", multiple=True, help="Only target clients with this label (repeatable)."
)
@click.option(
    "--exclude-label",
    "exclude_labels",
    multiple=True,
    help="Skip clients with this label (repeatable).",
)
@click.option(
    "--os",
    "os_",
    type=click.Choice(["windows", "linux", "darwin"], case_sensitive=False),
    default=None,
    help="Only target this operating system.",
)
@click.option(
    "--client-limit",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum number of clients to schedule.",
)
@click.option(
    "--expires",
    default="7d",
    show_default=True,
    help="Hunt expiry as a duration from now (e.g. 3600, 30m, 24h, 7d).",
)
@click.option("--description", default=None, help="Hunt description.")
@click.option("--tag", "tags", multiple=True, help="Hunt tag (repeatable).")
@wait_options
@dry_run_option
@pass_app
def ioc_hunt(
    app: AppCtx,
    hashes,
    filenames,
    yara_file,
    scope_glob,
    artifact,
    labels,
    exclude_labels,
    os_,
    client_limit,
    expires,
    description,
    tags,
    wait,
    wait_timeout,
    poll_interval,
    dry_run,
) -> None:
    """Launch a fleet-wide IOC hunt; with --wait, emit a per-client hit summary.

    At least one of --hash/--filename/--yara is required; they may be
    combined. No evidence directory is produced — the audit record goes to
    $R7_VR_AUDIT_LOG when set.
    """
    yara_rule = Path(yara_file).read_text(encoding="utf-8") if yara_file else None
    transport = app.transport(dry_run=dry_run)
    result = ops.launch_ioc_hunt(
        transport,
        hashes=hashes,
        filenames=filenames,
        yara_rule=yara_rule,
        scope_glob=scope_glob,
        artifact=artifact,
        labels=labels,
        excluded_labels=exclude_labels,
        os=os_,
        client_limit=client_limit,
        expires_in=parse_duration(expires),
        description=description,
        tags=tags,
    )
    if isinstance(result, dict):  # dry-run: emit the described request, never audit/wait
        emit(app, result)
        return
    write_audit("ops ioc-hunt", out_dir=None, created={"hunt_id": result})
    if wait:
        emit(
            app,
            ops.wait_and_summarize(
                transport,
                result,
                hashes=hashes,
                timeout=wait_timeout,
                poll_interval=poll_interval,
            ),
        )
    else:
        emit(app, {"hunt_id": result})


COMMANDS = [ioc_hunt]
