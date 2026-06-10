"""`vr ops triage`: end-to-end evidence collection, resume tokens, exit codes."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from vrcli.cli.main import main
from vrcli.errors import UsageError
from vrcli.ops.triage import (
    build_triage_args,
    decode_resume_token,
    default_out_dir,
    encode_resume_token,
)

from .conftest import ENV, page

CLIENT_ID = "C.1111111111111111"
FLOW_ID = "F.AAAA1111"
ARTIFACT = "Windows.KapeFiles.Targets"
FLOWS_PATH = f"/clients/{CLIENT_ID}/flows"
FLOW_PATH = f"{FLOWS_PATH}/{FLOW_ID}"


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


@pytest.fixture(autouse=True)
def _no_central_audit(monkeypatch):
    """Keep $R7_VR_AUDIT_LOG from the host environment out of every test."""
    monkeypatch.delenv("R7_VR_AUDIT_LOG", raising=False)


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def mock_finished_flow_output(mock_api):
    """Routes for a finished flow's evidence: results list, rows, logs."""
    available = mock_api.get(f"{FLOW_PATH}/results").respond(200, json=[ARTIFACT])
    rows = mock_api.get(f"{FLOW_PATH}/results/{ARTIFACT}").respond(
        200, json=page([{"SourceFile": "C:/Windows/foo"}, {"SourceFile": "C:/Windows/bar"}])
    )
    logs = mock_api.get(f"{FLOW_PATH}/logs").respond(200, json=page([{"message": "collected"}]))
    return available, rows, logs


# -- unit: collector args, default out dir, token ----------------------------


def test_build_triage_args_kape_checkbox_is_Y():
    assert build_triage_args() == {
        "artifacts": [ARTIFACT],
        "specs": [
            {"artifact": ARTIFACT, "parameters": {"env": [{"key": "KapeTriage", "value": "Y"}]}}
        ],
    }


def test_build_triage_args_options():
    body = build_triage_args("Custom.Triage", "BasicCollection", max_upload_bytes=42, urgent=True)
    assert body["artifacts"] == ["Custom.Triage"]
    assert body["specs"][0]["parameters"]["env"] == [{"key": "BasicCollection", "value": "Y"}]
    assert body["max_upload_bytes"] == 42
    assert body["urgent"] is True


def test_default_out_dir():
    assert default_out_dir(CLIENT_ID, FLOW_ID) == f"evidence/triage-{CLIENT_ID}-{FLOW_ID}"


def test_resume_token_round_trip():
    token = encode_resume_token(
        client_id=CLIENT_ID, flow_id=FLOW_ID, out="evidence/x", host="ws-alpha"
    )
    assert decode_resume_token(token) == {
        "v": 1,
        "client_id": CLIENT_ID,
        "flow_id": FLOW_ID,
        "out": "evidence/x",
        "host": "ws-alpha",
    }


@pytest.mark.parametrize("garbage", ["", "not-a-token!!", "Zm9v", "eyJ2IjogOTl9"])
def test_resume_token_garbage_raises_usage_error(garbage):
    with pytest.raises(UsageError, match="resume token"):
        decode_resume_token(garbage)


# -- --wait: happy path end-to-end --------------------------------------------


