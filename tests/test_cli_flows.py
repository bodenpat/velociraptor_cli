"""`vr flows`: command wiring, output contract, exit codes, --wait, --dry-run."""

from __future__ import annotations

import json

import httpx
import pytest

from vrcli.cli.main import main

from .conftest import ENV, page

CLIENT_ID = "C.1111111111111111"
FLOW_ID = "F.AAAA1111"
FLOWS_PATH = f"/clients/{CLIENT_ID}/flows"
FLOW_PATH = f"{FLOWS_PATH}/{FLOW_ID}"


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# -- list ----------------------------------------------------------------------


def test_list_one_page(invoke, mock_api):
    route = mock_api.get(FLOWS_PATH).respond(200, json=page([{"session_id": FLOW_ID}]))
    result, parsed = invoke(
        "flows", "list", CLIENT_ID, "--creator", "alice", "--sort", "oldest", "--limit", "5"
    )
    assert result.exit_code == 0
    assert parsed == page([{"session_id": FLOW_ID}])
    url = str(route.calls.last.request.url)
    assert route.calls.last.request.method == "GET"
    assert "creator=alice" in url
    assert "sort=OLDEST" in url
    assert "limit=5" in url


def test_list_resolves_hostname_to_client_id(invoke, mock_api):
    clients_route = mock_api.get("/clients").respond(
        200,
        json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws-alpha"}}]),
    )
    flows_route = mock_api.get(FLOWS_PATH).respond(200, json=page([]))
    result, parsed = invoke("flows", "list", "ws-alpha")
    assert result.exit_code == 0
    assert parsed == page([])
    assert "hostname=ws-alpha" in str(clients_route.calls.last.request.url)
    assert flows_route.calls.last.request.url.path.endswith(FLOWS_PATH)


def test_list_all_follows_cursors(invoke, mock_api):
    route = mock_api.get(FLOWS_PATH)
    route.side_effect = [
        httpx.Response(200, json=page([{"session_id": "F.AAAA1111"}], cursor="next1")),
        httpx.Response(200, json=page([{"session_id": "F.BBBB2222"}])),
    ]
    result, parsed = invoke("flows", "list", CLIENT_ID, "--all")
    assert result.exit_code == 0
    assert parsed == [{"session_id": "F.AAAA1111"}, {"session_id": "F.BBBB2222"}]
    assert route.call_count == 2


# -- create ----------------------------------------------------------------------


def test_create_body_exact_shape(invoke, mock_api):
    route = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    result, parsed = invoke(
        "flows", "create", CLIENT_ID,
        "--artifact", "Windows.KapeFiles.Targets",
        "--param", "Windows.KapeFiles.Targets:Device=C:",
        "--collection-timeout", "600",
        "--cpu-limit", "20",
        "--max-upload-bytes", "1073741824",
        "--urgent",
    )
    assert result.exit_code == 0
    assert parsed == {"flow_id": FLOW_ID, "client_id": CLIENT_ID}
    assert json.loads(route.calls.last.request.content) == {
        "artifacts": ["Windows.KapeFiles.Targets"],
        "specs": [
            {
                "artifact": "Windows.KapeFiles.Targets",
                "parameters": {"env": [{"key": "Device", "value": "C:"}]},
            }
        ],
        "timeout": 600,
        "cpu_limit": 20.0,
        "max_upload_bytes": 1073741824,
        "urgent": True,
    }


def test_create_dry_run_sends_nothing(invoke, mock_api):
    route = mock_api.post(FLOWS_PATH)
    result, parsed = invoke("flows", "create", CLIENT_ID, "--artifact", "A.B", "--dry-run")
    assert result.exit_code == 0
    assert route.call_count == 0
    assert parsed["dry_run"] is True
    assert parsed["method"] == "POST"
    assert parsed["url"].endswith(FLOWS_PATH)
    assert parsed["body"] == {"artifacts": ["A.B"], "specs": []}


