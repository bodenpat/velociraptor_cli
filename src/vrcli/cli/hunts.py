"""`vr hunts` — 1:1 with the Hunt API."""

from __future__ import annotations

import json

import click

from ..api import hunts as api
from ..ops._wait import wait_for_hunt
from ._common import (
    AppCtx,
    build_collector_args,
    dry_run_option,
    emit,
    paging_options,
    parse_duration,
    pass_app,
    require_yes,
    wait_options,
    yes_option,
)

HUNT_STATES = ["unset", "paused", "running", "stopped", "archived", "deleted"]


@click.group()
def hunts() -> None:
    """Create and manage fleet-wide hunts."""


@hunts.command(name="list")
@click.option(
    "--state",
    type=click.Choice(HUNT_STATES, case_sensitive=False),
    default=None,
    help="Filter by hunt state.",
)
@click.option(
    "--sort",
    type=click.Choice(["newest", "oldest"], case_sensitive=False),
    default=None,
    help="Sort order.",
)
@paging_options
@pass_app
def list_(app: AppCtx, state, sort, limit, fetch_all, page_budget) -> None:
    """List hunts."""
    transport = app.transport()
    if fetch_all:
        emit(
            app,
            list(
                api.iter_hunts(
                    transport, state=state, sort=sort, max_items=limit, page_budget=page_budget
                )
            ),
        )
    else:
        emit(app, api.list_hunts(transport, state=state, sort=sort, limit=limit))


@hunts.command()
@click.option(
    "--artifact",
    "artifacts",
    multiple=True,
    required=True,
    help="Artifact to collect (repeatable).",
)
@click.option(
    "--param",
    "params",
    multiple=True,
    metavar="ARTIFACT:KEY=VALUE",
    help="Artifact parameter (repeatable; bare KEY=VALUE allowed with a single --artifact).",
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
@click.option("--urgent", is_flag=True, help="Skip client-side queueing for this collection.")
@wait_options
@dry_run_option
@pass_app
def create(
    app: AppCtx,
    artifacts,
    params,
    labels,
    exclude_labels,
    os_,
    client_limit,
    expires,
    description,
    tags,
    urgent,
    wait,
    wait_timeout,
    poll_interval,
    dry_run,
) -> None:
    """Create a hunt (fleet-wide collection)."""
    body = api.build_hunt_body(
        build_collector_args(artifacts, params, urgent=urgent),
        labels=labels,
        excluded_labels=exclude_labels,
        os=os_,
        client_limit=client_limit,
        expires_in=parse_duration(expires),
        description=description,
        tags=tags,
    )
    transport = app.transport(dry_run=dry_run)
    result = api.create_hunt(transport, body)
    if isinstance(result, dict):  # dry-run: emit the described request, never wait
        emit(app, result)
        return
    if wait:
        hunt = wait_for_hunt(transport, result, timeout=wait_timeout, poll_interval=poll_interval)
        emit(app, {**hunt, "hunt_id": result})
    else:
        emit(app, {"hunt_id": result})


@hunts.command()
@click.argument("hunt_id")
@pass_app
def get(app: AppCtx, hunt_id: str) -> None:
    """Get hunt details and stats."""
    emit(app, api.get_hunt(app.transport(), hunt_id))


def _set_state(app: AppCtx, hunt_id: str, desired_state: str, dry_run: bool) -> None:
    """PATCH the hunt state and emit one result document (204 -> synthesized doc)."""
    transport = app.transport(dry_run=dry_run)
    result = api.set_hunt_state(transport, hunt_id, desired_state)
    emit(app, result if result is not None else {"hunt_id": hunt_id, "state": desired_state})


@hunts.command()
@click.argument("hunt_id")
@dry_run_option
@pass_app
def pause(app: AppCtx, hunt_id: str, dry_run: bool) -> None:
    """Pause a hunt (desiredState=PAUSED)."""
    _set_state(app, hunt_id, "PAUSED", dry_run)


@hunts.command()
@click.argument("hunt_id")
@dry_run_option
@pass_app
def resume(app: AppCtx, hunt_id: str, dry_run: bool) -> None:
    """Resume a paused hunt (desiredState=RUNNING)."""
    _set_state(app, hunt_id, "RUNNING", dry_run)


@hunts.command()
@click.argument("hunt_id")
@yes_option
@dry_run_option
@pass_app
def stop(app: AppCtx, hunt_id: str, yes: bool, dry_run: bool) -> None:
    """Stop a hunt (desiredState=STOPPED)."""
    require_yes(yes, f"stop hunt {hunt_id}")
    _set_state(app, hunt_id, "STOPPED", dry_run)


@hunts.command()
@click.argument("hunt_id")
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write result rows as JSONL to FILE; stdout then carries a summary document.",
)
@paging_options
@pass_app
def results(app: AppCtx, hunt_id, out_file, limit, fetch_all, page_budget) -> None:
    """Get hunt results (one page, or every page with --all)."""
    transport = app.transport()
    if fetch_all:
        document = list(
            api.iter_hunt_results(transport, hunt_id, max_items=limit, page_budget=page_budget)
        )
        rows = document
    else:
        document = api.get_hunt_results(transport, hunt_id, limit=limit)
        if isinstance(document, dict):
            rows = document.get("data") or []
        else:
            rows = document or []
    if out_file:
        with open(out_file, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        emit(app, {"hunt_id": hunt_id, "rows": len(rows), "out": out_file})
    else:
        emit(app, document)


@hunts.command()
@click.argument("hunt_id")
@pass_app
def errors(app: AppCtx, hunt_id: str) -> None:
    """Get per-client hunt errors."""
    emit(app, api.get_hunt_errors(app.transport(), hunt_id))
