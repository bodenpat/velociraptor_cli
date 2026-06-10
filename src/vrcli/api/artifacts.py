"""Artifact API bindings (spec: listArtifacts, getArtifact, addArtifact,
modifyArtifact, deleteArtifact)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..transport import VRTransport

YAML_CONTENT_TYPE = "application/yaml"


def list_artifacts(
    transport: VRTransport,
    *,
    type: str | None = None,  # noqa: A002 - mirrors the spec parameter name
    os: str | None = None,
    include_builtin: bool | None = None,
    include_custom: bool | None = None,
) -> Any:
    """GET /artifacts (spec: listArtifacts) — returns a bare array of ArtifactListing.

    Booleans serialize as true/false; None filters are omitted entirely so
    the server defaults (ALL / include everything) apply.
    """
    return transport.request(
        "GET",
        "/artifacts",
        params={
            "type": type.upper() if type else None,
            "os": os.upper() if os else None,
            "includeBuiltIn": include_builtin,
            "includeCustom": include_custom,
        },
    )


def get_artifact(transport: VRTransport, name: str, *, format: str = "json") -> Any:  # noqa: A002
    """GET /artifacts/{name} (spec: getArtifact).

    format="json" (default) returns the parsed Artifact object; format="yaml"
    sends `Accept: application/yaml` and returns the definition as raw YAML
    text (the transport passes non-JSON bodies through as str).
    """
    headers = {"Accept": YAML_CONTENT_TYPE} if format == "yaml" else None
    return transport.request("GET", f"/artifacts/{quote(name, safe='')}", headers=headers)


def add_artifact(transport: VRTransport, yaml_text: str) -> Any:
    """POST /artifacts (spec: addArtifact) — raw YAML body; 204 (None) on success."""
    return transport.request(
        "POST",
        "/artifacts",
        content=yaml_text,
        headers={"Content-Type": YAML_CONTENT_TYPE},
    )


def modify_artifact(transport: VRTransport, name: str, yaml_text: str) -> Any:
    """PATCH /artifacts/{name} (spec: modifyArtifact) — raw YAML body; 204 (None) on success."""
    return transport.request(
        "PATCH",
        f"/artifacts/{quote(name, safe='')}",
        content=yaml_text,
        headers={"Content-Type": YAML_CONTENT_TYPE},
    )


def delete_artifact(transport: VRTransport, name: str) -> Any:
    """DELETE /artifacts/{name} (spec: deleteArtifact) — 204 (None) on success."""
    return transport.request("DELETE", f"/artifacts/{quote(name, safe='')}")
