"""Audit JSONL records for every ops command (PLAN.md §4.2).

One line per ops invocation: timestamp, operator/env identity, command,
args with secrets stripped, and any hunt/flow IDs created. Records go to
<evidence dir>/audit.jsonl when the command has an output directory, and
additionally to $R7_VR_AUDIT_LOG if set (a central, append-only log on the
SOAR host).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..config import REDACTED
from ..errors import UsageError
from ..transport import active_secrets
from ._evidence import operator_identity, utc_now

ENV_AUDIT_LOG = "R7_VR_AUDIT_LOG"
AUDIT_NAME = "audit.jsonl"


def _scrub(text: str) -> str:
    """Replace every registered secret (from any source — env var OR key file)
    in `text` with ***. Source-independent: reads the live secret registry the
    transport populates, not just os.environ[R7_VR_API_KEY]."""
    for secret in active_secrets():
        if secret and secret in text:
            text = text.replace(secret, REDACTED)
    return text


def _redacted_argv() -> list[str]:
    """argv with any registered secret scrubbed. The key cannot be an argument
    by design, but strip defensively in case of operator error — regardless of
    whether the key came from R7_VR_API_KEY or R7_VR_API_KEY_FILE."""
    return [_scrub(arg) for arg in sys.argv[1:]]


def audit_record(command: str, *, created: dict | None = None, extra: dict | None = None) -> dict:
    record = {
        "ts": utc_now(),
        "operator": operator_identity(),
        "command": command,
        "argv": _redacted_argv(),
        "vrcli_pid": os.getpid(),
    }
    if created:
        record["created"] = created  # e.g. {"flow_id": ..., "client_id": ...}
    if extra:
        record.update(extra)
    return record


def _append_central(line: str) -> None:
    """Append one JSONL line to $R7_VR_AUDIT_LOG, if configured."""
    central = os.environ.get(ENV_AUDIT_LOG)
    if not central:
        return
    target = Path(central)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        # Chain-of-custody: a failed audit write must not pass silently.
        raise UsageError(f"Cannot write central audit log {central}: {exc.strerror}") from exc


def write_audit(
    command: str,
    *,
    out_dir: str | Path | None = None,
    created: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Append the record to the evidence-dir audit log and/or the central one.

    Use this for commands with no evidence directory (or where the audit line
    need not be in the integrity manifest). For evidence-producing commands,
    prefer record_evidence_audit() so the audit line is hashed into the
    manifest before finalize().
    """
    record = audit_record(command, created=created, extra=extra)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    if out_dir is not None:
        target = Path(out_dir) / AUDIT_NAME
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            raise UsageError(f"Cannot write audit log: {exc.strerror}") from exc
    _append_central(line)
    return record


def record_evidence_audit(
    evidence,
    command: str,
    *,
    created: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Write the audit record INTO an EvidenceDir (so finalize() hashes it into
    the manifest), then append the same line to the central log.

    Must be called BEFORE evidence.finalize() so audit.jsonl is in manifest.json
    with its SHA-256 — chain-of-custody covers the audit record itself.
    """
    record = audit_record(command, created=created, extra=extra)
    evidence.write_jsonl(AUDIT_NAME, [record])
    _append_central(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return record
