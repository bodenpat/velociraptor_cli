"""CLI root: vr status, exit-code contract, JSON-only stdout, --output table."""

from __future__ import annotations

import json

import pytest

from vrcli.cli.main import main

from .conftest import ENV, SENTINEL_KEY


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_status_ok(env, mock_api, capsys):
    route = mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    code, out, _err = run_main(capsys, "status")
    assert code == 0
    doc = json.loads(out)
    assert doc["status"] == "ok"
    assert doc["region"] == "us"
    assert "limit=1" in str(route.calls.last.request.url)


def test_status_auth_failure_exit_3(env, mock_api, capsys):
    mock_api.get("/clients").respond(401, json="unauthorized")
    code, out, err = run_main(capsys, "status")
    assert code == 3
    doc = json.loads(out)
    assert doc["error"]["type"] == "AuthError"
    assert "error" in err.lower()


def test_missing_env_is_exit_2(monkeypatch, capsys):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    code, out, _err = run_main(capsys, "status")
    assert code == 2
    assert json.loads(out)["error"]["type"] == "ConfigError"


def test_usage_error_exit_2(env, capsys):
    code, out, _err = run_main(capsys, "clients", "--bogus-flag")
    assert code == 2
    assert json.loads(out)["error"]["exit_code"] == 2


def test_help_exits_0(capsys):
    code, out, _err = run_main(capsys, "--help")
    assert code == 0
    assert "R7_VR_API_KEY" in out


def test_stdout_is_single_json_document(env, mock_api, capsys):
    mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    _code, out, _err = run_main(capsys, "status")
    json.loads(out)  # raises if stdout has anything but one JSON doc


def test_no_api_key_flag_exists(capsys):
    code, _out, _err = run_main(capsys, "--api-key", "x", "status")
    assert code == 2


def test_table_output(env, mock_api, capsys):
    mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    code, out, _err = run_main(capsys, "--output", "table", "status")
    assert code == 0
    assert "status" in out and "ok" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_key_never_in_output_on_failure(env, mock_api, capsys):
    """End-to-end redaction: API echoes the key in an error; CLI must scrub it."""
    mock_api.get("/clients").respond(403, json={"message": f"denied for {SENTINEL_KEY}"})
    _code, out, err = run_main(capsys, "status")
    assert SENTINEL_KEY not in out
    assert SENTINEL_KEY not in err


def test_verbose_logs_never_leak_key(env, mock_api, capsys):
    mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    _code, out, err = run_main(capsys, "--verbose", "status")
    assert SENTINEL_KEY not in out
    assert SENTINEL_KEY not in err
