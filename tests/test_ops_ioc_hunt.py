"""`vr ops ioc-hunt`: FileFinder body shapes per IOC type, no-IOC rejection,
client-side hash filtering, --wait completion, dry-run, audit records."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from vrcli.cli.main import main

from .conftest import ENV, page

# Placeholder hashes only (repo policy: no realistic IOC/secret material).
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

DEFAULT_GLOB = "C:\\Users\\**"


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    # Keep audit lines out of the developer's real central log.
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(tmp_path / "central-audit.jsonl"))
    return ENV


@pytest.fixture
def audit_log(monkeypatch, tmp_path):
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(target))
    return target


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


def _env_params(body: dict) -> dict:
    (spec,) = body["start_request"]["specs"]
    return {entry["key"]: entry["value"] for entry in spec["parameters"]["env"]}


# -- request body shapes -------------------------------------------------------


def test_hash_only_body_shape(invoke, mock_api, audit_log):
    route = mock_api.post("/hunts").respond(200, json="H.1")
    before = time.time()
    result, doc = invoke(
        "ops", "ioc-hunt",
        "--hash", HASH_A,
        "--hash", HASH_B,
        "--label", "scope",
        "--exclude-label", "critical-infrastructure",
        "--os", "windows",
        "--client-limit", "500",
        "--tag", "case-1",
    )
    after = time.time()

    assert result.exit_code == 0
    assert doc == {"hunt_id": "H.1"}
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path.endswith("/hunts")
    body = _body(route)
    assert body["start_request"]["artifacts"] == ["Windows.Search.FileFinder"]
    # Hash IOCs: scope glob + Calculate_Hash="Y" (Velociraptor booleans are "Y"/"N")
    assert _env_params(body) == {"SearchFilesGlob": DEFAULT_GLOB, "Calculate_Hash": "Y"}
    assert body["condition"] == {
        "labels": {"label": ["scope"]},
        "excluded_labels": {"label": ["critical-infrastructure"]},
        "os": {"os": "WINDOWS"},
    }
    assert body["client_limit"] == 500
    assert body["tags"] == ["case-1"]
    # default expiry is 7d from now, epoch microseconds
    week = 7 * 86400
    assert (before + week) * 1_000_000 <= body["expires"] <= (after + week + 1) * 1_000_000
    # default description mentions the IOCs
    assert "2 SHA-256 hash(es)" in body["hunt_description"]


def test_single_filename_uses_search_files_glob(invoke, mock_api, audit_log):
    route = mock_api.post("/hunts").respond(200, json="H.2")
    result, doc = invoke("ops", "ioc-hunt", "--filename", "C:\\Windows\\Temp\\evil*.exe")

    assert result.exit_code == 0
    assert doc == {"hunt_id": "H.2"}
    body = _body(route)
    assert _env_params(body) == {"SearchFilesGlob": "C:\\Windows\\Temp\\evil*.exe"}
    assert "1 filename glob(s)" in body["hunt_description"]


def test_multiple_filenames_use_glob_table(invoke, mock_api, audit_log):
    route = mock_api.post("/hunts").respond(200, json="H.3")
    result, _doc = invoke(
        "ops", "ioc-hunt", "--filename", "**/evil.exe", "--filename", "**/bad.dll"
    )

    assert result.exit_code == 0
    body = _body(route)
    assert _env_params(body) == {"SearchFilesGlobTable": "Glob\n**/evil.exe\n**/bad.dll"}


def test_yara_rule_read_from_file(invoke, mock_api, audit_log, tmp_path):
    rule = 'rule Demo { strings: $a = "marker" condition: $a }\n'
    rule_file = tmp_path / "demo.yar"
    rule_file.write_text(rule, encoding="utf-8")
    route = mock_api.post("/hunts").respond(200, json="H.4")
    result, _doc = invoke(
        "ops", "ioc-hunt", "--yara", str(rule_file), "--glob", "D:\\Data\\**"
    )

    assert result.exit_code == 0
    body = _body(route)
    assert _env_params(body) == {"SearchFilesGlob": "D:\\Data\\**", "YaraRule": rule}
    assert "a YARA rule" in body["hunt_description"]


def test_ioc_types_combine(invoke, mock_api, audit_log, tmp_path):
    rule_file = tmp_path / "combo.yar"
    rule_file.write_text("rule C { condition: true }", encoding="utf-8")
    route = mock_api.post("/hunts").respond(200, json="H.5")
    result, _doc = invoke(
        "ops", "ioc-hunt",
        "--hash", HASH_A,
        "--filename", "**/dropper.exe",
        "--yara", str(rule_file),
        "--description", "custom text",
    )

    assert result.exit_code == 0
    body = _body(route)
    # --filename defines the scope; --glob is not injected alongside it
    assert _env_params(body) == {
        "SearchFilesGlob": "**/dropper.exe",
        "YaraRule": "rule C { condition: true }",
        "Calculate_Hash": "Y",
    }
    assert body["hunt_description"] == "custom text"


def test_custom_artifact_option(invoke, mock_api, audit_log):
    route = mock_api.post("/hunts").respond(200, json="H.6")
    result, _doc = invoke(
        "ops", "ioc-hunt", "--hash", HASH_A, "--artifact", "Generic.Search.FileFinder"
    )

    assert result.exit_code == 0
    body = _body(route)
    assert body["start_request"]["artifacts"] == ["Generic.Search.FileFinder"]
    assert body["start_request"]["specs"][0]["artifact"] == "Generic.Search.FileFinder"


# -- IOC validation ------------------------------------------------------------


def test_no_ioc_rejected_exit_2(env, mock_api, capsys):
    route = mock_api.post("/hunts").respond(200, json="H.0")
    code = main(["ops", "ioc-hunt"])
    out = capsys.readouterr().out

    assert code == 2
    assert route.call_count == 0  # refused before any request
    error = json.loads(out)["error"]
    assert error["type"] == "UsageError"
    assert "--hash" in error["message"]
    assert "--yara" in error["message"]


# -- --wait summary ------------------------------------------------------------


def _complete_hunt(stats_extra: dict | None = None) -> dict:
    stats = {"total_clients_scheduled": 2, "total_clients_with_results": 2}
    stats.update(stats_extra or {})
    return {"hunt_id": "H.9", "state": "RUNNING", "stats": stats}


def test_wait_polls_until_stats_complete(invoke, mock_api, audit_log, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mock_api.post("/hunts").respond(200, json="H.9")
    poll = mock_api.get("/hunts/H.9")
    poll.side_effect = [
        httpx.Response(
            200,
            json={
                "hunt_id": "H.9",
                "state": "RUNNING",
                "stats": {"total_clients_scheduled": 2, "total_clients_with_results": 1},
            },
        ),
        httpx.Response(200, json=_complete_hunt()),
    ]
    results = mock_api.get("/hunts/H.9/results")
    results.side_effect = [  # two pages: --all semantics follow the cursor
        httpx.Response(200, json=page([{"ClientId": "C.1", "OSPath": "C:\\a"}], cursor="c1")),
        httpx.Response(
            200,
            json=page([{"ClientId": "C.1", "OSPath": "C:\\b"}, {"client_id": "C.2"}]),
        ),
    ]
    mock_api.get("/hunts/H.9/errors").respond(200, json=[])

    result, doc = invoke(
        "ops", "ioc-hunt", "--filename", "**/evil.exe", "--wait", "--poll-interval", "1"
    )

    assert result.exit_code == 0
    assert poll.call_count == 2  # first poll incomplete, second complete
    assert results.call_count == 2
    assert dict(results.calls.last.request.url.params)["cursor"] == "c1"
    assert doc == {
        "hunt_id": "H.9",
        "complete": True,
        "clients_with_hits": {"C.1": 2, "C.2": 1},
        "total_hits": 3,
        "clients_with_errors": 0,
        "stats": _complete_hunt()["stats"],
    }


def test_wait_filters_rows_by_hash(invoke, mock_api, audit_log, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mock_api.post("/hunts").respond(200, json="H.9")
    mock_api.get("/hunts/H.9").respond(200, json=_complete_hunt())
    rows = [
        {"ClientId": "C.1", "Hash": {"SHA256": HASH_A.upper()}},  # match (case-insensitive)
        {"client_id": "C.2", "SHA256": HASH_C},  # non-match: hash not hunted
        {"_ClientId": "C.1", "Sha256": HASH_B},  # match via alternate key + client key
        {"ClientId": "C.3", "Hash": HASH_A},  # match via bare Hash string
        {"ClientId": "C.4", "OSPath": "C:\\no-hash"},  # non-match: no hash in row
    ]
    mock_api.get("/hunts/H.9/results").respond(200, json=page(rows))
    mock_api.get("/hunts/H.9/errors").respond(
        200, json=[{"client_id": "C.9", "errors": [{"error_message": "boom"}]}]
    )

    result, doc = invoke(
        "ops", "ioc-hunt", "--hash", HASH_A, "--hash", HASH_B, "--wait"
    )

    assert result.exit_code == 0
    assert doc["clients_with_hits"] == {"C.1": 2, "C.3": 1}
    assert doc["total_hits"] == 3
    assert doc["clients_with_errors"] == 1
    assert doc["complete"] is True
    assert doc["hunt_id"] == "H.9"


# -- dry-run -------------------------------------------------------------------


def test_dry_run_describes_post_without_sending(invoke, mock_api, audit_log):
    route = mock_api.post("/hunts").respond(200, json="H.1")
    result, doc = invoke(
        "ops", "ioc-hunt", "--hash", HASH_A, "--os", "linux", "--dry-run"
    )

    assert result.exit_code == 0
    assert route.call_count == 0  # nothing was sent
    assert doc["dry_run"] is True
    assert doc["method"] == "POST"
    assert doc["url"].endswith("/hunts")
    assert doc["body"]["condition"]["os"] == {"os": "LINUX"}
    env = {e["key"]: e["value"] for e in doc["body"]["start_request"]["specs"][0]["parameters"]["env"]}
    assert env == {"SearchFilesGlob": DEFAULT_GLOB, "Calculate_Hash": "Y"}
    assert not audit_log.exists()  # dry-run creates nothing, so no audit record


# -- audit ---------------------------------------------------------------------


def test_audit_record_written_once_with_hunt_id(invoke, mock_api, audit_log):
    mock_api.post("/hunts").respond(200, json="H.42")
    result, _doc = invoke("ops", "ioc-hunt", "--hash", HASH_A)

    assert result.exit_code == 0
    lines = audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["command"] == "ops ioc-hunt"
    assert record["created"] == {"hunt_id": "H.42"}
    assert record["operator"]["user"]
    assert record["ts"]


def test_audit_record_written_on_wait_success(invoke, mock_api, audit_log, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mock_api.post("/hunts").respond(200, json="H.9")
    mock_api.get("/hunts/H.9").respond(200, json=_complete_hunt())
    mock_api.get("/hunts/H.9/results").respond(200, json=page([]))
    mock_api.get("/hunts/H.9/errors").respond(200, json=[])

    result, doc = invoke("ops", "ioc-hunt", "--filename", "**/x", "--wait")

    assert result.exit_code == 0
    assert doc["total_hits"] == 0
    lines = audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["created"] == {"hunt_id": "H.9"}
