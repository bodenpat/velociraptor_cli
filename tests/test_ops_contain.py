"""`vr ops contain` / `vr ops release`: quarantine guardrails, marker labels, dry-run, audit."""

from __future__ import annotations

import json

import pytest

from vrcli.cli.main import main

from .conftest import ENV, page

CLIENT_ID = "C.cccccccccccccccc"
LABELS_PATH = f"/clients/{CLIENT_ID}/labels"
FLOWS_PATH = f"/clients/{CLIENT_ID}/flows"
FLOW_ID = "F.QQQQ1111"
QUARANTINE_ARTIFACT = "Windows.Remediation.Quarantine"
UNQUARANTINE_BODY = {
    "artifacts": [QUARANTINE_ARTIFACT],
    "specs": [
        {
            "artifact": QUARANTINE_ARTIFACT,
            "parameters": {"env": [{"key": "RemovePolicy", "value": "Y"}]},
        }
    ],
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Never inherit deny labels or a central audit log from the dev machine."""
    monkeypatch.delenv("R7_VR_QUARANTINE_DENY_LABELS", raising=False)
    monkeypatch.delenv("R7_VR_AUDIT_LOG", raising=False)


@pytest.fixture
def env(monkeypatch):
    """Process env for tests that go through main() (exit-code contract)."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def mutating_routes(mock_api):
    """Catch-all routes for every request contain/release could mutate with."""
    return (
        mock_api.patch(path__regex=r".*/labels/.*"),
        mock_api.post(path__regex=r".*/flows$"),
        mock_api.delete(path__regex=r".*/labels/.*"),
    )


# -- quarantine guardrail (PLAN.md §9.7) ---------------------------------------


def test_quarantine_blocked_by_builtin_deny_label(env, mock_api, capsys):
    labels_route = mock_api.get(LABELS_PATH).respond(200, json=["critical-infrastructure"])
    patch_route, post_route, delete_route = mutating_routes(mock_api)
    code, out, _err = run_main(capsys, "ops", "contain", CLIENT_ID, "--quarantine")
    assert code == 2
    doc = json.loads(out)
    assert doc["error"]["type"] == "UsageError"
    assert "critical-infrastructure" in doc["error"]["message"]
    assert labels_route.call_count == 1  # checked client-side, from real labels
    assert patch_route.call_count == 0  # nothing was mutated
    assert post_route.call_count == 0
    assert delete_route.call_count == 0


def test_quarantine_blocked_by_env_deny_label(env, mock_api, capsys, monkeypatch):
    monkeypatch.setenv("R7_VR_QUARANTINE_DENY_LABELS", "domain-controller, payroll")
    mock_api.get(LABELS_PATH).respond(200, json=["payroll"])
    patch_route, post_route, delete_route = mutating_routes(mock_api)
    code, out, _err = run_main(capsys, "ops", "contain", CLIENT_ID, "--quarantine")
    assert code == 2
    assert "payroll" in json.loads(out)["error"]["message"]
    assert patch_route.call_count == 0
    assert post_route.call_count == 0
    assert delete_route.call_count == 0


def test_quarantine_blocked_by_deny_label_flag(env, mock_api, capsys):
    mock_api.get(LABELS_PATH).respond(200, json=["finance-server"])
    patch_route, post_route, delete_route = mutating_routes(mock_api)
    code, out, _err = run_main(
        capsys, "ops", "contain", CLIENT_ID, "--quarantine", "--deny-label", "finance-server"
    )
    assert code == 2
    assert "finance-server" in json.loads(out)["error"]["message"]
    assert patch_route.call_count == 0
    assert post_route.call_count == 0
    assert delete_route.call_count == 0


def test_quarantine_deny_match_is_case_insensitive(env, mock_api, capsys):
    mock_api.get(LABELS_PATH).respond(200, json=["Critical-Infrastructure"])
    patch_route, post_route, _ = mutating_routes(mock_api)
    code, out, _err = run_main(capsys, "ops", "contain", CLIENT_ID, "--quarantine")
    assert code == 2
    assert "Critical-Infrastructure" in json.loads(out)["error"]["message"]
    assert patch_route.call_count == 0
    assert post_route.call_count == 0


def test_contain_dry_run_still_enforces_guardrail(env, mock_api, capsys):
    mock_api.get(LABELS_PATH).respond(200, json=["no-quarantine"])
    code, out, _err = run_main(
        capsys, "ops", "contain", CLIENT_ID, "--quarantine", "--dry-run"
    )
    assert code == 2
    assert "no-quarantine" in json.loads(out)["error"]["message"]
    assert len(mock_api.calls) == 1  # only the labels GET


def test_plain_contain_is_not_blocked_by_deny_labels(invoke, mock_api):
    mock_api.get(LABELS_PATH).respond(200, json=["critical-infrastructure"])
    add_route = mock_api.patch(f"{LABELS_PATH}/under-investigation").respond(200)
    flow_route = mock_api.post(FLOWS_PATH)
    result, doc = invoke("ops", "contain", CLIENT_ID)
    assert result.exit_code == 0
    assert doc == {
        "client_id": CLIENT_ID,
        "host": CLIENT_ID,
        "labels_added": ["under-investigation"],
        "quarantine": None,
    }
    assert add_route.call_count == 1
    assert flow_route.call_count == 0


# -- contain -------------------------------------------------------------------


def test_contain_quarantine_creates_flow_and_marker(invoke, mock_api, monkeypatch, tmp_path):
    audit_log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(audit_log))
    mock_api.get(LABELS_PATH).respond(200, json=[])
    add_label = mock_api.patch(f"{LABELS_PATH}/under-investigation").respond(200)
    create_flow = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    get_flow = mock_api.get(f"{FLOWS_PATH}/{FLOW_ID}").respond(200, json={"state": "RUNNING"})
    add_marker = mock_api.patch(f"{LABELS_PATH}/vr-quarantined").respond(200)

    result, doc = invoke("ops", "contain", CLIENT_ID, "--quarantine")
    assert result.exit_code == 0
    assert doc == {
        "client_id": CLIENT_ID,
        "host": CLIENT_ID,
        "labels_added": ["under-investigation", "vr-quarantined"],
        "quarantine": {"flow_id": FLOW_ID, "state": "RUNNING"},
    }
    assert add_label.call_count == 1
    assert get_flow.call_count == 1  # verified the flow scheduled
    assert add_marker.call_count == 1
    assert create_flow.calls.last.request.method == "POST"
    assert json.loads(create_flow.calls.last.request.content) == {
        "artifacts": [QUARANTINE_ARTIFACT],
        "specs": [],
    }
    record = json.loads(audit_log.read_text().splitlines()[-1])
    assert record["command"] == "ops contain"
    assert record["created"] == {"client_id": CLIENT_ID, "quarantine_flow_id": FLOW_ID}


