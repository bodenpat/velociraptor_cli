"""`vr artifacts`: enum mapping, YAML in/out, validation, --yes, --dry-run.

Happy paths and click-level usage errors go through the `invoke` fixture
(result.exit_code); vrcli-error paths (require_yes, local YAML validation)
go through main() like test_cli_main.py, because the exit-code/error-JSON
contract lives there.
"""

from __future__ import annotations

import json

import pytest

from vrcli.cli.main import main

from .conftest import ENV
from .test_api_artifacts import ARTIFACT_NAME, ARTIFACT_YAML, WEIRD_NAME, WEIRD_NAME_ENCODED


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def artifact_file(tmp_path):
    path = tmp_path / "artifact.yaml"
    path.write_text(ARTIFACT_YAML)
    return str(path)


# -- list --------------------------------------------------------------------


def test_list_maps_enums_and_flags(invoke, mock_api):
    listing = [{"name": ARTIFACT_NAME, "type": "CLIENT", "built_in": False}]
    route = mock_api.get("/artifacts").respond(200, json=listing)
    result, parsed = invoke(
        "artifacts", "list", "--type", "client", "--os", "windows", "--builtin", "--no-custom"
    )
    assert result.exit_code == 0
    assert parsed == listing
    params = route.calls.last.request.url.params
    assert params["type"] == "CLIENT"
    assert params["os"] == "WINDOWS"
    assert params["includeBuiltIn"] == "true"
    assert params["includeCustom"] == "false"


def test_list_default_omits_all_params(invoke, mock_api):
    route = mock_api.get("/artifacts").respond(200, json=[])
    result, parsed = invoke("artifacts", "list")
    assert result.exit_code == 0
    assert parsed == []
    assert route.calls.last.request.url.query == b""


def test_list_rejects_unknown_type(invoke, mock_api):
    result, _parsed = invoke("artifacts", "list", "--type", "bogus", expect_json=False)
    assert result.exit_code == 2


# -- get ---------------------------------------------------------------------


def test_get_json_emits_one_json_document(invoke, mock_api):
    definition = {"name": ARTIFACT_NAME, "sources": []}
    route = mock_api.get(f"/artifacts/{ARTIFACT_NAME}").respond(200, json=definition)
    result, parsed = invoke("artifacts", "get", ARTIFACT_NAME)
    assert result.exit_code == 0
    assert parsed == definition
    assert route.calls.last.request.headers["Accept"] == "application/json"


def test_get_yaml_outputs_raw_text(invoke, mock_api):
    route = mock_api.get(f"/artifacts/{ARTIFACT_NAME}").respond(
        200, text=ARTIFACT_YAML, headers={"Content-Type": "application/yaml"}
    )
    result, _parsed = invoke(
        "artifacts", "get", ARTIFACT_NAME, "--format", "yaml", expect_json=False
    )
    assert result.exit_code == 0
    assert result.stdout == ARTIFACT_YAML + "\n"  # raw YAML, not JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
    assert route.calls.last.request.headers["Accept"] == "application/yaml"


def test_get_url_encodes_name(invoke, mock_api):
    route = mock_api.get(f"/artifacts/{WEIRD_NAME_ENCODED}").respond(200, json={})
    result, _parsed = invoke("artifacts", "get", WEIRD_NAME)
    assert result.exit_code == 0
    assert str(route.calls.last.request.url).endswith(f"/artifacts/{WEIRD_NAME_ENCODED}")


# -- add ---------------------------------------------------------------------


def test_add_sends_raw_yaml_with_content_type(invoke, mock_api, artifact_file):
    route = mock_api.post("/artifacts").respond(204)
    result, parsed = invoke("artifacts", "add", "--file", artifact_file)
    assert result.exit_code == 0
    assert parsed == {"added": ARTIFACT_NAME}
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/yaml"
    assert request.content == ARTIFACT_YAML.encode()  # verbatim, comment included