def test_wait_collects_evidence_end_to_end(invoke, mock_api, monkeypatch, tmp_path):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    create = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    flow_route = mock_api.get(FLOW_PATH)
    flow_route.side_effect = [
        httpx.Response(200, json={"state": "RUNNING"}),
        httpx.Response(200, json={"state": "FINISHED"}),
        httpx.Response(200, json={"state": "FINISHED", "session_id": FLOW_ID}),
    ]
    mock_finished_flow_output(mock_api)
    out = tmp_path / "case-001"

    result, parsed = invoke(
        "ops", "triage", CLIENT_ID, "--wait", "--poll-interval", "1", "--out", str(out)
    )
    assert result.exit_code == 0
    assert parsed == {
        "done": True,
        "client_id": CLIENT_ID,
        "flow_id": FLOW_ID,
        "out": str(out),
        "artifacts": {ARTIFACT: 2},
        "log_lines": 1,
        "manifest_files": 4,
    }

    # create-flow request: method, path, exact ArtifactCollectorArgs body
    assert create.calls.last.request.method == "POST"
    assert create.calls.last.request.url.path.endswith(FLOWS_PATH)
    assert json.loads(create.calls.last.request.content) == {
        "artifacts": [ARTIFACT],
        "specs": [
            {"artifact": ARTIFACT, "parameters": {"env": [{"key": "KapeTriage", "value": "Y"}]}}
        ],
    }
    assert flow_route.call_count == 3  # poll RUNNING, poll FINISHED, evidence snapshot

    # evidence directory contents + manifest hashes — audit.jsonl is hashed
    # into the manifest too (chain-of-custody covers the audit record).
    manifest = json.loads((out / "manifest.json").read_text())
    assert {f["name"] for f in manifest["files"]} == {
        "flow.json",
        f"results/{ARTIFACT}.jsonl",
        "logs.jsonl",
        "audit.jsonl",
    }
    for entry in manifest["files"]:
        path = out / entry["name"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest["collection"] == {
        "host": CLIENT_ID,
        "client_id": CLIENT_ID,
        "flow_id": FLOW_ID,
        "artifact": ARTIFACT,
        "targets": "KapeTriage",
    }
    rows = [json.loads(line) for line in (out / f"results/{ARTIFACT}.jsonl").read_text().splitlines()]
    assert rows == [{"SourceFile": "C:/Windows/foo"}, {"SourceFile": "C:/Windows/bar"}]

    # audit JSONL in the evidence dir records the created flow
    (audit_line,) = (out / "audit.jsonl").read_text().splitlines()
    audit = json.loads(audit_line)
    assert audit["command"] == "ops triage"
    assert audit["created"] == {"client_id": CLIENT_ID, "flow_id": FLOW_ID}


def test_wait_custom_targets_and_limits(invoke, mock_api, monkeypatch, tmp_path):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    create = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    mock_api.get(FLOW_PATH).respond(200, json={"state": "FINISHED"})
    mock_finished_flow_output(mock_api)

    result, parsed = invoke(
        "ops", "triage", CLIENT_ID, "--wait",
        "--targets", "BasicCollection",
        "--artifact", "Custom.KapeFiles.Targets",
        "--max-upload-bytes", "1073741824",
        "--urgent",
        "--out", str(tmp_path / "case"),
    )
    # Custom artifact name: the results list still advertises ARTIFACT (mocked),
    # which is fine — collection follows whatever the API reports as available.
    assert result.exit_code == 0
    assert parsed["done"] is True
    body = json.loads(create.calls.last.request.content)
    assert body["artifacts"] == ["Custom.KapeFiles.Targets"]
    assert body["specs"] == [
        {
            "artifact": "Custom.KapeFiles.Targets",
            "parameters": {"env": [{"key": "BasicCollection", "value": "Y"}]},
        }
    ]
    assert body["max_upload_bytes"] == 1073741824
    assert body["urgent"] is True


def test_wait_timeout_exits_5_and_writes_nothing(env, mock_api, capsys, monkeypatch, tmp_path):
    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    mock_api.get(FLOW_PATH).respond(200, json={"state": "RUNNING"})
    out = tmp_path / "case"

    code, stdout, _err = run_main(
        capsys, "ops", "triage", CLIENT_ID, "--wait",
        "--timeout", "30", "--poll-interval", "10", "--out", str(out),
    )
    assert code == 5
    assert json.loads(stdout)["error"]["type"] == "WaitTimeout"
    assert not out.exists()


def test_wait_error_flow_exits_6(env, mock_api, capsys, tmp_path):
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    mock_api.get(FLOW_PATH).respond(200, json={"state": "ERROR", "status": "boom"})
    out = tmp_path / "case"
    code, stdout, _err = run_main(
        capsys, "ops", "triage", CLIENT_ID, "--wait", "--out", str(out)
    )
    assert code == 6
    assert json.loads(stdout)["error"]["type"] == "APIError"
    assert not out.exists()


# -- --start: resume token -----------------------------------------------------


def test_start_returns_decodable_token(invoke, mock_api, monkeypatch, tmp_path):
    central = tmp_path / "central-audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(central))
    clients_route = mock_api.get("/clients").respond(
        200, json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws-alpha"}}])
    )
    create = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    out = tmp_path / "case"

    result, parsed = invoke("ops", "triage", "ws-alpha", "--start", "--out", str(out))
    assert result.exit_code == 0
    assert create.call_count == 1
    assert "hostname=ws-alpha" in str(clients_route.calls.last.request.url)
    assert parsed["done"] is False
    assert parsed["client_id"] == CLIENT_ID
    assert parsed["flow_id"] == FLOW_ID
    assert parsed["out"] == str(out)
    assert decode_resume_token(parsed["token"]) == {
        "v": 1,
        "client_id": CLIENT_ID,
        "flow_id": FLOW_ID,
        "out": str(out),
        "host": "ws-alpha",
    }
    # No evidence yet: audit goes to the central log only.
    assert not out.exists()
    (audit_line,) = central.read_text().splitlines()
    assert json.loads(audit_line)["created"] == {"client_id": CLIENT_ID, "flow_id": FLOW_ID}


def test_default_mode_is_start(invoke, mock_api, tmp_path):
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    result, parsed = invoke("ops", "triage", CLIENT_ID, "--out", str(tmp_path / "case"))
    assert result.exit_code == 0
    assert parsed["done"] is False
    assert decode_resume_token(parsed["token"])["flow_id"] == FLOW_ID


def test_start_default_out_dir_in_token(invoke, mock_api):
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    result, parsed = invoke("ops", "triage", CLIENT_ID, "--start")
    assert result.exit_code == 0
    assert parsed["out"] == f"evidence/triage-{CLIENT_ID}-{FLOW_ID}"
    assert decode_resume_token(parsed["token"])["out"] == parsed["out"]


# -- --check: resume ------------------------------------------------------------


def test_check_running_emits_done_false_and_writes_nothing(invoke, mock_api, tmp_path):
    out = tmp_path / "case"
    token = encode_resume_token(
        client_id=CLIENT_ID, flow_id=FLOW_ID, out=str(out), host="ws-alpha"
    )
    flow_route = mock_api.get(FLOW_PATH).respond(200, json={"state": "RUNNING"})

    result, parsed = invoke("ops", "triage", "--check", token)
    assert result.exit_code == 0
    assert flow_route.call_count == 1
    assert parsed == {"done": False, "state": "RUNNING", "token": token}
    assert not out.exists()


def test_check_finished_writes_evidence_like_wait(invoke, mock_api, tmp_path):
    out = tmp_path / "case"
    token = encode_resume_token(
        client_id=CLIENT_ID, flow_id=FLOW_ID, out=str(out), host="ws-alpha"
    )
    mock_api.get(FLOW_PATH).respond(200, json={"state": "FINISHED"})
    mock_finished_flow_output(mock_api)

    result, parsed = invoke("ops", "triage", "--check", token)
    assert result.exit_code == 0
    assert parsed == {
        "done": True,
        "client_id": CLIENT_ID,
        "flow_id": FLOW_ID,
        "out": str(out),
        "artifacts": {ARTIFACT: 2},
        "log_lines": 1,
        "manifest_files": 4,
    }
    manifest = json.loads((out / "manifest.json").read_text())
    assert {f["name"] for f in manifest["files"]} == {
        "flow.json",
        f"results/{ARTIFACT}.jsonl",
        "logs.jsonl",
        "audit.jsonl",
    }
    for entry in manifest["files"]:
        assert entry["sha256"] == hashlib.sha256((out / entry["name"]).read_bytes()).hexdigest()
    assert manifest["collection"]["resumed_from_token"] is True
    audit = json.loads((out / "audit.jsonl").read_text().splitlines()[0])
    assert audit["created"] == {"client_id": CLIENT_ID, "flow_id": FLOW_ID}


def test_check_error_flow_exits_6(env, mock_api, capsys, tmp_path):
    out = tmp_path / "case"
    token = encode_resume_token(
        client_id=CLIENT_ID, flow_id=FLOW_ID, out=str(out), host="ws-alpha"
    )
    mock_api.get(FLOW_PATH).respond(200, json={"state": "ERROR", "status": "boom"})
    code, stdout, _err = run_main(capsys, "ops", "triage", "--check", token)
    assert code == 6
    assert json.loads(stdout)["error"]["type"] == "APIError"
    assert not out.exists()


def test_check_garbage_token_exits_2(env, capsys):
    code, stdout, _err = run_main(capsys, "ops", "triage", "--check", "garbage!!")
    assert code == 2
    doc = json.loads(stdout)
    assert doc["error"]["type"] == "UsageError"
    assert "resume token" in doc["error"]["message"]


# -- --dry-run -------------------------------------------------------------------


def test_dry_run_sends_nothing_and_creates_nothing(invoke, mock_api, tmp_path):
    create = mock_api.post(FLOWS_PATH)
    out = tmp_path / "case"
    result, parsed = invoke("ops", "triage", CLIENT_ID, "--dry-run", "--out", str(out))
    assert result.exit_code == 0
    assert create.call_count == 0
    assert parsed["dry_run"] is True
    assert parsed["method"] == "POST"
    assert parsed["url"].endswith(FLOWS_PATH)
    assert parsed["body"] == {
        "artifacts": [ARTIFACT],
        "specs": [
            {"artifact": ARTIFACT, "parameters": {"env": [{"key": "KapeTriage", "value": "Y"}]}}
        ],
    }
    assert not out.exists()


def test_dry_run_wait_also_sends_nothing(invoke, mock_api, tmp_path):
    create = mock_api.post(FLOWS_PATH)
    result, parsed = invoke(
        "ops", "triage", CLIENT_ID, "--wait", "--dry-run", "--out", str(tmp_path / "case")
    )
    assert result.exit_code == 0
    assert create.call_count == 0
    assert parsed["dry_run"] is True


# -- usage validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("--wait", "--start"),
        ("--wait", "--check", "t"),
        ("--start", "--check", "t"),
    ],
)
def test_modes_mutually_exclusive(env, capsys, args):
    code, stdout, _err = run_main(capsys, "ops", "triage", CLIENT_ID, *args)
    assert code == 2
    assert "mutually exclusive" in json.loads(stdout)["error"]["message"]


def test_check_with_host_exits_2(env, capsys):
    token = encode_resume_token(client_id=CLIENT_ID, flow_id=FLOW_ID, out="x", host="h")
    code, stdout, _err = run_main(capsys, "ops", "triage", CLIENT_ID, "--check", token)
    assert code == 2
    assert "do not pass HOST" in json.loads(stdout)["error"]["message"]


def test_missing_host_exits_2(env, capsys):
    code, stdout, _err = run_main(capsys, "ops", "triage")
    assert code == 2
    assert "HOST" in json.loads(stdout)["error"]["message"]
