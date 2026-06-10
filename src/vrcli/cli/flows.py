"""`vr flows` — 1:1 with the Flow API (PLAN.md §4.1)."""

from __future__ import annotations

import click

from ..api import flows as api
from ..api.clients import resolve_client_arg
from ..errors import UsageError
from ..ops._wait import wait_for_flow
from ._common import (
    AppCtx,
    build_collector_args,
    dry_run_option,
    dump_jsonl,
    emit,
    paging_options,
    pass_app,
    wait_options,
)

_page_budget_option = click.option(
    "--page-budget",
    type=click.IntRange(min=1),
    default=100,
    show_default=True,
    help="Maximum pages to fetch with --all.",
)


@click.group()
def flows() -> None:
    """Create and manage per-client collections."""


@flows.command(name="list")
@click.argument("client_id")
@click.option("--creator", help="Filter by creator username.")
@click.option(
    "--sort",
    type=click.Choice(["newest", "oldest"], case_sensitive=False),
    default=None,
    help="Sort order for results.",
)
@paging_options
@pass_app
def list_(app: AppCtx, client_id, creator, sort, limit, fetch_all, page_budget) -> None:
    """List flows for a client (accepts a client ID or hostname)."""
    transport = app.transport()
    resolved = resolve_client_arg(transport, client_id)
    if fetch_all:
        data = list(
            api.iter_flows(
                transport,
                resolved,
                creator=creator,
                sort=sort,
                max_items=limit,
                page_budget=page_budget,
            )
        )
        emit(app, data)
    else:
        emit(app, api.list_flows(transport, resolved, creator=creator, sort=sort, limit=limit))


@flows.command()
@click.argument("client_id")
@click.option(
    "--artifact",
    "artifacts",
    multiple=True,
    required=True,
    metavar="NAME",
    help="Artifact to collect (repeatable).",
)
@click.option(
    "--param",
    "params",
    multiple=True,
    metavar="ARTIFACT:KEY=VALUE",
    help="Artifact parameter; bare KEY=VALUE is allowed with a single --artifact.",
)
@click.option(
    "--collection-timeout",
    type=click.IntRange(min=1),
    default=None,
    metavar="SEC",
    help="Collection execution timeout on the endpoint (ArtifactCollectorArgs.timeout).",
)
@click.option(
    "--cpu-limit",
    type=click.FloatRange(min=0),
    default=None,
    metavar="PCT",
    help="CPU usage limit for the collection.",
)
@click.option(
    "--max-upload-bytes",
    type=click.IntRange(min=1),
    default=None,
    help="Upload byte cap for the collection.",
)
@click.option("--urgent", is_flag=True, help="Mark the collection urgent.")
@wait_options
@dry_run_option
@pass_app
def create(
    app: AppCtx,
    client_id,
    artifacts,
    params,
    collection_timeout,
    cpu_limit,
    max_upload_bytes,
    urgent,
    wait,
    wait_timeout,
    poll_interval,
    dry_run,
) -> None:
    """Start a collection on a client (accepts a client ID or hostname)."""
    body = build_collector_args(
        artifacts,
        params,
        timeout=collection_timeout,
        cpu_limit=cpu_limit,
        max_upload_bytes=max_upload_bytes,
        urgent=urgent,
    )
    transport = app.transport(dry_run=dry_run)
    resolved = resolve_client_arg(transport, client_id)
    result = api.create_flow(transport, resolved, body)
    if dry_run:
        emit(app, result)
        return
    flow_id = result
    if wait:
        flow = wait_for_flow(
            transport, resolved, flow_id, timeout=wait_timeout, poll_interval=poll_interval
        )
        emit(app, {**flow, "flow_id": flow_id})
    else:
        emit(app, {"flow_id": flow_id, "client_id": resolved})


@flows.command()
@click.argument("client_id")
@click.argument("flow_id")
@pass_app
def get(app: AppCtx, client_id: str, flow_id: str) -> None:
    """Get one flow (accepts a client ID or hostname)."""
    transport = app.transport()
    emit(app, api.get_flow(transport, resolve_client_arg(transport, client_id), flow_id))


@flows.command()
@click.argument("client_id")
@click.argument("flow_id")
@click.option("--artifact", default=None, metavar="NAME", help="Fetch result rows for this artifact.")
@click.option("--source", default=None, metavar="S", help="Artifact source (requires --artifact).")
@click.option("--all", "fetch_all", is_flag=True, help="Follow cursors and fetch every page.")
@click.option(
    "--out",
    type=click.Path(dir_okay=False),
    default=None,
    metavar="FILE",
    help="Write result rows as JSONL to FILE (requires --artifact).",
)
@_page_budget_option
@pass_app
def results(app: AppCtx, client_id, flow_id, artifact, source, fetch_all, out, page_budget) -> None:
    """List available result artifacts, or fetch rows with --artifact."""
    if source and not artifact:
        raise UsageError("--source requires --artifact")
    if out and not artifact:
        raise UsageError("--out requires --artifact")
    transport = app.transport()
    resolved = resolve_client_arg(transport, client_id)

    if not artifact:
        emit(app, api.list_flow_results(transport, resolved, flow_id))
        return

    if out:
        if fetch_all:
            rows = list(
                api.iter_flow_results(
                    transport, resolved, flow_id, artifact, source=source, page_budget=page_budget
                )
            )
        else:
            rows = _page_rows(
                api.get_flow_results(transport, resolved, flow_id, artifact, source=source)
            )
        dump_jsonl(out, rows)
        emit(app, {"rows": len(rows), "out": out})
    elif fetch_all:
        emit(
            app,
            list(
                api.iter_flow_results(
                    transport, resolved, flow_id, artifact, source=source, page_budget=page_budget
                )
            ),
        )
    else:
        emit(app, api.get_flow_results(transport, resolved, flow_id, artifact, source=source))


def _page_rows(response) -> list:
    """Extract the data rows from one page envelope (or bare array)."""
    if isinstance(response, dict):
        return list(response.get("data") or [])
    return list(response or [])


@flows.command()
@click.argument("client_id")
@click.argument("flow_id")
@click.option("--all", "fetch_all", is_flag=True, help="Follow cursors and fetch every page.")
@_page_budget_option
@pass_app
def logs(app: AppCtx, client_id, flow_id, fetch_all, page_budget) -> None:
    """Get a flow's execution logs (accepts a client ID or hostname)."""
    transport = app.transport()
    resolved = resolve_client_arg(transport, client_id)
    if fetch_all:
        emit(
            app,
            list(api.iter_flow_logs(transport, resolved, flow_id, page_budget=page_budget)),
        )
    else:
        emit(app, api.get_flow_logs(transport, resolved, flow_id))