def test_create_wait_polls_until_finished(invoke, mock_api, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    flow_route = mock_api.get(FLOW_PATH)
    flow_route.side_effect = [
        httpx.Response(200, json={"state": "RUNNING"}),
        httpx.Response(200, json={"state": "FINISHED", "total_collected_rows": 7}),
    ]
    result, parsed = invoke(
        "flows", "create", CLIENT_ID, "--artifact", "A.B", "--wait", "--poll-interval", "1"
    )
    assert result.exit_code == 0
    assert flow_route.call_count == 2
    assert parsed == {"state": "FINISHED", "total_collected_rows": 7, "flow_id": FLOW_ID}


def test_create_wait_error_flow_exits_6(env, mock_api, capsys):
    mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    mock_api.get(FLOW_PATH).respond(200, json={"state": "ERROR", "status": "boom"})
    code, out, err = run_main(
        capsys, "flows", "create", CLIENT_ID, "--artifact", "A.B", "--wait"
    )
    assert code == 6
    doc = json.loads(out)
    assert doc["error"]["type"] == "APIError"
    assert FLOW_ID in doc["error"]["message"]
    assert "error" in err.lower()


def test_create_requires_artifact(env, capsys):
    code, out, _err = run_main(capsys, "flows", "create", CLIENT_ID)
    assert code == 2
    assert json.loads(out)["error"]["exit_code"] == 2


# -- get ----------------------------------------------------------------------


def test_get_flow(invoke, mock_api):
    route = mock_api.get(FLOW_PATH).respond(200, json={"state": "FINISHED"})
    result, parsed = invoke("flows", "get", CLIENT_ID, FLOW_ID)
    assert result.exit_code == 0
    assert parsed == {"state": "FINISHED"}
    assert route.calls.last.request.url.path.endswith(FLOW_PATH)


# -- results --------------------------------------------------------------------


def test_results_without_artifact_lists_available(invoke, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results").respond(200, json=["A.B", "C.D"])
    result, parsed = invoke("flows", "results", CLIENT_ID, FLOW_ID)
    assert result.exit_code == 0
    assert parsed == ["A.B", "C.D"]
    assert route.calls.last.request.url.path.endswith(f"{FLOW_PATH}/results")


def test_results_with_artifact_one_page(invoke, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results/A.B").respond(200, json=page([{"row": 1}]))
    result, parsed = invoke("flows", "results", CLIENT_ID, FLOW_ID, "--artifact", "A.B")
    assert result.exit_code == 0
    assert parsed == page([{"row": 1}])
    assert route.calls.last.request.url.query == b""  # cursor only per spec; none sent


def test_results_with_artifact_and_source(invoke, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results/A.B/src").respond(200, json=page([{"row": 1}]))
    result, parsed = invoke(
        "flows", "results", CLIENT_ID, FLOW_ID, "--artifact", "A.B", "--source", "src"
    )
    assert result.exit_code == 0
    assert parsed == page([{"row": 1}])
    assert route.calls.last.request.url.path.endswith(f"{FLOW_PATH}/results/A.B/src")


def test_results_all_follows_cursors(invoke, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results/A.B")
    route.side_effect = [
        httpx.Response(200, json=page([{"row": 1}], cursor="next1")),
        httpx.Response(200, json=page([{"row": 2}])),
    ]
    result, parsed = invoke(
        "flows", "results", CLIENT_ID, FLOW_ID, "--artifact", "A.B", "--all"
    )
    assert result.exit_code == 0
    assert parsed == [{"row": 1}, {"row": 2}]
    assert route.call_count == 2


def test_results_source_without_artifact_exit_2(env, capsys):
    code, out, _err = run_main(
        capsys, "flows", "results", CLIENT_ID, FLOW_ID, "--source", "src"
    )
    assert code == 2
    doc = json.loads(out)
    assert doc["error"]["type"] == "UsageError"
    assert "--source requires --artifact" in doc["error"]["message"]


def test_results_out_without_artifact_exit_2(env, capsys, tmp_path):
    code, out, _err = run_main(
        capsys, "flows", "results", CLIENT_ID, FLOW_ID, "--out", str(tmp_path / "rows.jsonl")
    )
    assert code == 2
    assert "--out requires --artifact" in json.loads(out)["error"]["message"]


def test_results_out_writes_jsonl(invoke, mock_api, tmp_path):
    mock_api.get(f"{FLOW_PATH}/results/A.B").side_effect = [
        httpx.Response(200, json=page([{"row": 1}, {"row": 2}], cursor="next1")),
        httpx.Response(200, json=page([{"row": 3}])),
    ]
    out_file = tmp_path / "rows.jsonl"
    result, parsed = invoke(
        "flows", "results", CLIENT_ID, FLOW_ID,
        "--artifact", "A.B", "--all", "--out", str(out_file),
    )
    assert result.exit_code == 0
    assert parsed == {"rows": 3, "out": str(out_file)}
    lines = [json.loads(line) for line in out_file.read_text().splitlines()]
    assert lines == [{"row": 1}, {"row": 2}, {"row": 3}]


def test_results_out_one_page_extracts_rows(invoke, mock_api, tmp_path):
    mock_api.get(f"{FLOW_PATH}/results/A.B").respond(200, json=page([{"row": 1}]))
    out_file = tmp_path / "rows.jsonl"
    result, parsed = invoke(
        "flows", "results", CLIENT_ID, FLOW_ID, "--artifact", "A.B", "--out", str(out_file)
    )
    assert result.exit_code == 0
    assert parsed == {"rows": 1, "out": str(out_file)}
    assert json.loads(out_file.read_text().strip()) == {"row": 1}


def test_results_encodes_artifact_path_segment(invoke, mock_api):
    route = mock_api.get(path__regex=r".*/results/.*").respond(200, json=page([]))
    result, _parsed = invoke(
        "flows", "results", CLIENT_ID, FLOW_ID, "--artifact", "Custom.My Artifact"
    )
    assert result.exit_code == 0
    assert b"/results/Custom.My%20Artifact" in route.calls.last.request.url.raw_path


# -- logs ----------------------------------------------------------------------


def test_logs_one_page(invoke, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/logs").respond(200, json=page([{"message": "a"}]))
    result, parsed = invoke("flows", "logs", CLIENT_ID, FLOW_ID)
    assert result.exit_code == 0
    assert parsed == page([{"message": "a"}])
    assert route.calls.last.request.url.query == b""


def test_logs_all_follows_cursors(invoke, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/logs")
    route.side_effect = [
        httpx.Response(200, json=page([{"message": "a"}], cursor="next1")),
        httpx.Response(200, json=page([{"message": "b"}])),
    ]
    result, parsed = invoke("flows", "logs", CLIENT_ID, FLOW_ID, "--all")
    assert result.exit_code == 0
    assert parsed == [{"message": "a"}, {"message": "b"}]
    assert route.call_count == 2
    assert "cursor=next1" in str(route.calls.last.request.url)
