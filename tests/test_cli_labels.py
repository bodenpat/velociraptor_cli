"""`vr labels` CLI: list/add/remove/bulk-add, hostname resolution, dry-run, exit codes."""

from __future__ import annotations

import json

import pytest

from vrcli.cli.main import cli, main

from .conftest import ENV, page

CLIENT_ID = "C.aaaaaaaaaaaaaaaa"
OTHER_ID = "C.bbbbbbbbbbbbbbbb"


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


# -- list --------------------------------------------------------------------


def test_list_by_client_id(invoke, mock_api):
    route = mock_api.get(f"/clients/{CLIENT_ID}/labels").respond(200, json=["ioc-hit", "triage"])
    result, doc = invoke("labels", "list", CLIENT_ID)
    assert result.exit_code == 0
    assert doc == ["ioc-hit", "triage"]
    assert route.calls.last.request.method == "GET"


def test_list_accepts_hostname(invoke, mock_api):
    mock_api.get("/clients", params={"hostname": "ws01"}).respond(
        200, json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws01"}}])
    )
    route = mock_api.get(f"/clients/{CLIENT_ID}/labels").respond(200, json=[])
    result, doc = invoke("labels", "list", "ws01")
    assert result.exit_code == 0
    assert doc == []
    assert route.called


def test_list_404_is_exit_4(env, mock_api, capsys):
    mock_api.get(f"/clients/{CLIENT_ID}/labels").respond(404, json="Client labels not found")
    code, out, _err = run_main(capsys, "labels", "list", CLIENT_ID)
    assert code == 4
    doc = json.loads(out)
    assert doc["error"]["type"] == "NotFoundError"
    assert doc["error"]["exit_code"] == 4


# -- add ---------------------------------------------------------------------


def test_add_emits_confirmation_on_empty_body(invoke, mock_api):
    route = mock_api.patch(f"/clients/{CLIENT_ID}/labels/triage").respond(200)
    result, doc = invoke("labels", "add", CLIENT_ID, "triage")
    assert result.exit_code == 0
    assert doc == {"client_id": CLIENT_ID, "label": "triage", "action": "added"}
    assert route.calls.last.request.method == "PATCH"


def test_add_url_encodes_label_with_space(invoke, mock_api):
    route = mock_api.patch(f"/clients/{CLIENT_ID}/labels/under%20investigation").respond(200)
    result, doc = invoke("labels", "add", CLIENT_ID, "under investigation")
    assert result.exit_code == 0
    assert doc["action"] == "added"
    assert route.calls.last.request.url.raw_path.endswith(b"/labels/under%20investigation")


def test_add_dry_run_describes_request_without_sending(invoke, mock_api):
    result, doc = invoke("labels", "add", CLIENT_ID, "triage", "--dry-run")
    assert result.exit_code == 0
    assert doc["dry_run"] is True
    assert doc["method"] == "PATCH"
    assert doc["url"].endswith(f"/clients/{CLIENT_ID}/labels/triage")
    assert len(mock_api.calls) == 0  # nothing was sent


def test_add_404_is_exit_4(env, mock_api, capsys):
    mock_api.patch(f"/clients/{CLIENT_ID}/labels/triage").respond(404, json="not found")
    code, out, _err = run_main(capsys, "labels", "add", CLIENT_ID, "triage")
    assert code == 4
    assert json.loads(out)["error"]["type"] == "NotFoundError"


# -- remove ------------------------------------------------------------------


def test_remove_emits_confirmation_on_204(invoke, mock_api):
    route = mock_api.delete(f"/clients/{CLIENT_ID}/labels/old-case").respond(204)
    result, doc = invoke("labels", "remove", CLIENT_ID, "old-case")
    assert result.exit_code == 0
    assert doc == {"client_id": CLIENT_ID, "label": "old-case", "action": "removed"}
    assert route.calls.last.request.method == "DELETE"


