"""Flow API bindings (spec: getClientFlows, createFlow, getClientFlow,
getAvailableFlowResults, getFlowResults, getFlowResults_1, getFlowLogs)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from ..errors import APIError
from ..pagination import DEFAULT_PAGE_BUDGET, paginate
from ..transport import VRTransport


def _seg(value: str) -> str:
    """URL-encode one user-supplied path segment."""
    return quote(value, safe="")


def list_flows(
    transport: VRTransport,
    client_id: str,
    *,
    creator: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> Any:
    """GET /clients/{clientId}/flows — one page (spec: getClientFlows)."""
    return transport.request(
        "GET",
        f"/clients/{_seg(client_id)}/flows",
        params={
            "creator": creator,
            "sort": sort.upper() if sort else None,
            "limit": limit,
            "cursor": cursor,
        },
    )


def iter_flows(
    transport: VRTransport,
    client_id: str,
    *,
    creator: str | None = None,
    sort: str | None = None,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[dict]:
    """All flows for a client matching the filters, following cursors."""
    return paginate(
        lambda cursor: list_flows(
            transport,
            client_id,
            creator=creator,
            sort=sort,
            cursor=cursor,
        ),
        max_items=max_items,
        page_budget=page_budget,
    )


def create_flow(transport: VRTransport, client_id: str, collector_args: dict) -> Any:
    """POST /clients/{clientId}/flows (spec: createFlow).

    The spec types the 200 response as a bare JSON string holding the new
    flow/session ID. Normalize defensively: object-wrapped variants are
    accepted via the flow_id / session_id / id keys; the return value is
    always a str. Dry-run request descriptions pass through unchanged.
    """
    response = transport.request(
        "POST",
        f"/clients/{_seg(client_id)}/flows",
        json_body=collector_args,
    )
    return _normalize_flow_id(response)


def _normalize_flow_id(response: Any) -> Any:
    if isinstance(response, dict):
        if response.get("dry_run"):
            return response
        for key in ("flow_id", "session_id", "id"):
            value = response.get(key)
            if value:
                return str(value)
        raise APIError("createFlow response contained no flow ID", detail=response)
    if isinstance(response, str):
        text = response.strip()
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            text = text[1:-1]  # non-JSON content type: body still quoted
        if not text:
            raise APIError("createFlow returned an empty flow ID")
        return text
    if response is None:
        raise APIError("createFlow returned no body; expected the new flow ID")
    return str(response)


def get_flow(transport: VRTransport, client_id: str, flow_id: str) -> Any:
    """GET /clients/{clientId}/flows/{flowId} (spec: getClientFlow)."""
    return transport.request("GET", f"/clients/{_seg(client_id)}/flows/{_seg(flow_id)}")


def list_flow_results(transport: VRTransport, client_id: str, flow_id: str) -> Any:
    """GET /clients/{clientId}/flows/{flowId}/results (spec: getAvailableFlowResults).

    Lists which artifact result sets exist for the flow; not paginated.
    """
    return transport.request(
        "GET", f"/clients/{_seg(client_id)}/flows/{_seg(flow_id)}/results"
    )


def get_flow_results(
    transport: VRTransport,
    client_id: str,
    flow_id: str,
    artifact: str,
    *,
    source: str | None = None,
    cursor: str | None = None,
) -> Any:
    """GET /clients/{clientId}/flows/{flowId}/results/{artifact}[/{source}]
    (spec: getFlowResults, getFlowResults_1) — one page.

    These endpoints take only a `cursor` query param — no `limit` — per spec.
    """
    path = f"/clients/{_seg(client_id)}/flows/{_seg(flow_id)}/results/{_seg(artifact)}"
    if source is not None:
        path += f"/{_seg(source)}"
    return transport.request("GET", path, params={"cursor": cursor})


def iter_flow_results(
    transport: VRTransport,
    client_id: str,
    flow_id: str,
    artifact: str,
    *,
    source: str | None = None,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[dict]:
    """All result rows for one artifact (and optional source), following cursors."""
    return paginate(
        lambda cursor: get_flow_results(
            transport,
            client_id,
            flow_id,
            artifact,
            source=source,
            cursor=cursor,
        ),
        max_items=max_items,
        page_budget=page_budget,
    )


def get_flow_logs(
    transport: VRTransport,
    client_id: str,
    flow_id: str,
    *,
    cursor: str | None = None,
) -> Any:
    """GET /clients/{clientId}/flows/{flowId}/logs (spec: getFlowLogs) — one page.

    Takes only a `cursor` query param — no `limit` — per spec.
    """
    return transport.request(
        "GET",
        f"/clients/{_seg(client_id)}/flows/{_seg(flow_id)}/logs",
        params={"cursor": cursor},
    )


def iter_flow_logs(
    transport: VRTransport,
    client_id: str,
    flow_id: str,
    *,
    max_items: int | None = None,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> Iterator[dict]:
    """All execution log lines for a flow, following cursors."""
    return paginate(
        lambda cursor: get_flow_logs(transport, client_id, flow_id, cursor=cursor),
        max_items=max_items,
        page_budget=page_budget,
    )
