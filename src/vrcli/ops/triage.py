"""Triage collection composite (PLAN.md §4.2 row 1, §8 Phase 2 — the vertical slice).

`vr ops triage` resolves a host, starts a `Windows.KapeFiles.Targets` flow,
polls until it finishes, and persists all result rows + flow logs into an
evidence directory with a SHA-256 manifest and an audit JSONL record —
chain-of-custody by default.

Resume contract for SOAR step-duration caps (PLAN.md §8 Phase 2): `--start`
returns an opaque token (base64url JSON: {v, client_id, flow_id, out, host});
`--check <token>` polls, and the terminal `--check` writes the evidence
directory and manifest exactly as `--wait` would.

The default evidence directory is ./evidence/triage-<client_id>-<flow_id>;
the repo's .gitignore blocks evidence/ so collections can never be committed.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ..api import flows as flows_api
from ..api.clients import resolve_client_arg
from ..errors import UsageError
from ..transport import VRTransport
from ._audit import write_audit
from ._collect import collect_flow_evidence
from ._evidence import EvidenceDir
from ._wait import check_flow, flow_state, wait_for_flow

AUDIT_COMMAND = "ops triage"
DEFAULT_ARTIFACT = "Windows.KapeFiles.Targets"
DEFAULT_TARGETS = "KapeTriage"
TOKEN_VERSION = 1


def build_triage_args(
    artifact: str = DEFAULT_ARTIFACT,
    targets: str = DEFAULT_TARGETS,
    *,
    max_upload_bytes: int | None = None,
    urgent: bool = False,
) -> dict:
    """ArtifactCollectorArgs for a KapeFiles triage (spec: createFlow request body).

    The KapeFiles target-group checkbox is passed as a spec env parameter
    whose KEY is the target group name (e.g. `KapeTriage`) and whose value
    is the Velociraptor boolean string "Y" (booleans are "Y"/"N" strings).
    The parameter NAME is an assumption about the Windows.KapeFiles.Targets
    artifact, not part of the REST spec — verify against the tenant's
    artifact definition in Phase 3.
    """
    body: dict = {
        "artifacts": [artifact],
        "specs": [
            {
                "artifact": artifact,
                "parameters": {"env": [{"key": targets, "value": "Y"}]},
            }
        ],
    }
    if max_upload_bytes is not None:
        body["max_upload_bytes"] = max_upload_bytes
    if urgent:
        body["urgent"] = True
    return body


def default_out_dir(client_id: str, flow_id: str) -> str:
    """./evidence/triage-<client_id>-<flow_id> (evidence/ is gitignored)."""
    return str(Path("evidence") / f"triage-{client_id}-{flow_id}")


# -- resume token (PLAN.md §8 Phase 2 --start/--check contract) --------------


def encode_resume_token(*, client_id: str, flow_id: str, out: str, host: str) -> str:
    """Opaque resume token: base64url of {v, client_id, flow_id, out, host}."""
    payload = {
        "v": TOKEN_VERSION,
        "client_id": client_id,
        "flow_id": flow_id,
        "out": out,
        "host": host,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_resume_token(token: str) -> dict:
    """Decode and validate a --start resume token. UsageError (exit 2) on garbage."""
    try:
        raw = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except ValueError:
        raise UsageError("Invalid resume token: not a vr ops triage --start token") from None
    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
        raise UsageError("Invalid resume token: unsupported version or shape")
    missing = [key for key in ("client_id", "flow_id", "out") if not payload.get(key)]
    if missing:
        raise UsageError(f"Invalid resume token: missing {', '.join(missing)}")
    return payload


# -- composite modes ---------------------------------------------------------


def _create_triage_flow(
    transport: VRTransport,
    host: str,
    *,
    artifact: str,
    targets: str,
    max_upload_bytes: int | None,
    urgent: bool,
) -> tuple[str, Any]:
    """Resolve `host` and create the triage flow.

    Returns (client_id, flow_id). Under a dry-run transport the second
    element is the request-description dict instead (see createFlow).
    """
    client_id = resolve_client_arg(transport, host)
    args = build_triage_args(
        artifact, targets, max_upload_bytes=max_upload_bytes, urgent=urgent
    )
    return client_id, flows_api.create_flow(transport, client_id, args)


def run_triage(
    transport: VRTransport,
    host: str,
    *,
    artifact: str = DEFAULT_ARTIFACT,
    targets: str = DEFAULT_TARGETS,
    out: str | None = None,
    max_upload_bytes: int | None = None,
    urgent: bool = False,
    timeout: int,
    poll_interval: int,
) -> dict:
    """--wait mode: create the flow, block until FINISHED, persist evidence.

    Raises WaitTimeout (exit 5) past `timeout` and APIError (exit 6) if the
    flow ends in ERROR. Under a dry-run transport, returns the described
    create-flow request without creating anything.
    """
    client_id, created = _create_triage_flow(
        transport,
        host,
        artifact=artifact,
        targets=targets,
        max_upload_bytes=max_upload_bytes,
        urgent=urgent,
    )
    if isinstance(created, dict):  # dry-run request description
        return created
    flow_id = created
    wait_for_flow(transport, client_id, flow_id, timeout=timeout, poll_interval=poll_interval)
    return _collect_finalize_audit(
        transport,
        client_id=client_id,
        flow_id=flow_id,
        out=out or default_out_dir(client_id, flow_id),
        context={
            "host": host,
            "client_id": client_id,
            "flow_id": flow_id,
            "artifact": artifact,
            "targets": targets,
        },
        mode="wait",
    )


def start_triage(
    transport: VRTransport,
    host: str,
    *,
    artifact: str = DEFAULT_ARTIFACT,
    targets: str = DEFAULT_TARGETS,
    out: str | None = None,
    max_upload_bytes: int | None = None,
    urgent: bool = False,
) -> dict:
    """--start mode: create the flow and return a resume token for --check.

    Audits centrally only ($R7_VR_AUDIT_LOG): the evidence directory does
    not exist until the terminal --check writes it. Under a dry-run
    transport, returns the described create-flow request instead.
    """
    client_id, created = _create_triage_flow(
        transport,
        host,
        artifact=artifact,
        targets=targets,
        max_upload_bytes=max_upload_bytes,
        urgent=urgent,
    )
    if isinstance(created, dict):  # dry-run request description
        return created
    flow_id = created
    out = out or default_out_dir(client_id, flow_id)
    token = encode_resume_token(client_id=client_id, flow_id=flow_id, out=out, host=host)
    write_audit(
        AUDIT_COMMAND,
        out_dir=None,
        created={"client_id": client_id, "flow_id": flow_id},
        extra={"mode": "start", "out": out},
    )
    return {"done": False, "client_id": client_id, "flow_id": flow_id, "token": token, "out": out}


def check_triage(transport: VRTransport, token: str) -> dict:
    """--check mode: poll the tokenized flow; on FINISHED, persist evidence.

    Still running -> {done: false, state, token} (exit 0, nothing written
    except the central audit log). FINISHED -> the full evidence collection
    into the token's out dir, exactly as --wait would. ERROR -> APIError
    (exit 6) via check_flow.
    """
    payload = decode_resume_token(token)
    client_id, flow_id = payload["client_id"], payload["flow_id"]
    done, flow = check_flow(transport, client_id, flow_id)
    if not done:
        state = flow_state(flow)
        write_audit(
            AUDIT_COMMAND,
            out_dir=None,
            extra={"mode": "check", "state": state, "client_id": client_id, "flow_id": flow_id},
        )
        return {"done": False, "state": state, "token": token}
    return _collect_finalize_audit(
        transport,
        client_id=client_id,
        flow_id=flow_id,
        out=payload["out"],
        context={
            "host": payload.get("host"),
            "client_id": client_id,
            "flow_id": flow_id,
            "resumed_from_token": True,
        },
        mode="check",
    )


def _collect_finalize_audit(
    transport: VRTransport,
    *,
    client_id: str,
    flow_id: str,
    out: str,
    context: dict,
    mode: str,
) -> dict:
    """Shared terminal path: EvidenceDir -> collect -> finalize -> audit -> summary."""
    evidence = EvidenceDir(out, context=context)
    summary = collect_flow_evidence(transport, client_id, flow_id, evidence)
    manifest = evidence.finalize()
    write_audit(
        AUDIT_COMMAND,
        out_dir=evidence.path,
        created={"client_id": client_id, "flow_id": flow_id},
        extra={"mode": mode, "out": str(evidence.path)},
    )
    return {
        "done": True,
        "client_id": client_id,
        "flow_id": flow_id,
        "out": str(evidence.path),
        "artifacts": summary["artifacts"],
        "log_lines": summary["log_lines"],
        "manifest_files": len(manifest["files"]),
    }