def test_remove_dry_run_describes_request_without_sending(invoke, mock_api):
    result, doc = invoke("labels", "remove", CLIENT_ID, "old-case", "--dry-run")
    assert result.exit_code == 0
    assert doc["dry_run"] is True
    assert doc["method"] == "DELETE"
    assert doc["url"].endswith(f"/clients/{CLIENT_ID}/labels/old-case")
    assert len(mock_api.calls) == 0


# -- bulk-add ----------------------------------------------------------------


def test_bulk_add_with_client_id_options(invoke, mock_api):
    route = mock_api.post("/clients/bulk-labels").respond(200, json=[])
    result, doc = invoke(
        "labels", "bulk-add", "ioc-hit", "--client-id", CLIENT_ID, "--client-id", OTHER_ID
    )
    assert result.exit_code == 0
    assert doc == []
    assert json.loads(route.calls.last.request.content) == {
        "client_ids": [CLIENT_ID, OTHER_ID],
        "label": "ioc-hit",
    }


def test_bulk_add_from_file_skips_blanks_comments_and_dedupes(invoke, mock_api, tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(
        f"# triage scope\n{CLIENT_ID}\n\n  {OTHER_ID}  \n{CLIENT_ID}\n",
        encoding="utf-8",
    )
    route = mock_api.post("/clients/bulk-labels").respond(200, json=[])
    result, _doc = invoke("labels", "bulk-add", "ioc-hit", "--from-file", str(ids_file))
    assert result.exit_code == 0
    assert json.loads(route.calls.last.request.content) == {
        "client_ids": [CLIENT_ID, OTHER_ID],  # de-duplicated, order preserved
        "label": "ioc-hit",
    }


def test_bulk_add_stdin(runner, mock_api):
    route = mock_api.post("/clients/bulk-labels").respond(200, json=[])
    result = runner.invoke(
        cli,
        ["labels", "bulk-add", "ioc-hit", "--stdin"],
        input=f"# from SOAR\n{CLIENT_ID}\n\n{OTHER_ID}\n",
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert json.loads(route.calls.last.request.content) == {
        "client_ids": [CLIENT_ID, OTHER_ID],
        "label": "ioc-hit",
    }


def test_bulk_add_combines_sources_and_resolves_hostnames(invoke, mock_api, tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(f"{OTHER_ID}\n", encoding="utf-8")
    mock_api.get("/clients", params={"hostname": "ws01"}).respond(
        200, json=page([{"client_id": CLIENT_ID, "os_info": {"hostname": "ws01"}}])
    )
    route = mock_api.post("/clients/bulk-labels").respond(200, json=[])
    result, _doc = invoke(
        "labels", "bulk-add", "ioc-hit", "--client-id", "ws01", "--from-file", str(ids_file)
    )
    assert result.exit_code == 0
    assert json.loads(route.calls.last.request.content) == {
        "client_ids": [CLIENT_ID, OTHER_ID],  # --client-id sources first, then file
        "label": "ioc-hit",
    }


def test_bulk_add_no_ids_is_usage_error_exit_2(env, mock_api, capsys):
    code, out, _err = run_main(capsys, "labels", "bulk-add", "ioc-hit")
    assert code == 2
    doc = json.loads(out)
    assert doc["error"]["type"] == "UsageError"
    assert "client" in doc["error"]["message"].lower()
    assert len(mock_api.calls) == 0


def test_bulk_add_dry_run_describes_request_without_sending(invoke, mock_api):
    result, doc = invoke(
        "labels", "bulk-add", "ioc-hit", "--client-id", CLIENT_ID, "--dry-run"
    )
    assert result.exit_code == 0
    assert doc["dry_run"] is True
    assert doc["method"] == "POST"
    assert doc["url"].endswith("/clients/bulk-labels")
    assert doc["body"] == {"client_ids": [CLIENT_ID], "label": "ioc-hit"}
    assert len(mock_api.calls) == 0
