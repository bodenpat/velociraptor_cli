"""Evidence directories with SHA-256 manifests (PLAN.md §4.2).

Chain-of-custody by default: every ops command that persists collection
output does it through EvidenceDir, which hashes every file it writes and
finalizes a manifest.json recording what was collected, from where, when,
and by whom.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import UsageError

MANIFEST_NAME = "manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def operator_identity() -> dict:
    """Best-effort operator identity for audit/manifest records."""
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    return {"user": user, "host": socket.gethostname()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceDir:
    """Collects files under a directory and finalizes a SHA-256 manifest."""

    def __init__(self, out_dir: str | Path, *, context: dict | None = None):
        self.path = Path(out_dir)
        if self.path.exists() and any(self.path.iterdir()):
            raise UsageError(
                f"Evidence directory {self.path} already exists and is not empty; "
                f"refusing to mix evidence from different collections"
            )
        self.path.mkdir(parents=True, exist_ok=True)
        self.context = context or {}
        self.started = utc_now()
        self._files: list[dict] = []

    def write_json(self, name: str, data: Any) -> Path:
        return self.write_bytes(
            name, json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        )

    def write_jsonl(self, name: str, rows: list) -> Path:
        payload = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
        return self.write_bytes(name, payload.encode("utf-8"))

    def write_bytes(self, name: str, payload: bytes) -> Path:
        target = self._safe_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(payload)
        self._files.append(
            {
                "name": str(target.relative_to(self.path)),
                "sha256": sha256_file(target),
                "size_bytes": target.stat().st_size,
                "written_at": utc_now(),
            }
        )
        return target

    def add_existing_file(self, source: str | Path, *, note: str | None = None) -> Path:
        """Copy an externally-obtained file (e.g. GUI download) into evidence."""
        source = Path(source)
        if not source.is_file():
            raise UsageError(f"{source} is not a file")
        target = self._safe_path(source.name)
        target.write_bytes(source.read_bytes())
        entry = {
            "name": str(target.relative_to(self.path)),
            "sha256": sha256_file(target),
            "size_bytes": target.stat().st_size,
            "written_at": utc_now(),
            "ingested_from": str(source.resolve()),
        }
        if note:
            entry["note"] = note
        self._files.append(entry)
        return target

    def finalize(self, *, extra: dict | None = None) -> dict:
        """Write manifest.json and return it."""
        manifest = {
            "manifest_version": 1,
            "vrcli_version": __version__,
            "operator": operator_identity(),
            "collection": self.context,
            "started_at": self.started,
            "finalized_at": utc_now(),
            "files": self._files,
        }
        if extra:
            manifest.update(extra)
        with open(self.path / MANIFEST_NAME, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False, default=str)
            fh.write("\n")
        return manifest

    def _safe_path(self, name: str) -> Path:
        """Resolve a file name inside the evidence dir, rejecting traversal.

        Artifact names become file names; `/` is allowed for subdirs but the
        result must stay inside the evidence directory.
        """
        cleaned = name.replace("\\", "_").lstrip("/")
        target = (self.path / cleaned).resolve()
        if not target.is_relative_to(self.path.resolve()):
            raise UsageError(f"Refusing to write outside the evidence directory: {name!r}")
        if target == (self.path / MANIFEST_NAME).resolve():
            raise UsageError(f"{MANIFEST_NAME} is reserved for the evidence manifest")
        return target