def test_add_dry_run_shows_body_raw_and_sends_nothing(invoke, mock_api, artifact_file):
    route = mock_api.post("/artifacts").respond(204)
    result, parsed = invoke("artifacts", "add", "--file", artifact_file, "--dry-run")
    assert result.exit_code == 0
    assert route.call_count == 0
    assert parsed["dry_run"] is True
    assert parsed["method"] == "POST"
    assert parsed["url"].endswith("/artifacts")
    assert parsed["body_raw"] == ARTIFACT_YAML
    assert parsed["headers"]["Content-Type"] == "application/yaml"


def test_add_missing_file_option_exits_2(invoke, mock_api):
    result, _parsed = invoke("artifacts", "add", expect_json=False)
    assert result.exit_code == 2


def test_add_rejects_unparseable_yaml(env, capsys, tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("name: [unclosed\n")
    code, out, _err = run_main(capsys, "artifacts", "add", "--file", str(path))
    assert code == 2
    error = json.loads(out)["error"]
    assert error["type"] == "UsageError"
    assert "not valid YAML" in error["message"]


def test_add_rejects_yaml_missing_name_and_sources(env, capsys, tmp_path):
    path = tmp_path / "incomplete.yaml"
    path.write_text("description: no name or sources here\n")
    code, out, _err = run_main(capsys, "artifacts", "add", "--file", str(path))
    assert code == 2
    error = json.loads(out)["error"]
    assert error["type"] == "UsageError"
    assert "name" in error["message"] and "sources" in error["message"]


# -- update ------------------------------------------------------------------


def test_update_patches_raw_yaml(invoke, mock_api, artifact_file):
    route = mock_api.patch(f"/artifacts/{ARTIFACT_NAME}").respond(204)
    result, parsed = invoke("artifacts", "update", ARTIFACT_NAME, "--file", artifact_file)
    assert result.exit_code == 0
    assert parsed == {"updated": ARTIFACT_NAME}
    request = route.calls.last.request
    assert request.method == "PATCH"
    assert request.headers["Content-Type"] == "application/yaml"
    assert request.content == ARTIFACT_YAML.encode()


def test_update_rejects_name_mismatch(env, capsys, artifact_file):
    code, out, _err = run_main(
        capsys, "artifacts", "update", "Custom.Other.Name", "--file", artifact_file
    )
    assert code == 2
    error = json.loads(out)["error"]
    assert error["type"] == "UsageError"
    assert "mismatch" in error["message"]
    assert ARTIFACT_NAME in error["message"]


def test_update_dry_run_sends_nothing(invoke, mock_api, artifact_file):
    route = mock_api.patch(f"/artifacts/{ARTIFACT_NAME}").respond(204)
    result, parsed = invoke(
        "artifacts", "update", ARTIFACT_NAME, "--file", artifact_file, "--dry-run"
    )
    assert result.exit_code == 0
    assert route.call_count == 0
    assert parsed["dry_run"] is True
    assert parsed["method"] == "PATCH"
    assert parsed["body_raw"] == ARTIFACT_YAML


# -- delete ------------------------------------------------------------------


def test_delete_requires_yes(env, capsys, mock_api):
    route = mock_api.delete(f"/artifacts/{ARTIFACT_NAME}").respond(204)
    code, out, _err = run_main(capsys, "artifacts", "delete", ARTIFACT_NAME)
    assert code == 2
    error = json.loads(out)["error"]
    assert error["type"] == "UsageError"
    assert "--yes" in error["message"]
    assert route.call_count == 0


def test_delete_with_yes(invoke, mock_api):
    route = mock_api.delete(f"/artifacts/{WEIRD_NAME_ENCODED}").respond(204)
    result, parsed = invoke("artifacts", "delete", WEIRD_NAME, "--yes")
    assert result.exit_code == 0
    assert parsed == {"deleted": WEIRD_NAME}
    request = route.calls.last.request
    assert request.method == "DELETE"
    assert str(request.url).endswith(f"/artifacts/{WEIRD_NAME_ENCODED}")


def test_delete_dry_run(invoke, mock_api):
    route = mock_api.delete(f"/artifacts/{ARTIFACT_NAME}").respond(204)
    result, parsed = invoke("artifacts", "delete", ARTIFACT_NAME, "--yes", "--dry-run")
    assert result.exit_code == 0
    assert route.call_count == 0
    assert parsed["dry_run"] is True
    assert parsed["method"] == "DELETE"
