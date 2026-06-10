"""Ops infrastructure: evidence manifests, audit JSONL, wait loops."""

from __future__ import annotations

import hashlib
import json

import pytest

from vrcli.errors import UsageError, WaitTimeout
from vrcli.ops._audit import write_audit
from vrcli.ops._evidence import EvidenceDir
from vrcli.ops._wait import hunt_complete, wait_until

# -- evidence ----------------------------------------------------------------


def test_evidence_manifest_hashes_files(tmp_path):
    out = tmp_path / "case-001"
    ev = EvidenceDir(out, context={"client_id": "C.1", "flow_id": "F.1"})
    ev.write_json("results/A.B.json", [{"row": 1}])
    ev.write_jsonl("logs.jsonl", [{"line": 1}, {"line": 2}])
    manifest = ev.finalize()

    assert manifest["collection"] == {"client_id": "C.1", "flow_id": "F.1"}
    assert {f["name"] for f in manifest["files"]} == {"results/A.B.json", "logs.jsonl"}
    for entry in manifest["files"]:
        path = out / entry["name"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["size_bytes"] == path.stat().st_size

    on_disk = json.loads((out / "manifest.json").read_text())
    assert on_disk["files"] == manifest["files"]
    assert on_disk["operator"]["user"]


def test_evidence_refuses_nonempty_dir(tmp_path):
    out = tmp_path / "case"
    out.mkdir()
    (out / "junk.txt").write_text("old")
    with pytest.raises(UsageError, match="not empty"):
        EvidenceDir(out)


def test_evidence_refuses_path_traversal(tmp_path):
    ev = EvidenceDir(tmp_path / "case")
    with pytest.raises(UsageError, match="outside"):
        ev.write_json("../escape.json", {})


def test_evidence_reserves_manifest_name(tmp_path):
    ev = EvidenceDir(tmp_path / "case")
    with pytest.raises(UsageError, match="reserved"):
        ev.write_json("manifest.json", {})


def test_evidence_ingest_existing_file(tmp_path):
    source = tmp_path / "triage.zip"
    source.write_bytes(b"zipbytes")
    ev = EvidenceDir(tmp_path / "case")
    ev.add_existing_file(source, note="downloaded from GUI")
    manifest = ev.finalize()
    (entry,) = manifest["files"]
    assert entry["name"] == "triage.zip"
    assert entry["sha256"] == hashlib.sha256(b"zipbytes").hexdigest()
    assert entry["ingested_from"] == str(source.resolve())
    assert entry["note"] == "downloaded from GUI"


# -- audit -------------------------------------------------------------------


def test_audit_writes_to_evidence_dir_and_central_log(tmp_path, monkeypatch):
    central = tmp_path / "central" / "audit.jsonl"
    monkeypatch.setenv("R7_VR_AUDIT_LOG", str(central))
    record = write_audit("ops triage", out_dir=tmp_path / "case", created={"flow_id": "F.1"})

    local_lines = (tmp_path / "case" / "audit.jsonl").read_text().splitlines()
    central_lines = central.read_text().splitlines()
    assert json.loads(local_lines[0]) == json.loads(central_lines[0])
    assert record["created"] == {"flow_id": "F.1"}
    assert record["command"] == "ops triage"
    assert record["operator"]["user"]
    assert record["ts"]


def test_audit_appends(tmp_path, monkeypatch):
    monkeypatch.delenv("R7_VR_AUDIT_LOG", raising=False)
    write_audit("ops contain", out_dir=tmp_path)
    write_audit("ops release", out_dir=tmp_path)
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_audit_redacts_key_in_argv(tmp_path, monkeypatch):
    key = "99999999-8888-7777-6666-555555555555"
    monkeypatch.setenv("R7_VR_API_KEY", key)
    monkeypatch.setattr("sys.argv", ["vr", "ops", "triage", f"--oops={key}"])
    record = write_audit("ops triage", out_dir=tmp_path)
    assert key not in json.dumps(record)


# -- wait --------------------------------------------------------------------


def test_wait_until_success_no_sleep():
    assert wait_until(lambda: (True, "done"), timeout=1, poll_interval=1) == "done"


def test_wait_until_polls_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    states = iter([(False, None), (False, None), (True, "ok")])
    assert wait_until(lambda: next(states), timeout=60, poll_interval=5) == "ok"
    assert len(sleeps) == 2


def test_wait_until_timeout(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr("time.sleep", sleep)
    with pytest.raises(WaitTimeout):
        wait_until(lambda: (False, None), timeout=30, poll_interval=10)


@pytest.mark.parametrize(
    ("hunt", "done"),
    [
        ({"state": "STOPPED"}, True),
        ({"state": "RUNNING", "stats": {"stopped": True}}, True),
        ({"state": "RUNNING", "stats": {"total_clients_scheduled": 0}}, False),
        (
            {
                "state": "RUNNING",
                "stats": {
                    "total_clients_scheduled": 10,
                    "total_clients_with_results": 7,
                    "total_clients_with_errors": 3,
                },
            },
            True,
        ),
        (
            {
                "state": "RUNNING",
                "stats": {"total_clients_scheduled": 10, "total_clients_with_results": 5},
            },
            False,
        ),
    ],
)
def test_hunt_complete_heuristic(hunt, done):
    assert hunt_complete(hunt) is done
