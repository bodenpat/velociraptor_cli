"""`vr ops enrich` — one-shot host enrichment blob (PLAN.md §4.2).

Read-only composite for InsightIDR investigations: the full client record,
its labels, last-seen, and recent flows in a single JSON document a SOAR
step can attach as-is. No evidence directory is produced, so the audit
record goes only to the central log ($R7_VR_AUDIT_LOG, when set).

The REST spec leaves the client and flow response schemas undefined
(`content: {}`), so every field access here is defensive.
"""

from __future__ import annotations

from typing import Any

from ..transport import VRTransport
from ._audit import write_audit

DEFAULT_RECENT_FLOWS = 10

_LAST_SEEN_KEYS = ("last_seen_at", "last_seen_time")


def last_seen(client: Any) -> Any:
    """Best-effort last-seen from a client record.

    Client records have been observed with either `last_seen_at` or
    `last_seen_time` (the spec defines neither); check both, else None.
    """
    if isinstance(client, dict):
        for key in _LAST_SEEN_KEYS:
            value = client.get(key)
            if value is not None:
                return value
    return None


def flow_summary(flow: Any) -> dict:
    """Reduce one flow record to {flow_id, state, create_time, artifacts}.

    Tolerates missing fields: the ID may be `session_id` or `flow_id`,
    and `request.artifacts` (the collected artifact names) may be absent
    entirely — then `artifacts` is [].
    """
    if not isinstance(flow, dict):
        return {"flow_id": None, "state": None, "create_time": None, "artifacts": []}
    request = flow.get("request")
    artifacts = request.get("artifacts") if isinstance(request, dict) else None
    return {
        "flow_id": flow.get("session_id") or flow.get("flow_id"),
        "state": flow.get("state"),
        "create_time": flow.get("create_time"),
        "artifacts": list(artifacts) if artifacts else [],
    }


def enrich_host(
    transport: VRTransport,
    host: str,
    *,
    flows: int = DEFAULT_RECENT_FLOWS,
) -> dict:
    """Build the enrichment document for one host (client ID or hostname).

    Composite over getClients (hostname resolution), getClient, getLabels,
    and getClientFlows (sort=NEWEST, limit=`flows`). Read-only — nothing is
    created or mutated — but still writes one central audit record.
    """
    from ..api import clients as clients_api
    from ..api import flows as flows_api
    from ..api import labels as labels_api

    client_id = clients_api.resolve_client_arg(transport, host)
    client = clients_api.get_client(transport, client_id)
    labels = labels_api.get_labels(transport, client_id) or []

    response = flows_api.list_flows(transport, client_id, sort="NEWEST", limit=flows)
    records = response.get("data") if isinstance(response, dict) else response
    recent = [flow_summary(flow) for flow in (records or [])[:flows]]

    document = {
        "client": client,
        "labels": labels,
        "last_seen_at": last_seen(client),
        "recent_flows": recent,
    }
    write_audit(
        "ops enrich",
        out_dir=None,
        extra={"client_id": client_id, "host_arg": host, "recent_flows": len(recent)},
    )
    return document
