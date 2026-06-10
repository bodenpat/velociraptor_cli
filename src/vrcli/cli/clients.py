"""`vr clients` — 1:1 with the Client API plus the resolve helper."""

from __future__ import annotations

import click

from ..api import clients as api
from ..errors import UsageError
from ._common import AppCtx, dry_run_option, emit, paging_options, pass_app, require_yes, yes_option


@click.group()
def clients() -> None:
    """Manage and inspect enrolled clients."""


@clients.command(name="list")
@click.option("--hostname", help="Filter by hostname substring.")
@click.option("--os", "os_", help="Filter by operating system.")
@click.option("--label", help="Filter by label.")
@click.option(
    "--status",
    type=click.Choice(["online", "offline", "all"], case_sensitive=False),
    default=None,
    help="Filter by online status.",
)
@paging_options
@pass_app
def list_(app: AppCtx, hostname, os_, label, status, limit, fetch_all, page_budget) -> None:
    """List clients."""
    transport = app.transport()
    if fetch_all:
        data = list(
            api.iter_clients(
                transport,
                hostname=hostname,
                os=os_,
                label=label,
                status=status,
                max_items=limit,
                page_budget=page_budget,
            )
        )
        emit(app, data)
    else:
        emit(
            app,
            api.list_clients(
                transport, hostname=hostname, os=os_, label=label, status=status, limit=limit
            ),
        )


@clients.command()
@click.argument("client_id")
@pass_app
def get(app: AppCtx, client_id: str) -> None:
    """Get one client (accepts a client ID or hostname)."""
    transport = app.transport()
    emit(app, api.get_client(transport, api.resolve_client_arg(transport, client_id)))


@clients.command()
@click.argument("hostname")
@click.option("--first", is_flag=True, help="On multiple matches, take the first instead of erroring.")
@pass_app
def resolve(app: AppCtx, hostname: str, first: bool) -> None:
    """Resolve a hostname to its client record (errors on 0 or >1 matches)."""
    emit(app, api.resolve_hostname(app.transport(), hostname, first=first))


@clients.command()
@click.argument("client_id")
@click.option("--add", "add_", multiple=True, metavar="KEY=VALUE", help="Metadata key to set.")
@click.option("--remove", "remove_", multiple=True, metavar="KEY", help="Metadata key to remove.")
@dry_run_option
@pass_app
def update(app: AppCtx, client_id, add_, remove_, dry_run) -> None:
    """Update client metadata."""
    add: dict[str, str] = {}
    for pair in add_:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise UsageError(f"--add {pair!r} is not of the form key=value")
        add[key] = value
    if not add and not remove_:
        raise UsageError("Nothing to do: pass --add key=value and/or --remove key")
    transport = app.transport(dry_run=dry_run)
    resolved = api.resolve_client_arg(transport, client_id)
    result = api.update_client_metadata(transport, resolved, add=add, remove=list(remove_))
    emit(app, result if result is not None else {"updated": resolved})


@clients.command()
@click.argument("client_id")
@yes_option
@dry_run_option
@pass_app
def delete(app: AppCtx, client_id: str, yes: bool, dry_run: bool) -> None:
    """Delete a client from the organization."""
    require_yes(yes, f"delete client {client_id}")
    transport = app.transport(dry_run=dry_run)
    resolved = api.resolve_client_arg(transport, client_id)
    result = api.delete_client(transport, resolved)
    emit(app, result if result is not None else {"deleted": resolved})