def test_contain_resolves_hostname(invoke, mock_api):
    mock_api.get("/clients", params={"hostname": "ws01"}).respond(
        200, json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws01"}}])
    )
    mock_api.get(LABELS_PATH).respond(200, json=[])
    add_route = mock_api.patch(f"{LABELS_PATH}/under-investigation").respond(200)
    result, doc = invoke("ops", "contain", "ws01")
    assert result.exit_code == 0
    assert doc["client_id"] == CLIENT_ID
    assert doc["host"] == "ws01"
    assert add_route.call_count == 1


def test_contain_custom_label_is_url_encoded(invoke, mock_api):
    mock_api.get(LABELS_PATH).respond(200, json=[])
    route = mock_api.patch(f"{LABELS_PATH}/ir%20case").respond(200)
    result, doc = invoke("ops", "contain", CLIENT_ID, "--label", "ir case")
    assert result.exit_code == 0
    assert doc["labels_added"] == ["ir case"]
    assert route.calls.last.request.url.raw_path.endswith(b"/labels/ir%20case")


def test_contain_quarantine_dry_run_sends_nothing(invoke, mock_api):
    labels_route = mock_api.get(LABELS_PATH).respond(200, json=[])
    patch_route, post_route, delete_route = mutating_routes(mock_api)
    result, doc = invoke("ops", "contain", CLIENT_ID, "--quarantine", "--dry-run")
    assert result.exit_code == 0
    assert doc["dry_run"] is True
    assert labels_route.call_count == 1  # guardrail still reads real labels
    assert patch_route.call_count == 0
    assert post_route.call_count == 0
    assert delete_route.call_count == 0
    assert [r["method"] for r in doc["requests"]] == ["PATCH", "POST", "PATCH"]
    assert doc["requests"][0]["url"].endswith(f"{LABELS_PATH}/under-investigation")
    assert doc["requests"][1]["url"].endswith(FLOWS_PATH)
    assert doc["requests"][1]["body"] == {"artifacts": [QUARANTINE_ARTIFACT], "specs": []}
    assert doc["requests"][2]["url"].endswith(f"{LABELS_PATH}/vr-quarantined")


