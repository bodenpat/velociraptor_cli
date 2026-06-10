"""Label API bindings: methods, exact paths, URL-encoding, bodies, errors."""

from __future__ import annotations

import json

import pytest

from vrcli.api import labels as api
from vrcli.errors import NotFoundError

CLIENT_ID = "C.aaaaaaaaaaaaaaaa"
OTHER_ID = "C.bbbbbbbbbbbbbbbb"


def test_get_labels(transport, mock_api):
    route = mock_api.get(f"/clients/{CLIENT_ID}/labels").respond(200, json=["ioc-hit", "triage"])
    assert api.get_labels(transport, CLIENT_ID) == ["ioc-hit", "triage"]
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.raw_path.endswith(f"/clients/{CLIENT_ID}/labels".encode())
    assert request.url.query == b""


def test_get_labels_404_raises_not_found(transport, mock_api):
    mock_api.get(f"/clients/{CLIENT_ID}/labels").respond(404, json="Client labels not found")
    with pytest.raises(NotFoundError):
        api.get_labels(transport, CLIENT_ID)


def test_add_label(transport, mock_api):
    route = mock_api.patch(f"/clients/{CLIENT_ID}/labels/triage").respond(200)
    assert api.add_label(transport, CLIENT_ID, "triage") is None  # 200 with empty body
    request = route.calls.last.request
    assert request.method == "PATCH"
    assert request.url.raw_path.endswith(f"/clients/{CLIENT_ID}/labels/triage".encode())
    assert request.content == b""


def test_add_label_url_encodes_label_with_space(transport, mock_api):
    route = mock_api.patch(f"/clients/{CLIENT_ID}/labels/under%20investigation").respond(200)
    api.add_label(transport, CLIENT_ID, "under investigation")
    raw_path = route.calls.last.request.url.raw_path
    assert raw_path.endswith(b"/labels/under%20investigation")


def test_add_label_url_encodes_slash_in_label(transport, mock_api):
    """A label containing `/` must not change the path structure."""
    route = mock_api.patch(f"/clients/{CLIENT_ID}/labels/case%2F1234").respond(200)
    api.add_label(transport, CLIENT_ID, "case/1234")
    raw_path = route.calls.last.request.url.raw_path
    assert raw_path.endswith(b"/labels/case%2F1234")


def test_remove_label_204(transport, mock_api):
    route = mock_api.delete(f"/clients/{CLIENT_ID}/labels/old-case").respond(204)
    assert api.remove_label(transport, CLIENT_ID, "old-case") is None
    request = route.calls.last.request
    assert request.method == "DELETE"
    assert request.url.raw_path.endswith(f"/clients/{CLIENT_ID}/labels/old-case".encode())


def test_remove_label_url_encodes_label_with_space(transport, mock_api):
    route = mock_api.delete(f"/clients/{CLIENT_ID}/labels/under%20investigation").respond(204)
    api.remove_label(transport, CLIENT_ID, "under investigation")
    assert route.calls.last.request.url.raw_path.endswith(b"/labels/under%20investigation")


def test_bulk_add_labels(transport, mock_api):
    route = mock_api.post("/clients/bulk-labels").respond(
        200, json=[{"client_id": CLIENT_ID}, {"client_id": OTHER_ID}]
    )
    result = api.bulk_add_labels(transport, [CLIENT_ID, OTHER_ID], "ioc-hit")
    assert result == [{"client_id": CLIENT_ID}, {"client_id": OTHER_ID}]
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.raw_path.endswith(b"/clients/bulk-labels")
    assert json.loads(request.content) == {
        "client_ids": [CLIENT_ID, OTHER_ID],
        "label": "ioc-hit",
    }


def test_bulk_add_labels_dry_run_describes_without_sending(config, mock_api):
    from vrcli.transport import VRTransport

    with VRTransport(config, dry_run=True) as transport:
        described = api.bulk_add_labels(transport, [CLIENT_ID], "ioc-hit")
    assert described["dry_run"] is True
    assert described["method"] == "POST"
    assert described["url"].endswith("/clients/bulk-labels")
    assert described["body"] == {"client_ids": [CLIENT_ID], "label": "ioc-hit"}
    assert len(mock_api.calls) == 0
