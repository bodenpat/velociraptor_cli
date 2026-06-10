"""`vr ops live` — targeted live response via curated artifact presets (PLAN.md §4.2).

Composite over the Flow API: resolve the host (spec: getClients), start one
collection with the preset's artifacts (spec: createFlow), always wait for it
to finish (spec: getClientFlow), then persist the flow record, every
artifact's result rows, and the execution logs (spec: getAvailableFlowResults,
getFlowResults, getFlowLogs) into an evidence directory with a SHA-256
manifest and an audit JSONL record — chain-of-custody by default.

PRESETS maps preset name -> artifact list. The mapping is curated, not
API-derived: edit it as the team's live-response playbook evolves. The
artifact names are standard Velociraptor built-ins, but their presence in the
hosted tenant is an assumption — verify each against the tenant artifact
library in Phase 3. No artifact parameters are sent (presets run with
defaults).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..api import flows as flows_api
from ..api.clients import resolve_client_arg
from ..errors import UsageError
from ..transport import VRTransport
from ._audit import record_evidence_audit
from ._collect import collect_flow_evidence
from ._evidence import EvidenceDir
from ._wait import wait_for_flow

PRESETS: dict[str, list[str]] = {
    "pslist": ["Windows.System.Pslist"],
    "netstat": ["Windows.Network.Netstat"],
    "persistence": ["Windows.Packs.Persistence"],
    "tasks": ["Windows.System.TaskScheduler"],
    "browser-history": ["Windows.Applications.Chrome.History"],
    "users": ["Windows.Sys.Users"],
}


def preset_artifacts(presets: Sequence[str]) -> list[str]:
    """Union of the artifact lists for the given presets, order-preserving.

    Multiple presets are allowed; their artifact lists are unioned (deduped)
    into a single collection so the host runs one flow, not one per preset.
    """
    artifacts: list[str] = []
    for name in presets:
        key = name.lower()
        if key not in PRESETS:
            raise UsageError(f"Unknown preset {name!r} (valid: {', '.join(PRESETS)})")
        for artifact in PRESETS[key]:
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def default_out_dir(client_id: str, flow_id: str) -> Path:
    """./evidence/live-<client_id>-<flow_id> (evidence/ is gitignored)."""
    return Path("evidence") / f"live-{client_id}-{flow_id}"


def live_response(
    transport: VRTransport,
    host: str,
    presets: Sequence[str],
    *,
    out_dir: str | Path | None = None,
    timeout: int,
    poll_interval: int,
    urgent: bool = False,
) -> dict:
    """Run a curated live-response collection end to end; return the summary.

    Resolve `host` (client ID or hostname) -> POST the unioned preset
    artifacts as one flow (spec: createFlow, ArtifactCollectorArgs with no
    parameters) -> always wait for FINISHED (spec: getClientFlow; raises
    WaitTimeout, exit 5, on expiry) -> write evidence + manifest -> append
    the audit record.

    With transport.dry_run the described request is returned instead and
    nothing is sent, written, or audited.
    """
    artifacts = preset_artifacts(presets)
    preset_names = list(dict.fromkeys(name.lower() for name in presets))
    body: dict = {"artifacts": artifacts, "specs": []}
    if urgent:
        body["urgent"] = True

    client_id = resolve_client_arg(transport, host)
    created = flows_api.create_flow(transport, client_id, body)
    if isinstance(created, dict) and created.get("dry_run"):
        return {
            "dry_run": True,
            "host": host,
            "client_id": client_id,
            "presets": preset_names,
            "artifacts": artifacts,
            "request": created,
        }

    flow_id = created
    wait_for_flow(transport, client_id, flow_id, timeout=timeout, poll_interval=poll_interval)

    # Resolve so EvidenceDir's manifest paths work for relative --out values too.
    out_path = (Path(out_dir) if out_dir is not None else default_out_dir(client_id, flow_id)).resolve()
    evidence = EvidenceDir(
        out_path,
        context={
            "command": "ops live",
            "host": host,
            "client_id": client_id,
            "flow_id": flow_id,
            "presets": preset_names,
            "artifacts": artifacts,
        },
    )
    collected = collect_flow_evidence(transport, client_id, flow_id, evidence)
    record_evidence_audit(
        evidence,
        "ops live",
        created={"client_id": client_id, "flow_id": flow_id},
        extra={"presets": preset_names, "artifacts": artifacts},
    )
    manifest = evidence.finalize()

    return {
        "ops": "live",
        "host": host,
        "client_id": client_id,
        "flow_id": flow_id,
        "state": collected["state"],
        "presets": preset_names,
        "artifacts": collected["artifacts"],
        "log_lines": collected["log_lines"],
        "out_dir": str(evidence.path),
        "manifest_files": len(manifest["files"]),
    }