def test_contain_dry_run_audit_is_marked(invoke, mock_api, monkeypatch, tmp_path):
    audit_log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(audit_log))
    mock_api.get(LABELS_PATH).respond(200, json=[])
    result, doc = invoke("ops", "contain", CLIENT_ID, "--dry-run")
    assert result.exit_code == 0
    assert doc["dry_run"] is True
    record = json.loads(audit_log.read_text().splitlines()[-1])
    assert record["command"] == "ops contain"
    assert record["dry_run"] is True
    assert record["created"] == {"client_id": CLIENT_ID}


# -- release -------------------------------------------------------------------


def test_release_removes_label_without_marker_runs_no_flow(invoke, mock_api):
    mock_api.get(LABELS_PATH).respond(200, json=["under-investigation", "other"])
    remove_route = mock_api.delete(f"{LABELS_PATH}/under-investigation").respond(204)
    flow_route = mock_api.post(FLOWS_PATH)
    result, doc = invoke("ops", "release", CLIENT_ID)
    assert result.exit_code == 0
    assert doc == {
        "client_id": CLIENT_ID,
        "host": CLIENT_ID,
        "labels_removed": ["under-investigation"],
        "unquarantine": None,
    }
    assert remove_route.calls.last.request.method == "DELETE"
    assert remove_route.call_count == 1
    assert flow_route.call_count == 0  # RemovePolicy flow only when marker present


def test_release_unquarantines_when_marker_present(invoke, mock_api, monkeypatch, tmp_path):
    audit_log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(audit_log))
    mock_api.get(LABELS_PATH).respond(200, json=["under-investigation", "vr-quarantined"])
    remove_label = mock_api.delete(f"{LABELS_PATH}/under-investigation").respond(204)
    remove_marker = mock_api.delete(f"{LABELS_PATH}/vr-quarantined").respond(204)
    create_flow = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)

    result, doc = invoke("ops", "release", CLIENT_ID)
    assert result.exit_code == 0
    assert doc == {
        "client_id": CLIENT_ID,
        "host": CLIENT_ID,
        "labels_removed": ["under-investigation", "vr-quarantined"],
        "unquarantine": {"flow_id": FLOW_ID},
    }
    assert remove_label.call_count == 1
    assert remove_marker.call_count == 1
    assert json.loads(create_flow.calls.last.request.content) == UNQUARANTINE_BODY
    record = json.loads(audit_log.read_text().splitlines()[-1])
    assert record["command"] == "ops release"
    assert record["created"] == {"client_id": CLIENT_ID, "unquarantine_flow_id": FLOW_ID}


def test_release_with_no_matching_labels_is_noop_success(invoke, mock_api):
    mock_api.get(LABELS_PATH).respond(200, json=["unrelated"])
    patch_route, post_route, delete_route = mutating_routes(mock_api)
    result, doc = invoke("ops", "release", CLIENT_ID)
    assert result.exit_code == 0
    assert doc["labels_removed"] == []
    assert doc["unquarantine"] is None
    assert patch_route.call_count == 0
    assert post_route.call_count == 0
    assert delete_route.call_count == 0


def test_release_matches_label_case_insensitively(invoke, mock_api):
    mock_api.get(LABELS_PATH).respond(200, json=["Under-Investigation"])
    remove_route = mock_api.delete(f"{LABELS_PATH}/Under-Investigation").respond(204)
    result, doc = invoke("ops", "release", CLIENT_ID)
    assert result.exit_code == 0
    assert doc["labels_removed"] == ["Under-Investigation"]  # actual casing removed
    assert remove_route.call_count == 1


def test_release_resolves_hostname(invoke, mock_api):
    mock_api.get("/clients", params={"hostname": "ws01"}).respond(
        200, json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws01"}}])
    )
    mock_api.get(LABELS_PATH).respond(200, json=[])
    result, doc = invoke("ops", "release", "ws01")
    assert result.exit_code == 0
    assert doc["client_id"] == CLIENT_ID
    assert doc["host"] == "ws01"


def test_release_dry_run_sends_nothing(invoke, mock_api):
    mock_api.get(LABELS_PATH).respond(200, json=["under-investigation", "vr-quarantined"])
    patch_route, post_route, delete_route = mutating_routes(mock_api)
    result, doc = invoke("ops", "release", CLIENT_ID, "--dry-run")
    assert result.exit_code == 0
    assert doc["dry_run"] is True
    assert patch_route.call_count == 0
    assert post_route.call_count == 0
    assert delete_route.call_count == 0
    assert [r["method"] for r in doc["requests"]] == ["DELETE", "POST", "DELETE"]
    assert doc["requests"][0]["url"].endswith(f"{LABELS_PATH}/under-investigation")
    assert doc["requests"][1]["body"] == UNQUARANTINE_BODY
    assert doc["requests"][2]["url"].endswith(f"{LABELS_PATH}/vr-quarantined")
