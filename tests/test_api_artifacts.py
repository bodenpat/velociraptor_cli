"""api/artifacts.py: request shapes for the Artifact API (spec: listArtifacts,
getArtifact, addArtifact, modifyArtifact, deleteArtifact)."""

from __future__ import annotations

from vrcli.api import artifacts as api

ARTIFACT_NAME = "Custom.Test.Artifact"
WEIRD_NAME = "Custom.My Artifact/v1"
WEIRD_NAME_ENCODED = "Custom.My%20Artifact%2Fv1"

ARTIFACT_YAML = """\
# raw text must reach the server verbatim, comments included
name: Custom.Test.Artifact
description: Test artifact
sources:
  - query: |
      SELECT * FROM info()
"""


def test_list_artifacts_no_filters_sends_no_params(transport, mock_api):
    route = mock_api.get("/artifacts").respond(200, json=[])
    result = api.list_artifacts(transport)
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path.endswith("/artifacts")
    assert request.url.query == b""
    assert result == []


def test_list_artifacts_camelcase_params_and_boolean_serialization(transport, mock_api):
    listing = [{"name": ARTIFACT_NAME, "type": "CLIENT", "built_in": False}]
    route = mock_api.get("/artifacts").respond(200, json=listing)
    result = api.list_artifacts(
        transport, type="client", os="windows", include_builtin=False, include_custom=True
    )
    params = route.calls.last.request.url.params
    assert params["type"] == "CLIENT"
    assert params["os"] == "WINDOWS"
    assert params["includeBuiltIn"] == "false"
    assert params["includeCustom"] == "true"
    assert result == listing


def test_get_artifact_json_default_accept(transport, mock_api):
    route = mock_api.get(f"/artifacts/{ARTIFACT_NAME}").respond(
        200, json={"name": ARTIFACT_NAME, "sources": []}
    )
    result = api.get_artifact(transport, ARTIFACT_NAME)
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.headers["Accept"] == "application/json"
    assert result["name"] == ARTIFACT_NAME


def test_get_artifact_yaml_accept_header_and_text_passthrough(transport, mock_api):
    route = mock_api.get(f"/artifacts/{ARTIFACT_NAME}").respond(
        200, text=ARTIFACT_YAML, headers={"Content-Type": "application/yaml"}
    )
    result = api.get_artifact(transport, ARTIFACT_NAME, format="yaml")
    assert route.calls.last.request.headers["Accept"] == "application/yaml"
    assert result == ARTIFACT_YAML  # raw text, not parsed


def test_get_artifact_url_encodes_name(transport, mock_api):
    route = mock_api.get(f"/artifacts/{WEIRD_NAME_ENCODED}").respond(200, json={})
    api.get_artifact(transport, WEIRD_NAME)
    assert str(route.calls.last.request.url).endswith(f"/artifacts/{WEIRD_NAME_ENCODED}")


def test_add_artifact_posts_raw_yaml(transport, mock_api):
    route = mock_api.post("/artifacts").respond(204)
    result = api.add_artifact(transport, ARTIFACT_YAML)
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path.endswith("/artifacts")
    assert request.headers["Content-Type"] == "application/yaml"
    assert request.content == ARTIFACT_YAML.encode()
    assert result is None  # 204


def test_modify_artifact_patches_raw_yaml_to_encoded_path(transport, mock_api):
    route = mock_api.patch(f"/artifacts/{WEIRD_NAME_ENCODED}").respond(204)
    result = api.modify_artifact(transport, WEIRD_NAME, ARTIFACT_YAML)
    request = route.calls.last.request
    assert request.method == "PATCH"
    assert str(request.url).endswith(f"/artifacts/{WEIRD_NAME_ENCODED}")
    assert request.headers["Content-Type"] == "application/yaml"
    assert request.content == ARTIFACT_YAML.encode()
    assert result is None


def test_delete_artifact_encoded_path(transport, mock_api):
    route = mock_api.delete(f"/artifacts/{WEIRD_NAME_ENCODED}").respond(204)
    result = api.delete_artifact(transport, WEIRD_NAME)
    request = route.calls.last.request
    assert request.method == "DELETE"
    assert str(request.url).endswith(f"/artifacts/{WEIRD_NAME_ENCODED}")
    assert result is None
