"""Persist a finished flow's output into an evidence directory.

Shared by `vr ops triage`, `vr ops live`, and the triage `--check` resume
path: result rows for every available artifact, the flow's execution logs,
and the flow record itself, all hashed into the manifest.

Until Rapid7 exposes file/container download over REST (PLAN.md §2 known
gap), result rows + logs + manifest IS the evidence package; raw uploaded
files require the GUI and can be attached later with
EvidenceDir.add_existing_file().
"""

from __future__ import annotations

from ..pagination import paginate
from ..transport import VRTransport
from ._evidence import EvidenceDir


def collect_flow_evidence(
    transport: VRTransport,
    client_id: str,
    flow_id: str,
    evidence: EvidenceDir,
    *,
    page_budget: int = 100,
) -> dict:
    """Write flow record + per-artifact result rows + logs into `evidence`.

    Returns a summary dict (artifact -> row count, log count, file list).
    Does not finalize the manifest; the caller does, so it can attach
    context first.
    """
    from ..api import flows as flows_api

    flow = flows_api.get_flow(transport, client_id, flow_id)
    evidence.write_json("flow.json", flow)

    available = flows_api.list_flow_results(transport, client_id, flow_id) or []
    artifact_names = [
        entry if isinstance(entry, str) else entry.get("artifact") or entry.get("name", "")
        for entry in available
    ]
    artifact_names = [name for name in artifact_names if name]

    row_counts: dict[str, int] = {}
    for artifact in artifact_names:
        rows = list(
            paginate(
                lambda cursor, _a=artifact: flows_api.get_flow_results(
                    transport, client_id, flow_id, _a, cursor=cursor
                ),
                page_budget=page_budget,
            )
        )
        evidence.write_jsonl(f"results/{artifact}.jsonl", rows)
        row_counts[artifact] = len(rows)

    logs = list(
        paginate(
            lambda cursor: flows_api.get_flow_logs(transport, client_id, flow_id, cursor=cursor),
            page_budget=page_budget,
        )
    )
    evidence.write_jsonl("logs.jsonl", logs)

    return {
        "client_id": client_id,
        "flow_id": flow_id,
        "state": str(flow.get("state", "")).upper() if isinstance(flow, dict) else "",
        "artifacts": row_counts,
        "log_lines": len(logs),
    }
