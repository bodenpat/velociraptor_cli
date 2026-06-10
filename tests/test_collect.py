"""collect_flow_evidence: tolerate both response shapes from getAvailableFlowResults."""

from __future__ import annotations

import json

from vrcli.ops._collect import collect_flow_evidence
from vrcli.ops._evidence import EvidenceDir

from .conftest import page

CLIENT = "C.1111111111111111"
FLOW = "F.2222222222222222"
ART = "Windows.System.Pslist"


def _wire(mock_api, available):
    mock_api.get(path__regex=rf"/clients/{CLIENT}/flows/{FLOW}$").respond(
        200, json={"state": "FINISHED"}
    )
    mock_api.get(path__regex=rf"/flows/{FLOW}/results$").respond(200, json=available)
    mock_api.get(path__regex=rf"/results/{ART}$").respond(200, json=page([{"pid": 1}, {"pid": 2}]))
    mock_api.get(path__regex=rf"/flows/{FLOW}/logs$").respond(200, json=page([{"line": "ok"}]))


def test_collect_handles_envelope_available_results(transport, mock_api, tmp_path):
    """getAvailableFlowResults returning a {size,cursor,data} envelope must be
    unwrapped — not iterated as if its keys ('size','cursor','data') were
    artifact names."""
    _wire(mock_api, page([ART]))
    evidence = EvidenceDir(tmp_path / "case")
    summary = collect_flow_evidence(transport, CLIENT, FLOW, evidence)
    assert summary["artifacts"] == {ART: 2}
    assert summary["log_lines"] == 1
    rows = (tmp_path / "case" / f"results/{ART}.jsonl").read_text().splitlines()
    assert [json.loads(r) for r in rows] == [{"pid": 1}, {"pid": 2}]
    # No bogus 'size.jsonl' / 'data.jsonl' files from iterating envelope keys.
    assert not (tmp_path / "case" / "results" / "data.jsonl").exists()


def test_collect_handles_bare_array_available_results(transport, mock_api, tmp_path):
    _wire(mock_api, [ART])
    evidence = EvidenceDir(tmp_path / "case")
    summary = collect_flow_evidence(transport, CLIENT, FLOW, evidence)
    assert summary["artifacts"] == {ART: 2}


def test_collect_handles_list_of_dicts_available_results(transport, mock_api, tmp_path):
    _wire(mock_api, [{"artifact": ART}])
    evidence = EvidenceDir(tmp_path / "case")
    summary = collect_flow_evidence(transport, CLIENT, FLOW, evidence)
    assert summary["artifacts"] == {ART: 2}
