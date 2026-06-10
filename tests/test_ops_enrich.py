"""`vr ops enrich`: document shape, flow truncation, missing-field tolerance, audit."""

from __future__ import annotations

import json

import pytest

from vrcli.cli.main import main
from vrcli.ops.enrich import flow_summary, last_seen

from .conftest import ENV, page

CLIENT_ID = "C.1111111111111111"
CLIENT_PATH = f"/clients/{CLIENT_ID}"
LABELS_PATH = f"{CLIENT_PATH}/labels"
FLOWS_PATH = f"{CLIENT_PATH}/flows"

CLIENT_RECORD = {
    "client_id": CLIENT_ID,
    "os_info": {"hostname": "ws-alpha"},
    "last_seen_at": "2026-06-09T12:00:00Z",
}
FLOW_FULL = {
    "session_id": "F.AAAA1111",
    "state": "FINISHED",
    "create_time": 1717000000,
    "request": {"artifacts": ["Windows.System.Pslist"]},
}
FLOW_SPARSE = {"flow_id": "F.BBBB2222", "state": "RUNNING"}


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def mock_happy_path(mock_api, *, client=CLIENT_RECORD, labels=None, flows_body=None):
    """Stub the three GETs enrich makes for a known client ID."""
    routes = {
        "client": mock_api.get(CLIENT_PATH).respond(200, json=client),
        "labels": mock_api.get(LABELS_PATH).respond(
            200, json=["vip"] if labels is None else labels
        ),
        "flows": mock_api.get(FLOWS_PATH).respond(
            200, json=page([FLOW_FULL, FLOW_SPARSE]) if flows_body is None else flows_body
        ),
    }
    return routes


# -- document shape ------------------------------------------------------------


def test_enrich_document_shape_and_requests(invoke, mock_api):
    routes = mock_happy_path(mock_api)
    result, parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    assert parsed == {
        "client": CLIENT_RECORD,
        "labels": ["vip"],
        "last_seen_at": "2026-06-09T12:00:00Z",
        "recent_flows": [
            {
                "flow_id": "F.AAAA1111",
                "state": "FINISHED",
                "create_time": 1717000000,
                "artifacts": ["Windows.System.Pslist"],
            },
            {
                "flow_id": "F.BBBB2222",
                "state": "RUNNING",
                "create_time": None,
                "artifacts": [],
            },
        ],
    }
    for name, path in (("client", CLIENT_PATH), ("labels", LABELS_PATH), ("flows", FLOWS_PATH)):
        request = routes[name].calls.last.request
        assert request.method == "GET"
        assert request.url.path.endswith(path)
    flows_url = str(routes["flows"].calls.last.request.url)
    assert "sort=NEWEST" in flows_url
    assert "limit=10" in flows_url  # default --flows


def test_enrich_resolves_hostname(invoke, mock_api):
    resolve_route = mock_api.get("/clients").respond(
        200, json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws-alpha"}}])
    )
    mock_happy_path(mock_api)
    result, parsed = invoke("ops", "enrich", "ws-alpha")
    assert result.exit_code == 0
    assert parsed["client"] == CLIENT_RECORD
    assert "hostname=ws-alpha" in str(resolve_route.calls.last.request.url)


def test_enrich_host_not_found_exit_4(env, mock_api, capsys):
    mock_api.get("/clients").respond(200, json=page([]))
    code, out, _err = run_main(capsys, "ops", "enrich", "ghost-host")
    assert code == 4
    doc = json.loads(out)
    assert doc["error"]["type"] == "NotFoundError"
    assert "ghost-host" in doc["error"]["message"]


# -- flow truncation -------------------------------------------------------------


def test_enrich_truncates_flows_to_n(invoke, mock_api):
    extra_flows = [
        {"session_id": f"F.{c * 4}{i}{i}{i}{i}", "state": "FINISHED"}
        for i, c in enumerate("ABCD")
    ]
    routes = mock_happy_path(mock_api, flows_body=page(extra_flows))
    result, parsed = invoke("ops", "enrich", CLIENT_ID, "--flows", "2")
    assert result.exit_code == 0
    assert len(parsed["recent_flows"]) == 2  # truncated even though server sent 4
    assert [f["flow_id"] for f in parsed["recent_flows"]] == ["F.AAAA0000", "F.BBBB1111"]
    assert "limit=2" in str(routes["flows"].calls.last.request.url)


def test_enrich_flows_must_be_positive(env, capsys):
    code, out, _err = run_main(capsys, "ops", "enrich", CLIENT_ID, "--flows", "0")
    assert code == 2
    assert json.loads(out)["error"]["exit_code"] == 2


def test_enrich_tolerates_bare_array_flows_response(invoke, mock_api):
    mock_happy_path(mock_api, flows_body=[FLOW_FULL])  # no {size, cursor, data} envelope
    result, parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    assert [f["flow_id"] for f in parsed["recent_flows"]] == ["F.AAAA1111"]


# -- missing-field tolerance ------------------------------------------------------


def test_enrich_client_without_last_seen(invoke, mock_api):
    mock_happy_path(mock_api, client={"client_id": CLIENT_ID})
    result, parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    assert parsed["last_seen_at"] is None


def test_enrich_client_last_seen_time_fallback(invoke, mock_api):
    mock_happy_path(mock_api, client={"client_id": CLIENT_ID, "last_seen_time": 1717000000})
    result, parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    assert parsed["last_seen_at"] == 1717000000


def test_enrich_flow_without_request(invoke, mock_api):
    mock_happy_path(mock_api, flows_body=page([FLOW_SPARSE]))
    result, parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    assert parsed["recent_flows"] == [
        {"flow_id": "F.BBBB2222", "state": "RUNNING", "create_time": None, "artifacts": []}
    ]


def test_enrich_empty_labels_and_flows(invoke, mock_api):
    mock_happy_path(mock_api, labels=[], flows_body=page([]))
    result, parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    assert parsed["labels"] == []
    assert parsed["recent_flows"] == []


def test_flow_summary_non_dict_record():
    assert flow_summary("garbage") == {
        "flow_id": None,
        "state": None,
        "create_time": None,
        "artifacts": [],
    }


def test_last_seen_prefers_last_seen_at():
    record = {"last_seen_at": "2026-06-09T12:00:00Z", "last_seen_time": 1717000000}
    assert last_seen(record) == "2026-06-09T12:00:00Z"
    assert last_seen(None) is None


# -- audit -------------------------------------------------------------------------


def test_enrich_writes_central_audit_record(invoke, mock_api, monkeypatch, tmp_path):
    central = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(central))
    mock_happy_path(mock_api)
    result, _parsed = invoke("ops", "enrich", CLIENT_ID)
    assert result.exit_code == 0
    lines = central.read_text().splitlines()
    assert len(lines) == 1  # exactly one audit record per invocation
    record = json.loads(lines[0])
    assert record["command"] == "ops enrich"
    assert record["client_id"] == CLIENT_ID
    assert record["recent_flows"] == 2
    assert record["operator"]["user"]
    assert record["ts"]
    assert "created" not in record  # read-only: nothing created


def test_enrich_no_audit_record_on_failure(env, mock_api, monkeypatch, tmp_path, capsys):
    central = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(central))
    mock_api.get(CLIENT_PATH).respond(404, json="no such client")
    code, _out, _err = run_main(capsys, "ops", "enrich", CLIENT_ID)
    assert code == 4
    assert not central.exists()
