"""Hunt API bindings (spec: getHunts, createHunt, getHunt, updateHuntStatus,
getHuntResults, getHuntErrors)."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from typing import Any
from urllib.parse import quote

from ..errors import APIError, UsageError
from ..pagination import DEFAULT_PAGE_BUDGET, paginate
from ..transport import VRTransport

MICROSECONDS_PER_SECOND = 1_000_000

# CLI OS values (lowercase) -> HuntOsCondition enum (spec: ALL/WINDOWS/LINUX/OSX).
_OS_CONDITIONS = {
    "all": "ALL",
    "windows": "WINDOWS",
    "linux": "LINUX",
    "darwin": "OSX",
    "osx": "OSX",
}


def list_hunts(
    transport: VRTransport,
    *,
    state: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> Any:
    """GET /hunts (spec: getHunts) — one page."""
    return transport.request(
        "GET",
        "/hunts",
        params={
            "state": state.upper() if state else None,
            "sort": sort.upper() if sort else None,
            "limit": limit,
            "cursor": cursor,
        },
    )


def iter_hunts(
    transport: VRTransport,
    *,
    state: str | None = None,
    sort: str | None = None,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[dict]:
    """All hunts matching the filters, following cursors (spec: getHunts)."""
    return paginate(
        lambda cursor: list_hunts(transport, state=state, sort=sort, cursor=cursor),
        max_items=max_items,
        page_budget=page_budget,
    )


def create_hunt(transport: VRTransport, body: dict) -> str | dict:
    """POST /hunts (spec: createHunt) — body is the Hunt schema.

    The spec types the 200 response as a bare JSON string holding the new
    hunt ID; defensively, an object response is normalized via its
    `hunt_id`/`id` key. With a dry-run transport the request description
    dict is returned unchanged.
    """
    result = transport.request("POST", "/hunts", json_body=body)
    if isinstance(result, dict):
        if result.get("dry_run"):
            return result
        for key in ("hunt_id", "id"):
            if result.get(key):
                return str(result[key])
        raise APIError(f"createHunt returned an unrecognized payload (keys: {sorted(result)})")
    return str(result)


def get_hunt(transport: VRTransport, hunt_id: str) -> Any:
    """GET /hunts/{huntId} (spec: getHunt) — details plus stats."""
    return transport.request("GET", f"/hunts/{quote(hunt_id, safe='')}")


def set_hunt_state(transport: VRTransport, hunt_id: str, desired_state: str) -> Any:
    """PATCH /hunts/{huntId}?desiredState=… (spec: updateHuntStatus).

    desired_state is one of the spec enum values (UNSET/PAUSED/RUNNING/
    STOPPED/ARCHIVED/DELETED, any case). Returns None on the documented 204.
    """
    return transport.request(
        "PATCH",
        f"/hunts/{quote(hunt_id, safe='')}",
        params={"desiredState": desired_state.upper()},
    )


def get_hunt_results(
    transport: VRTransport,
    hunt_id: str,
    *,
    limit: int | None = None,
    cursor: str | None = None,
) -> Any:
    """GET /hunts/{huntId}/results (spec: getHuntResults) — one page."""
    return transport.request(
        "GET",
        f"/hunts/{quote(hunt_id, safe='')}/results",
        params={"limit": limit, "cursor": cursor},
    )


def iter_hunt_results(
    transport: VRTransport,
    hunt_id: str,
    *,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[dict]:
    """All hunt result rows, following cursors (spec: getHuntResults)."""
    return paginate(
        lambda cursor: get_hunt_results(transport, hunt_id, cursor=cursor),
        max_items=max_items,
        page_budget=page_budget,
    )


def get_hunt_errors(transport: VRTransport, hunt_id: str) -> Any:
    """GET /hunts/{huntId}/errors (spec: getHuntErrors) — bare HuntErrorResponse array."""
    return transport.request("GET", f"/hunts/{quote(hunt_id, safe='')}/errors")


# -- request building --------------------------------------------------------


def build_hunt_body(
    start_request: dict,
    *,
    labels: Sequence[str] = (),
    excluded_labels: Sequence[str] = (),
    os: str | None = None,
    client_limit: int | None = None,
    expires_in: int | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
) -> dict:
    """Assemble a Hunt schema body (spec: Hunt/HuntCondition/HuntOsCondition).

    start_request is a ready ArtifactCollectorArgs dict (see
    vrcli.cli._common.build_collector_args). Only keys actually set are
    included. CLI OS values are lowercase; `darwin` maps to the spec enum
    value OSX (the enum is ALL/WINDOWS/LINUX/OSX).

    expires_in is seconds from now. ASSUMPTION: the hosted API follows the
    Velociraptor convention that `expires` is an epoch timestamp in
    MICROSECONDS (the spec only says int64); we send
    int((time.time() + expires_in) * 1_000_000). Verify empirically against
    the tenant in Phase 3 alongside the desiredState lifecycle checks
    (PLAN.md §10.5).
    """
    body: dict = {"start_request": start_request}
    condition: dict = {}
    if labels:
        condition["labels"] = {"label": list(labels)}
    if excluded_labels:
        condition["excluded_labels"] = {"label": list(excluded_labels)}
    if os:
        try:
            condition["os"] = {"os": _OS_CONDITIONS[os.lower()]}
        except KeyError:
            raise UsageError(
                f"Unknown OS {os!r} (expected windows, linux, darwin, or all)"
            ) from None
    if condition:
        body["condition"] = condition
    if client_limit is not None:
        body["client_limit"] = client_limit
    if expires_in is not None:
        body["expires"] = int((time.time() + expires_in) * MICROSECONDS_PER_SECOND)
    if description:
        body["hunt_description"] = description
    if tags:
        body["tags"] = list(tags)
    return body
