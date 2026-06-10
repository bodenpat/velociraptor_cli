#!/usr/bin/env python3
"""Spec-drift check: re-download the published Velociraptor OpenAPI spec and
diff it against the vendored copy (PLAN.md §6, §9.3).

Run monthly and before each release. Two comparison layers:

1. Operation inventory — sets of (METHOD, path, operationId) from both
   documents; added/removed operations are reported explicitly. This is how
   we notice Rapid7 shipping new API surface (e.g. a file-download endpoint,
   the PLAN.md §2 known gap).
2. Full-text unified diff for everything else (schemas, parameters,
   descriptions), printed with --verbose.

The published spec is a public document: it is fetched with NO auth header
and this script never reads the R7_VR_* environment variables. It is fully
standalone — it imports nothing from vrcli.

Exit codes (mirroring the vrcli convention, PLAN.md §3):
    0  no drift
    1  drift found (also with --update, so wrappers still notice)
    2  usage error (vendored spec missing/unreadable)
    7  network failure downloading the published spec
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import httpx
import yaml

SPEC_URL = "https://docs.rapid7.com/_api/velociraptor-api-v1.yaml"
REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_SPEC = REPO_ROOT / "spec" / "velociraptor-api-v1.yaml"
DOWNLOAD_TIMEOUT = 30.0  # seconds

# Exit codes duplicated from vrcli.errors on purpose: this script must run
# standalone (no vrcli import), but SOAR/cron wrappers see one convention.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_USAGE = 2
EXIT_NETWORK = 7

# The HTTP methods an OpenAPI path item may carry (path items also hold
# non-operation keys like `parameters` and `summary`, which must be ignored).
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


# -- pure comparison layer (unit-testable without network) -------------------


def extract_operations(spec: dict) -> set[tuple[str, str, str]]:
    """All (METHOD, path, operationId) triples in a parsed OpenAPI document."""
    operations: set[tuple[str, str, str]] = set()
    if not isinstance(spec, dict):
        return operations
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return operations
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict):
                operations.add((method.upper(), str(path), str(operation.get("operationId", "?"))))
    return operations


def diff_operations(
    vendored: dict, published: dict
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(added, removed) operation triples, published relative to vendored.

    added   = in the published spec but not the vendored copy (new surface)
    removed = in the vendored copy but gone from the published spec
    """
    vendored_ops = extract_operations(vendored)
    published_ops = extract_operations(published)
    return sorted(published_ops - vendored_ops), sorted(vendored_ops - published_ops)


def text_diff(vendored_text: str, published_text: str) -> str:
    """Unified diff vendored -> published; empty string when identical."""
    if vendored_text == published_text:
        return ""
    return "".join(
        difflib.unified_diff(
            vendored_text.splitlines(keepends=True),
            published_text.splitlines(keepends=True),
            fromfile="spec/velociraptor-api-v1.yaml (vendored)",
            tofile=SPEC_URL,
        )
    )


def check_drift(vendored_text: str, published_text: str, *, verbose: bool = False) -> tuple[str, bool]:
    """Compare two spec documents. Returns (human-readable report, drift_found)."""
    lines: list[str] = []
    drift = vendored_text != published_text

    vendored_doc = _load_yaml(vendored_text)
    published_doc = _load_yaml(published_text)
    inventory_ok = vendored_doc is not None and published_doc is not None
    if not inventory_ok:
        lines.append(
            "warning: could not parse one of the documents as a YAML mapping; "
            "skipping the operation-inventory comparison."
        )

    if not drift:
        count = len(extract_operations(vendored_doc)) if inventory_ok else 0
        lines.append(f"No drift: vendored spec matches the published spec ({count} operations).")
        return "\n".join(lines), False

    lines.append("DRIFT DETECTED between the vendored and published specs.")
    lines.append("")

    if inventory_ok:
        added, removed = diff_operations(vendored_doc, published_doc)
        if added:
            lines.append(f"Added operations ({len(added)}) — in the published spec, not vendored:")
            lines.extend(f"  + {method:<7s}{path}  ({op_id})" for method, path, op_id in added)
        if removed:
            lines.append(
                f"Removed operations ({len(removed)}) — vendored, gone from the published spec:"
            )
            lines.extend(f"  - {method:<7s}{path}  ({op_id})" for method, path, op_id in removed)
        if not added and not removed:
            lines.append(
                "Operation inventory unchanged (no endpoints added or removed); "
                "drift is in parameters, schemas, or descriptions."
            )
        lines.append("")

    diff = text_diff(vendored_text, published_text)
    if verbose:
        lines.append("Unified diff (vendored -> published):")
        lines.append(diff.rstrip("\n"))
    else:
        changed = sum(
            1
            for line in diff.splitlines()
            if line[:1] in "+-" and not line.startswith(("+++", "---"))
        )
        lines.append(f"Full-text diff: {changed} changed line(s). Re-run with --verbose to see it.")

    return "\n".join(lines), True


def _load_yaml(text: str) -> dict | None:
    """Parse a YAML document; None when it is not a mapping (or not YAML)."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return document if isinstance(document, dict) else None


# -- network + entry point ---------------------------------------------------


def download_spec(url: str = SPEC_URL, timeout: float = DOWNLOAD_TIMEOUT) -> str:
    """Fetch the published spec. Public document: no auth, no R7_VR_* env."""
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff the vendored OpenAPI spec against the published one (PLAN.md §6).",
        epilog=f"Compares {SPEC_URL} to spec/velociraptor-api-v1.yaml. "
        "Exit 0 = no drift, 1 = drift found, 2 = usage error, 7 = network failure.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print the full unified diff, not just the summary."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="After reporting, overwrite the vendored spec with the downloaded one "
        "(so the diff lands in git). The exit code still reports the drift.",
    )
    args = parser.parse_args(argv)

    if not VENDORED_SPEC.is_file():
        print(f"error: vendored spec not found at {VENDORED_SPEC}", file=sys.stderr)
        return EXIT_USAGE
    vendored_text = VENDORED_SPEC.read_text(encoding="utf-8")

    try:
        published_text = download_spec()
    except httpx.HTTPError as exc:
        print(
            f"error: could not download {SPEC_URL}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_NETWORK

    report, drift = check_drift(vendored_text, published_text, verbose=args.verbose)
    print(report)

    if args.update:
        if drift:
            VENDORED_SPEC.write_text(published_text, encoding="utf-8")
            print(f"\nUpdated {VENDORED_SPEC.relative_to(REPO_ROOT)}; review and commit the diff.")
        else:
            print("\nNothing to update: the vendored spec is already current.")

    return EXIT_DRIFT if drift else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
