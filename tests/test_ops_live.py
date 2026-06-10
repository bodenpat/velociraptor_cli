"""`vr ops live`: preset->artifact mapping, evidence dir, audit, dry-run, exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from vrcli.cli.main import main
from vrcli.errors import UsageError
from vrcli.ops.live_response import PRESETS, default_out_dir, live_response, preset_artifacts

from .conftest import ENV, page

CLIENT_ID = "C.2222222222222222"
FLOW_ID = "F.BBBB2222"
FLOWS_PATH = f"/clients/{CLIENT_ID}/flows"
FLOW_PATH = f"{FLOWS_PATH}/{FLOW_ID}"


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("R7_VR_AUDIT_LOG", raising=False)
    return ENV


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def mock_finished_flow(mock_api, artifacts, rows_per_artifact=1):
    """Mock create -> FINISHED flow -> available results -> rows -> logs."""
    post = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    mock_api.get(FLOW_PATH).respond(200, json={"state": "FINISHED"})
    mock_api.get(f"{FLOW_PATH}/results").respond(200, json=list(artifacts))
    for artifact in artifacts:
        mock_api.get(f"{FLOW_PATH}/results/{artifact}").respond(
            200, json=page([{"row": i} for i in range(rows_per_artifact)])
        )
    mock_api.get(f"{FLOW_PATH}/logs").respond(200, json=page([{"message": "log"}]))
    return post


# -- preset -> artifacts mapping ----------------------------------------------


@pytest.mark.parametrize(("preset", "artifacts"), sorted(PRESETS.items()))
def test_preset_maps_to_artifacts_in_post_body(invoke, mock_api, tmp_path, preset, artifacts):
    post = mock_finished_flow(mock_api, artifacts)
    result, parsed = invoke(
        "ops", "live", CLIENT_ID, "--preset", preset, "--out", str(tmp_path / "case")
    )
    assert result.exit_code == 0
    assert post.calls.last.request.method == "POST"
    assert post.calls.last.request.url.path.endswith(FLOWS_PATH)
    assert json.loads(post.calls.last.request.content) == {
        "artifacts": artifacts,
        "specs": [],
    }
    assert parsed["presets"] == [preset]


def test_multiple_presets_union_deduped(invoke, mock_api, tmp_path):
    expected = ["Windows.System.Pslist", "Windows.Network.Netstat"]
    post = mock_finished_flow(mock_api, expected)
    result, parsed = invoke(
        "ops", "live", CLIENT_ID,
        "--preset", "pslist", "--preset", "netstat", "--preset", "pslist",
        "--out", str(tmp_path / "case"),
    )
    assert result.exit_code == 0
    assert json.loads(post.calls.last.request.content)["artifacts"] == expected
    assert parsed["presets"] == ["pslist", "netstat"]


def test_urgent_flag_in_post_body(invoke, mock_api, tmp_path):
    post = mock_finished_flow(mock_api, PRESETS["users"])
    result, _parsed = invoke(
        "ops", "live", CLIENT_ID, "--preset", "users", "--urgent",
        "--out", str(tmp_path / "case"),
    )
    assert result.exit_code == 0
    assert json.loads(post.calls.last.request.content)["urgent"] is True


def test_preset_artifacts_rejects_unknown():
    with pytest.raises(UsageError, match="Unknown preset"):
        preset_artifacts(["pslist", "bogus"])


# -- end to end: evidence dir + manifest + audit -------------------------------


def test_end_to_end_writes_evidence_dir(invoke, mock_api, tmp_path):
    artifact = PRESETS["persistence"][0]
    mock_finished_flow(mock_api, [artifact], rows_per_artifact=2)
    out = tmp_path / "case-live"
    result, parsed = invoke(
        "ops", "live", CLIENT_ID, "--preset", "persistence", "--out", str(out)
    )
    assert result.exit_code == 0
    assert parsed == {
        "ops": "live",
        "host": CLIENT_ID,
        "client_id": CLIENT_ID,
        "flow_id": FLOW_ID,
        "state": "FINISHED",
        "presets": ["persistence"],
        "artifacts": {artifact: 2},
        "log_lines": 1,
        "out_dir": str(out),
        "manifest_files": 4,
    }

    assert (out / "flow.json").is_file()
    rows = [json.loads(line) for line in (out / f"results/{artifact}.jsonl").read_text().splitlines()]
    assert rows == [{"row": 0}, {"row": 1}]
    assert (out / "logs.jsonl").is_file()

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["collection"]["command"] == "ops live"
    assert manifest["collection"]["flow_id"] == FLOW_ID
    assert manifest["collection"]["presets"] == ["persistence"]
    assert manifest["collection"]["artifacts"] == [artifact]
    assert {f["name"] for f in manifest["files"]} == {
        "flow.json",
        f"results/{artifact}.jsonl",
        "logs.jsonl",
        "audit.jsonl",
    }


def test_audit_record_written(invoke, mock_api, tmp_path, monkeypatch):
    central = tmp_path / "central" / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(central))
    mock_finished_flow(mock_api, PRESETS["tasks"])
    out = tmp_path / "case"
    result, _parsed = invoke("ops", "live", CLIENT_ID, "--preset", "tasks", "--out", str(out))
    assert result.exit_code == 0

    local = [json.loads(line) for line in (out / "audit.jsonl").read_text().splitlines()]
    assert len(local) == 1
    record = local[0]
    assert record["command"] == "ops live"
    assert record["created"] == {"client_id": CLIENT_ID, "flow_id": FLOW_ID}
    assert record["presets"] == ["tasks"]
    assert record["artifacts"] == PRESETS["tasks"]
    assert json.loads(central.read_text().splitlines()[0]) == record


def test_resolves_hostname_to_client_id(invoke, mock_api, tmp_path):
    clients_route = mock_api.get("/clients").respond(
        200,
        json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws-bravo"}}]),
    )
    post = mock_finished_flow(mock_api, PRESETS["pslist"])
    result, parsed = invoke(
        "ops", "live", "ws-bravo", "--preset", "pslist", "--out", str(tmp_path / "case")
    )
    assert result.exit_code == 0
    assert "hostname=ws-bravo" in str(clients_route.calls.last.request.url)
    assert post.calls.last.request.url.path.endswith(FLOWS_PATH)
    assert parsed["host"] == "ws-bravo"
    assert parsed["client_id"] == CLIENT_ID


def test_default_out_dir_under_evidence(transport, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_finished_flow(mock_api, PRESETS["netstat"])
    summary = live_response(transport, CLIENT_ID, ("netstat",), timeout=5, poll_interval=1)
    expected = default_out_dir(CLIENT_ID, FLOW_ID)
    assert expected == Path("evidence") / f"live-{CLIENT_ID}-{FLOW_ID}"
    assert summary["out_dir"] == str(tmp_path / expected)
    assert (tmp_path / expected / "manifest.json").is_file()


# -- wait behavior --------------------------------------------------------------


def test_always_waits_polls_until_finished(invoke, mock_api, tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    flow_route = mock_api.get(FLOW_PATH)
    flow_route.side_effect = [
        httpx.Response(200, json={"state": "RUNNING"}),
        httpx.Response(200, json={"state": "FINISHED"}),
        httpx.Response(200, json={"state": "FINISHED"}),  # collect re-reads the flow
    ]
    artifact = PRESETS["users"][0]
    mock_api.get(f"{FLOW_PATH}/results").respond(200, json=[artifact])
    mock_api.get(f"{FLOW_PATH}/results/{artifact}").respond(200, json=page([{"row": 1}]))
    mock_api.get(f"{FLOW_PATH}/logs").respond(200, json=page([]))
    result, parsed = invoke(
        "ops", "live", CLIENT_ID, "--preset", "users",
        "--poll-interval", "1", "--out", str(tmp_path / "case"),
    )
    assert result.exit_code == 0
    assert flow_route.call_count == 3
    assert parsed["state"] == "FINISHED"


def test_wait_timeout_exit_5_writes_no_evidence(env, mock_api, capsys, tmp_path, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    mock_api.get(FLOW_PATH).respond(200, json={"state": "RUNNING"})
    out = tmp_path / "case"
    code, stdout, _err = run_main(
        capsys,
        "ops", "live", CLIENT_ID, "--preset", "pslist",
        "--timeout", "5", "--poll-interval", "1", "--out", str(out),
    )
    assert code == 5
    assert json.loads(stdout)["error"]["type"] == "WaitTimeout"
    assert not out.exists()


# -- usage errors ----------------------------------------------------------------


def test_unknown_preset_rejected_by_click_exit_2(env, capsys, tmp_path):
    code, stdout, _err = run_main(
        capsys, "ops", "live", CLIENT_ID, "--preset", "bogus", "--out", str(tmp_path / "case")
    )
    assert code == 2
    doc = json.loads(stdout)
    assert doc["error"]["exit_code"] == 2
    assert "preset" in doc["error"]["message"].lower()
    assert not (tmp_path / "case").exists()


def test_preset_is_required(env, capsys):
    code, stdout, _err = run_main(capsys, "ops", "live", CLIENT_ID)
    assert code == 2
    assert json.loads(stdout)["error"]["exit_code"] == 2


# -- dry run ----------------------------------------------------------------------


def test_dry_run_sends_nothing_and_writes_nothing(invoke, mock_api, tmp_path, monkeypatch):
    central = tmp_path / "central-audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(central))
    post = mock_api.post(FLOWS_PATH)
    out = tmp_path / "case"
    result, parsed = invoke(
        "ops", "live", CLIENT_ID,
        "--preset", "browser-history", "--out", str(out), "--dry-run",
    )
    assert result.exit_code == 0
    assert post.call_count == 0
    assert parsed["dry_run"] is True
    assert parsed["client_id"] == CLIENT_ID
    assert parsed["artifacts"] == PRESETS["browser-history"]
    assert parsed["request"]["method"] == "POST"
    assert parsed["request"]["url"].endswith(FLOWS_PATH)
    assert parsed["request"]["body"] == {
        "artifacts": PRESETS["browser-history"],
        "specs": [],
    }
    assert not out.exists()
    assert not central.exists()
