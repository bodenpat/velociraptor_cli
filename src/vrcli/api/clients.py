"""Client API bindings (spec: getClients, getClient, updateClientMetadata, deleteClient)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..errors import NotFoundError, UsageError
from ..pagination import DEFAULT_PAGE_BUDGET, paginate
from ..transport import VRTransport


def list_clients(
    transport: VRTransport,
    *,
    hostname: str | None = None,
    os: str | None = None,
    label: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> Any:
    """GET /clients — one page."""
    return transport.request(
        "GET",
        "/clients",
        params={
            "hostname": hostname,
            "os": os,
            "label": label,
            "status": status.upper() if status else None,
            "limit": limit,
            "cursor": cursor,
        },
    )


def iter_clients(
    transport: VRTransport,
    *,
    hostname: str | None = None,
    os: str | None = None,
    label: str | None = None,
    status: str | None = None,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[dict]:
    """All clients matching the filters, following cursors."""
    return paginate(
        lambda cursor: list_clients(
            transport,
            hostname=hostname,
            os=os,
            label=label,
            status=status,
            cursor=cursor,
        ),
        max_items=max_items,
        page_budget=page_budget,
    )


def get_client(transport: VRTransport, client_id: str) -> Any:
    """GET /clients/{clientId}"""
    return transport.request("GET", f"/clients/{client_id}")


def update_client_metadata(
    transport: VRTransport,
    client_id: str,
    *,
    add: dict[str, str] | None = None,
    remove: list[str] | None = None,
) -> Any:
    """PUT /clients/{clientId} — body is UpdateClientMetadataRequest.

    The spec types `add`/`remove` as JsonNode; we send `add` as an object of
    key->value and `remove` as an array of key names.
    """
    body: dict = {}
    if add:
        body["add"] = add
    if remove:
        body["remove"] = remove
    return transport.request("PUT", f"/clients/{client_id}", json_body=body)


def delete_client(transport: VRTransport, client_id: str) -> Any:
    """DELETE /clients/{clientId}"""
    return transport.request("DELETE", f"/clients/{client_id}")


# -- hostname resolution (PLAN.md §4.1 conventions) -------------------------

_CLIENT_ID_PREFIX = "C."
_HOST_PREFIX = "host:"


def resolve_hostname(transport: VRTransport, hostname: str, *, first: bool = False) -> dict:
    """hostname -> the single matching client record.

    The API filter is a substring match, so re-check for exact (case-
    insensitive) hostname equality and fall back to the substring matches
    only if no exact hit exists. Errors on 0 matches (exit 4) or, without
    first=True, on >1 match (exit 2).
    """
    matches = list(iter_clients(transport, hostname=hostname))
    exact = [
        c
        for c in matches
        if str(c.get("os_info", {}).get("hostname", c.get("hostname", ""))).lower()
        == hostname.lower()
    ]
    candidates = exact or matches
    if not candidates:
        raise NotFoundError(f"No client found with hostname {hostname!r}")
    if len(candidates) > 1 and not first:
        ids = [c.get("client_id", "?") for c in candidates[:10]]
        raise UsageError(
            f"Hostname {hostname!r} matches {len(candidates)} clients ({', '.join(ids)}"
            f"{', …' if len(candidates) > 10 else ''}); pass a client ID or use --first"
        )
    return candidates[0]


def resolve_client_arg(transport: VRTransport, value: str, *, first: bool = False) -> str:
    """Accept a client ID or a hostname anywhere a <client_id> is expected.

    `C.xxxx` is used as-is; a `host:` prefix forces hostname resolution;
    anything else is auto-resolved as a hostname.
    """
    if value.startswith(_HOST_PREFIX):
        return resolve_hostname(transport, value[len(_HOST_PREFIX):], first=first)["client_id"]
    if value.startswith(_CLIENT_ID_PREFIX):
        return value
    return resolve_hostname(transport, value, first=first)["client_id"]
