"""Label API bindings (spec: getLabels, addLabel, deleteLabel, bulkAddLabels)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..transport import VRTransport


def get_labels(transport: VRTransport, client_id: str) -> Any:
    """GET /clients/{clientId}/labels (spec: getLabels) — bare string array."""
    return transport.request("GET", f"/clients/{quote(client_id, safe='')}/labels")


def add_label(transport: VRTransport, client_id: str, label: str) -> Any:
    """PATCH /clients/{clientId}/labels/{label} (spec: addLabel) — 200, possibly empty body."""
    return transport.request(
        "PATCH",
        f"/clients/{quote(client_id, safe='')}/labels/{quote(label, safe='')}",
    )


def remove_label(transport: VRTransport, client_id: str, label: str) -> Any:
    """DELETE /clients/{clientId}/labels/{label} (spec: deleteLabel) — 204."""
    return transport.request(
        "DELETE",
        f"/clients/{quote(client_id, safe='')}/labels/{quote(label, safe='')}",
    )


def bulk_add_labels(transport: VRTransport, client_ids: list[str], label: str) -> Any:
    """POST /clients/bulk-labels (spec: bulkAddLabels) — body is BulkLabelRequest."""
    return transport.request(
        "POST",
        "/clients/bulk-labels",
        json_body={"client_ids": list(client_ids), "label": label},
    )
