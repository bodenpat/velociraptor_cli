"""Fleet-wide IOC hunt: hash / filename-glob / YARA sweeps (PLAN.md §4.2).

Builds a single-artifact hunt (default ``Windows.Search.FileFinder``) whose
parameters express the supplied IOC types, launches it fleet-wide with
label/OS conditions (spec: createHunt), and — when the caller waits —
summarizes per-client hits from the hunt's result rows (spec:
getHuntResults) and errors (spec: getHuntErrors).

ASSUMPTION (verify against the tenant artifact in Phase 3): the artifact
parameter names ``SearchFilesGlob``, ``SearchFilesGlobTable``, ``YaraRule``,
and ``Calculate_Hash`` come from the open-source Windows.Search.FileFinder
definition — the REST spec only carries opaque key/value specs, so none of
these names appear in it. Velociraptor boolean artifact parameters are the
strings "Y"/"N".

Hash IOCs are matched client-side (in this process) during the summary
step: the hunt only asks FileFinder to compute hashes; the rows are
filtered here against the requested SHA-256 values.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..api import hunts as hunts_api
from ..errors import UsageError
from ..pagination import DEFAULT_PAGE_BUDGET
from ..transport import VRTransport
from ._wait import wait_for_hunt

DEFAULT_ARTIFACT = "Windows.Search.FileFinder"
DEFAULT_SCOPE_GLOB = "C:\\Users\\**"


def build_finder_parameters(
    *,
    hashes: Sequence[str] = (),
    filenames: Sequence[str] = (),
    yara_rule: str | None = None,
    scope_glob: str = DEFAULT_SCOPE_GLOB,
) -> dict[str, str]:
    """FileFinder parameters for the supplied IOC types (combinable).

    - One --filename glob fills ``SearchFilesGlob`` (FileFinder takes a
      single glob); multiple globs go to ``SearchFilesGlobTable`` as CSV
      lines ``Glob\\n<g1>\\n<g2>`` (globs are inserted verbatim — verify
      both parameter names and the CSV quoting rules against the tenant
      artifact in Phase 3).
    - A YARA rule fills ``YaraRule``; the filesystem scope searched is
      `scope_glob` unless --filename globs already define it.
    - Hash IOCs set ``Calculate_Hash="Y"`` over `scope_glob` (parameter
      name is a Phase 3 assumption too); matching happens client-side in
      summarize_results().
    """
    if not (hashes or filenames or yara_rule):
        raise UsageError("Provide at least one IOC: --hash, --filename, and/or --yara")
    parameters: dict[str, str] = {}
    if len(filenames) == 1:
        parameters["SearchFilesGlob"] = filenames[0]
    elif filenames:
        parameters["SearchFilesGlobTable"] = "Glob\n" + "\n".join(filenames)
    else:
        parameters["SearchFilesGlob"] = scope_glob
    if yara_rule:
        parameters["YaraRule"] = yara_rule
    if hashes:
        parameters["Calculate_Hash"] = "Y"
    return parameters


def launch_ioc_hunt(
    transport: VRTransport,
    *,
    hashes: Sequence[str] = (),
    filenames: Sequence[str] = (),
    yara_rule: str | None = None,
    scope_glob: str = DEFAULT_SCOPE_GLOB,
    artifact: str = DEFAULT_ARTIFACT,
    labels: Sequence[str] = (),
    excluded_labels: Sequence[str] = (),
    os: str | None = None,
    client_limit: int | None = None,
    expires_in: int | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
) -> str | dict:
    """POST /hunts (spec: createHunt) with IOC FileFinder parameters.

    Returns the new hunt ID, or the request-description dict when the
    transport is in dry-run mode. Raises UsageError if no IOC was given.
    """
    parameters = build_finder_parameters(
        hashes=hashes, filenames=filenames, yara_rule=yara_rule, scope_glob=scope_glob
    )
    start_request = {
        "artifacts": [artifact],
        "specs": [
            {
                "artifact": artifact,
                "parameters": {"env": [{"key": k, "value": v} for k, v in parameters.items()]},
            }
        ],
    }
    body = hunts_api.build_hunt_body(
        start_request,
        labels=labels,
        excluded_labels=excluded_labels,
        os=os,
        client_limit=client_limit,
        expires_in=expires_in,
        description=description or _default_description(hashes, filenames, yara_rule),
        tags=tags,
    )
    return hunts_api.create_hunt(transport, body)


def wait_and_summarize(
    transport: VRTransport,
    hunt_id: str,
    *,
    hashes: Sequence[str] = (),
    timeout: int,
    poll_interval: int,
    page_budget: int = DEFAULT_PAGE_BUDGET,
) -> dict:
    """Block until the hunt completes, then build the per-client hit summary.

    Completion uses the heuristic in ops._wait (spec: getHunt); rows come
    from every result page (spec: getHuntResults) and the error count from
    getHuntErrors. Raises WaitTimeout (exit 5) if the hunt is still running
    at the deadline.
    """
    hunt = wait_for_hunt(transport, hunt_id, timeout=timeout, poll_interval=poll_interval)
    rows = list(hunts_api.iter_hunt_results(transport, hunt_id, page_budget=page_budget))
    errors = hunts_api.get_hunt_errors(transport, hunt_id)
    stats = hunt.get("stats") if isinstance(hunt, dict) else None
    return summarize_results(hunt_id, rows, errors, hashes=hashes, stats=stats)


def summarize_results(
    hunt_id: str,
    rows: Sequence[Any],
    errors: Any,
    *,
    hashes: Sequence[str] = (),
    stats: dict | None = None,
) -> dict:
    """Group result rows into a per-client hit summary.

    With hash IOCs, only rows whose SHA-256 matches one of `hashes`
    (case-insensitively) count as hits; rows without a recognizable hash
    are dropped. Without hashes every row is a hit.
    """
    wanted = {h.strip().lower() for h in hashes if h and h.strip()}
    hits: dict[str, int] = {}
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if wanted:
            sha = _row_sha256(row)
            if sha is None or sha.lower() not in wanted:
                continue
        client = _row_client_id(row) or "unknown"
        hits[client] = hits.get(client, 0) + 1
        total += 1
    if isinstance(errors, dict):  # defensive: tolerate a paged envelope
        errors = errors.get("data") or []
    return {
        "hunt_id": hunt_id,
        "complete": True,
        "clients_with_hits": hits,
        "total_hits": total,
        "clients_with_errors": len(errors or []),
        "stats": stats or {},
    }


def _default_description(
    hashes: Sequence[str], filenames: Sequence[str], yara_rule: str | None
) -> str:
    parts = []
    if hashes:
        parts.append(f"{len(hashes)} SHA-256 hash(es)")
    if filenames:
        parts.append(f"{len(filenames)} filename glob(s)")
    if yara_rule:
        parts.append("a YARA rule")
    return "vr ops ioc-hunt: sweep for " + ", ".join(parts)


def _row_client_id(row: dict) -> str | None:
    """Defensive: hunt result rows have carried several client-ID spellings."""
    for key in ("ClientId", "client_id", "_ClientId"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _row_sha256(row: dict) -> str | None:
    """Defensive: FileFinder hash output appears as Hash.SHA256 (nested),
    SHA256, Sha256, or a bare Hash string depending on artifact version."""
    hash_field = row.get("Hash")
    if isinstance(hash_field, dict):
        value = hash_field.get("SHA256")
        if value:
            return str(value)
    for key in ("SHA256", "Sha256"):
        value = row.get(key)
        if value:
            return str(value)
    if isinstance(hash_field, str) and hash_field:
        return hash_field
    return None
