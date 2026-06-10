"""scripts/check_spec_drift.py: pure comparison functions + entry point.

The script is standalone (not part of the vrcli package), so it is loaded via
importlib straight from scripts/. No test here performs network I/O: main()
is exercised only with download_spec monkeypatched out.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import httpx
import pytest
import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_spec_drift.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_spec_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift_mod = _load_script()

BASE_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Hosted Velociraptor REST API", "version": "v0"},
    "paths": {
        "/clients": {
            "summary": "not an operation",  # path-item keys that are not methods are ignored
            "get": {"operationId": "getClients"},
        },
        "/hunts/{huntId}": {
            "get": {"operationId": "getHunt"},
            "patch": {"operationId": "updateHuntStatus"},
        },
    },
}
BASE_TEXT = yaml.safe_dump(BASE_SPEC, sort_keys=True)


# -- extract_operations ------------------------------------------------------


def test_extract_operations_builds_method_path_operationid_triples():
    ops = drift_mod.extract_operations(BASE_SPEC)
    assert ops == {
        ("GET", "/clients", "getClients"),
        ("GET", "/hunts/{huntId}", "getHunt"),
        ("PATCH", "/hunts/{huntId}", "updateHuntStatus"),
    }


def test_extract_operations_tolerates_junk_documents():
    assert drift_mod.extract_operations({}) == set()
    assert drift_mod.extract_operations({"paths": None}) == set()
    assert drift_mod.extract_operations({"paths": {"/x": "not-a-mapping"}}) == set()


# -- diff_operations ---------------------------------------------------------


def test_identical_specs_have_no_operation_drift():
    added, removed = drift_mod.diff_operations(BASE_SPEC, copy.deepcopy(BASE_SPEC))
    assert added == []
    assert removed == []


def test_added_path_is_reported_as_added_operation():
    published = copy.deepcopy(BASE_SPEC)
    # The endpoint we are watching for (PLAN.md §2 known gap): file download.
    published["paths"]["/clients/{clientId}/flows/{flowId}/download"] = {
        "get": {"operationId": "downloadFlowFile"}
    }
    added, removed = drift_mod.diff_operations(BASE_SPEC, published)
    assert added == [("GET", "/clients/{clientId}/flows/{flowId}/download", "downloadFlowFile")]
    assert removed == []


def test_removed_method_is_reported_as_removed_operation():
    published = copy.deepcopy(BASE_SPEC)
    del published["paths"]["/hunts/{huntId}"]["patch"]
    added, removed = drift_mod.diff_operations(BASE_SPEC, published)
    assert added == []
    assert removed == [("PATCH", "/hunts/{huntId}", "updateHuntStatus")]


# -- text_diff ---------------------------------------------------------------


def test_text_diff_empty_for_identical_text():
    assert drift_mod.text_diff(BASE_TEXT, BASE_TEXT) == ""


def test_text_diff_is_a_unified_diff_for_changed_text():
    changed = BASE_TEXT.replace("getClients", "listClients")
    diff = drift_mod.text_diff(BASE_TEXT, changed)
    assert "--- spec/velociraptor-api-v1.yaml (vendored)" in diff
    assert "+++ " in diff
    assert "+      operationId: listClients" in diff
    assert "-      operationId: getClients" in diff


# -- check_drift (report layer) ----------------------------------------------


def test_check_drift_identical_specs_report_no_drift():
    report, drift = drift_mod.check_drift(BASE_TEXT, BASE_TEXT)
    assert drift is False
    assert "No drift" in report
    assert "3 operations" in report


def test_check_drift_reports_added_operation():
    published = copy.deepcopy(BASE_SPEC)
    published["paths"]["/flows/download"] = {"get": {"operationId": "downloadFlowFile"}}
    report, drift = drift_mod.check_drift(BASE_TEXT, yaml.safe_dump(published, sort_keys=True))
    assert drift is True
    assert "DRIFT DETECTED" in report
    assert "Added operations (1)" in report
    assert "+ GET    /flows/download  (downloadFlowFile)" in report
    assert "Removed operations" not in report


def test_check_drift_reports_removed_method():
    published = copy.deepcopy(BASE_SPEC)
    del published["paths"]["/hunts/{huntId}"]["patch"]
    report, drift = drift_mod.check_drift(BASE_TEXT, yaml.safe_dump(published, sort_keys=True))
    assert drift is True
    assert "Removed operations (1)" in report
    assert "- PATCH  /hunts/{huntId}  (updateHuntStatus)" in report
    assert "Added operations" not in report


def test_check_drift_schema_only_change_notes_unchanged_inventory():
    changed = BASE_TEXT.replace("version: v0", "version: v1")
    report, drift = drift_mod.check_drift(BASE_TEXT, changed)
    assert drift is True
    assert "Operation inventory unchanged" in report
    assert "Re-run with --verbose" in report


def test_check_drift_verbose_includes_unified_diff():
    changed = BASE_TEXT.replace("version: v0", "version: v1")
    report, _drift = drift_mod.check_drift(BASE_TEXT, changed, verbose=True)
    assert "Unified diff" in report
    assert "-  version: v0" in report
    assert "+  version: v1" in report


# -- main() entry point (download_spec always monkeypatched: no network) ------


@pytest.fixture
def vendored(monkeypatch, tmp_path):
    """Point the script at a temp vendored spec; returns its Path."""
    path = tmp_path / "velociraptor-api-v1.yaml"
    path.write_text(BASE_TEXT, encoding="utf-8")
    monkeypatch.setattr(drift_mod, "VENDORED_SPEC", path)
    monkeypatch.setattr(drift_mod, "REPO_ROOT", tmp_path)
    return path


def test_main_no_drift_exits_0(vendored, monkeypatch, capsys):
    monkeypatch.setattr(drift_mod, "download_spec", lambda *a, **kw: BASE_TEXT)
    assert drift_mod.main([]) == 0
    assert "No drift" in capsys.readouterr().out


def test_main_drift_exits_1(vendored, monkeypatch, capsys):
    monkeypatch.setattr(
        drift_mod, "download_spec", lambda *a, **kw: BASE_TEXT.replace("v0", "v1")
    )
    assert drift_mod.main([]) == 1
    assert "DRIFT DETECTED" in capsys.readouterr().out


def test_main_network_failure_exits_7(vendored, monkeypatch, capsys):
    def boom(*a, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(drift_mod, "download_spec", boom)
    assert drift_mod.main([]) == 7
    captured = capsys.readouterr()
    assert "could not download" in captured.err


def test_main_missing_vendored_spec_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(drift_mod, "VENDORED_SPEC", tmp_path / "missing.yaml")
    monkeypatch.setattr(
        drift_mod, "download_spec", lambda *a, **kw: pytest.fail("must not download")
    )
    assert drift_mod.main([]) == 2
    assert "not found" in capsys.readouterr().err


def test_main_update_overwrites_vendored_copy_and_still_exits_1(vendored, monkeypatch, capsys):
    published = BASE_TEXT.replace("version: v0", "version: v1")
    monkeypatch.setattr(drift_mod, "download_spec", lambda *a, **kw: published)
    assert drift_mod.main(["--update"]) == 1
    assert vendored.read_text(encoding="utf-8") == published
    assert "Updated" in capsys.readouterr().out


def test_main_update_without_drift_leaves_file_alone(vendored, monkeypatch, capsys):
    before_mtime = vendored.stat().st_mtime_ns
    monkeypatch.setattr(drift_mod, "download_spec", lambda *a, **kw: BASE_TEXT)
    assert drift_mod.main(["--update"]) == 0
    assert vendored.stat().st_mtime_ns == before_mtime
    assert "Nothing to update" in capsys.readouterr().out


def test_main_verbose_prints_unified_diff(vendored, monkeypatch, capsys):
    monkeypatch.setattr(
        drift_mod, "download_spec", lambda *a, **kw: BASE_TEXT.replace("v0", "v1")
    )
    assert drift_mod.main(["--verbose"]) == 1
    out = capsys.readouterr().out
    assert "Unified diff" in out
    assert "+  version: v1" in out


def test_script_never_reads_r7_vr_env():
    """The published spec is public; the script must not touch tenant secrets."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "os.environ" not in text
    assert "getenv" not in text
    assert "R7_VR_API_KEY" not in text
    assert "X-Api-Key" not in text
