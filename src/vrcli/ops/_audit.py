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

from ..config import ENV_API_KEY, REDACTED
from ._evidence import operator_identity, utc_now

ENV_AUDIT_LOG = "R7_VR_AUDIT_LOG"
AUDIT_NAME = "audit.jsonl"


def _redacted_argv() -> list[str]:
    """argv with anything resembling the key scrubbed. The key cannot be an
    argument by design, but strip defensively in case of operator error."""
    key = os.environ.get(ENV_API_KEY, "")
    return [REDACTED if (key and key in arg) else arg for arg in sys.argv[1:]]


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


def write_audit(
    command: str,
    *,
    out_dir: str | Path | None = None,
    created: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Append the record to the evidence dir audit log and/or the central one."""
    record = audit_record(command, created=created, extra=extra)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"

    targets: list[Path] = []
    if out_dir is not None:
        targets.append(Path(out_dir) / AUDIT_NAME)
    central = os.environ.get(ENV_AUDIT_LOG)
    if central:
        targets.append(Path(central))

    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    return record
