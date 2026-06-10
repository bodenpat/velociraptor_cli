"""`vr hunts`: option mapping, body shapes, dry-run, --wait, --out, exit codes."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from vrcli.cli.main import main

from .conftest import page

# -- list ----------------------------------------------------------------------


def test_list_passes_filters_uppercased(invoke, mock_api):
    route = mock_api.get("/hunts").respond(200, json=page([{"hunt_id": "H.1"}]))
    result, doc = invoke("hunts", "list", "--state", "running", "--sort", "oldest", "--limit", "5")

    assert result.exit_code == 0
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path.endswith("/hunts")
    assert dict(request.url.params) == {"state": "RUNNING", "sort": "OLDEST", "limit": "5"}
    assert doc == {"size": 1, "data": [{"hunt_id": "H.1"}]}


def test_list_all_follows_cursors(invoke, mock_api):
    route = mock_api.get("/hunts")
    route.side_effect = [
        httpx.Response(200, json=page([{"hunt_id": "H.1"}], cursor="c1")),
        httpx.Response(200, json=page([{"hunt_id": "H.2"}])),
    ]
    result, doc = invoke("hunts", "list", "--all")

    assert result.exit_code == 0
    assert doc == [{"hunt_id": "H.1"}, {"hunt_id": "H.2"}]
    assert route.call_count == 2
    assert dict(route.calls.last.request.url.params)["cursor"] == "c1"


# -- create ----------------------------------------------------------------


def test_create_full_body_shape(invoke, mock_api):
    route = mock_api.post("/hunts").respond(200, json="H.77")
    before = time.time()
    result, doc = invoke(
        "hunts", "create",
        "--artifact", "A.B",
        "--param", "A.B:k=v",
        "--label", "scope",
        "--label", "ir",
        "--exclude-label", "critical-infrastructure",
        "--os", "darwin",
        "--client-limit", "100",
        "--expires", "1h",
        "--description", "ioc sweep",
        "--tag", "case-1",
        "--urgent",
    )
    after = time.time()

    assert result.exit_code == 0
    assert doc == {"hunt_id": "H.77"}
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path.endswith("/hunts")
    body = json.loads(request.content)
    assert body["start_request"]["artifacts"] == ["A.B"]
    assert body["start_request"]["specs"] == [
        {"artifact": "A.B", "parameters": {"env": [{"key": "k", "value": "v"}]}}
    ]
    assert body["start_request"]["urgent"] is True
    assert body["condition"] == {
        "labels": {"label": ["scope", "ir"]},
        "excluded_labels": {"label": ["critical-infrastructure"]},
        "os": {"os": "OSX"},
    }
    assert body["client_limit"] == 100
    assert body["hunt_description"] == "ioc sweep"
    assert body["tags"] == ["case-1"]
    # epoch microseconds, one hour out (PLAN.md §10.5 assumption)
    assert (before + 3600) * 1_000_000 <= body["expires"] <= (after + 3601) * 1_000_000


def test_create_default_expires_is_7d(invoke, mock_api):
    route = mock_api.post("/hunts").respond(200, json="H.1")
    before = time.time()
    result, _doc = invoke("hunts", "create", "--artifact", "A.B")
    after = time.time()

    assert result.exit_code == 0
    body = json.loads(route.calls.last.request.content)
    week = 7 * 86400
    assert (before + week) * 1_000_000 <= body["expires"] <= (after + week + 1) * 1_000_000
    assert "condition" not in body  # nothing set -> key omitted


def test_create_dry_run_describes_without_sending(invoke, mock_api):
    route = mock_api.post("/hunts").respond(200, json="H.1")
    result, doc = invoke("hunts", "create", "--artifact", "A.B", "--os", "windows", "--dry-run")

    assert result.exit_code == 0
    assert route.call_count == 0  # nothing was sent
    assert doc["dry_run"] is True
    assert doc["method"] == "POST"
    assert doc["url"].endswith("/hunts")
    assert doc["body"]["start_request"]["artifacts"] == ["A.B"]
    assert doc["body"]["condition"]["os"] == {"os": "WINDOWS"}


def test_create_wait_polls_until_complete(invoke, mock_api, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mock_api.post("/hunts").respond(200, json="H.5")
    poll = mock_api.get("/hunts/H.5")
    poll.side_effect = [
        httpx.Response(
            200,
            json={
                "hunt_id": "H.5",
                "state": "RUNNING",
                "stats": {"total_clients_scheduled": 2, "total_clients_with_results": 1},
            },
        ),
        httpx.Response(
            200,
            json={
                "hunt_id": "H.5",
                "state": "RUNNING",
                "stats": {
                    "total_clients_scheduled": 2,
                    "total_clients_with_results": 1,
                    "total_clients_with_errors": 1,
                },
            },
        ),
    ]
    result, doc = invoke("hunts", "create", "--artifact", "A.B", "--wait", "--poll-interval", "1")

    assert result.exit_code == 0
    assert poll.call_count == 2  # first poll incomplete, second complete
    assert doc["hunt_id"] == "H.5"
    assert doc["stats"]["total_clients_with_errors"] == 1


# -- get / pause / resume / stop ----------------------------------------------


def test_get_hunt(invoke, mock_api):
    mock_api.get("/hunts/H.1").respond(200, json={"hunt_id": "H.1", "state": "RUNNING"})
    result, doc = invoke("hunts", "get", "H.1")
    assert result.exit_code == 0
    assert doc == {"hunt_id": "H.1", "state": "RUNNING"}


@pytest.mark.parametrize(
    ("args", "desired"),
    [
        (("hunts", "pause", "H.1"), "PAUSED"),
        (("hunts", "resume", "H.1"), "RUNNING"),
        (("hunts", "stop", "H.1", "--yes"), "STOPPED"),
    ],
)
def test_state_changes_patch_desired_state(invoke, mock_api, args, desired):
    route = mock_api.patch("/hunts/H.1").respond(204)
    result, doc = invoke(*args)

    assert result.exit_code == 0
    request = route.calls.last.request
    assert request.method == "PATCH"
    assert dict(request.url.params) == {"desiredState": desired}
    assert doc == {"hunt_id": "H.1", "state": desired}


def test_stop_without_yes_exits_2(mock_api, capsys):
    route = mock_api.patch("/hunts/H.1").respond(204)
    code = main(["hunts", "stop", "H.1"])
    out = capsys.readouterr().out

    assert code == 2
    assert route.call_count == 0  # refused before any request
    error = json.loads(out)["error"]
    assert error["type"] == "UsageError"
    assert "--yes" in error["message"]


def test_pause_dry_run(invoke, mock_api):
    route = mock_api.patch("/hunts/H.1").respond(204)
    result, doc = invoke("hunts", "pause", "H.1", "--dry-run")

    assert result.exit_code == 0
    assert route.call_count == 0
    assert doc["dry_run"] is True
    assert doc["method"] == "PATCH"
    assert doc["url"].endswith("/hunts/H.1")
    assert doc["params"] == {"desiredState": "PAUSED"}


# -- results / errors --------------------------------------------------------


def test_results_emits_page(invoke, mock_api):
    route = mock_api.get("/hunts/H.1/results").respond(200, json=page([{"row": 1}]))
    result, doc = invoke("hunts", "results", "H.1", "--limit", "50")

    assert result.exit_code == 0
    assert dict(route.calls.last.request.url.params) == {"limit": "50"}
    assert doc == {"size": 1, "data": [{"row": 1}]}


def test_results_all_emits_rows(invoke, mock_api):
    route = mock_api.get("/hunts/H.1/results")
    route.side_effect = [
        httpx.Response(200, json=page([{"row": 1}], cursor="c1")),
        httpx.Response(200, json=page([{"row": 2}])),
    ]
    result, doc = invoke("hunts", "results", "H.1", "--all")
    assert result.exit_code == 0
    assert doc == [{"row": 1}, {"row": 2}]


def test_results_out_writes_jsonl(invoke, mock_api, tmp_path):
    mock_api.get("/hunts/H.1/results").respond(
        200, json=page([{"row": 1}, {"row": 2, "x": "y"}])
    )
    out_file = tmp_path / "rows.jsonl"
    result, doc = invoke("hunts", "results", "H.1", "--out", str(out_file))

    assert result.exit_code == 0
    assert doc == {"hunt_id": "H.1", "rows": 2, "out": str(out_file)}
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"row": 1}, {"row": 2, "x": "y"}]


def test_results_all_out_writes_every_page(invoke, mock_api, tmp_path):
    route = mock_api.get("/hunts/H.1/results")
    route.side_effect = [
        httpx.Response(200, json=page([{"row": 1}], cursor="c1")),
        httpx.Response(200, json=page([{"row": 2}])),
    ]
    out_file = tmp_path / "rows.jsonl"
    result, doc = invoke("hunts", "results", "H.1", "--all", "--out", str(out_file))

    assert result.exit_code == 0
    assert doc["rows"] == 2
    assert len(out_file.read_text(encoding="utf-8").splitlines()) == 2


def test_errors_passthrough(invoke, mock_api):
    payload = [{"client_id": "C.1", "errors": [{"artifact": "A.B", "error_message": "boom"}]}]
    mock_api.get("/hunts/H.1/errors").respond(200, json=payload)
    result, doc = invoke("hunts", "errors", "H.1")
    assert result.exit_code == 0
    assert doc == payload
