"""api/flows.py: paths, query params, body shapes, pagination, ID normalization."""

from __future__ import annotations

import json

import httpx
import pytest

from vrcli.api import flows as api
from vrcli.errors import APIError
from vrcli.transport import VRTransport

from .conftest import page

CLIENT_ID = "C.1111111111111111"
FLOW_ID = "F.AAAA1111"
FLOWS_PATH = f"/clients/{CLIENT_ID}/flows"
FLOW_PATH = f"{FLOWS_PATH}/{FLOW_ID}"


# -- list / iter --------------------------------------------------------------


def test_list_flows_sends_filters(transport, mock_api):
    route = mock_api.get(FLOWS_PATH).respond(200, json=page([]))
    api.list_flows(
        transport, CLIENT_ID, creator="alice", sort="oldest", limit=5, cursor="cur1"
    )
    url = str(route.calls.last.request.url)
    assert route.calls.last.request.method == "GET"
    assert route.calls.last.request.url.path.endswith(FLOWS_PATH)
    assert "creator=alice" in url
    assert "sort=OLDEST" in url  # lowercase CLI enum -> UPPERCASE API enum
    assert "limit=5" in url
    assert "cursor=cur1" in url


def test_list_flows_drops_none_params(transport, mock_api):
    route = mock_api.get(FLOWS_PATH).respond(200, json=page([]))
    api.list_flows(transport, CLIENT_ID)
    assert route.calls.last.request.url.query == b""


def test_iter_flows_follows_cursors(transport, mock_api):
    route = mock_api.get(FLOWS_PATH)
    route.side_effect = [
        httpx.Response(200, json=page([{"session_id": "F.AAAA1111"}], cursor="next1")),
        httpx.Response(200, json=page([{"session_id": "F.BBBB2222"}])),
    ]
    items = list(api.iter_flows(transport, CLIENT_ID, creator="alice"))
    assert [item["session_id"] for item in items] == ["F.AAAA1111", "F.BBBB2222"]
    assert route.call_count == 2
    first, second = route.calls
    assert "cursor" not in str(first.request.url)
    assert "cursor=next1" in str(second.request.url)
    assert "creator=alice" in str(second.request.url)


# -- create -------------------------------------------------------------------


def test_create_flow_posts_body_and_returns_string_id(transport, mock_api):
    route = mock_api.post(FLOWS_PATH).respond(200, json=FLOW_ID)
    body = {"artifacts": ["A.B"], "specs": []}
    flow_id = api.create_flow(transport, CLIENT_ID, body)
    assert flow_id == FLOW_ID
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path.endswith(FLOWS_PATH)
    assert json.loads(request.content) == body


@pytest.mark.parametrize("key", ["flow_id", "session_id", "id"])
def test_create_flow_normalizes_dict_response(transport, mock_api, key):
    mock_api.post(FLOWS_PATH).respond(200, json={key: FLOW_ID})
    assert api.create_flow(transport, CLIENT_ID, {"artifacts": [], "specs": []}) == FLOW_ID


def test_create_flow_unrecognized_dict_raises(transport, mock_api):
    mock_api.post(FLOWS_PATH).respond(200, json={"unexpected": "shape"})
    with pytest.raises(APIError, match="no flow ID"):
        api.create_flow(transport, CLIENT_ID, {"artifacts": [], "specs": []})


def test_create_flow_empty_body_raises(transport, mock_api):
    mock_api.post(FLOWS_PATH).respond(204)
    with pytest.raises(APIError, match="no body"):
        api.create_flow(transport, CLIENT_ID, {"artifacts": [], "specs": []})


def test_create_flow_dry_run_describes_without_sending(config, mock_api):
    route = mock_api.post(FLOWS_PATH)
    with VRTransport(config, dry_run=True) as transport:
        described = api.create_flow(transport, CLIENT_ID, {"artifacts": ["A.B"], "specs": []})
    assert route.call_count == 0
    assert described["dry_run"] is True
    assert described["method"] == "POST"
    assert described["url"].endswith(FLOWS_PATH)
    assert described["body"] == {"artifacts": ["A.B"], "specs": []}


# -- get / available results --------------------------------------------------


def test_get_flow_path(transport, mock_api):
    route = mock_api.get(FLOW_PATH).respond(200, json={"state": "RUNNING"})
    assert api.get_flow(transport, CLIENT_ID, FLOW_ID) == {"state": "RUNNING"}
    assert route.calls.last.request.url.path.endswith(FLOW_PATH)
    assert route.calls.last.request.url.query == b""


def test_list_flow_results_path(transport, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results").respond(200, json=["A.B"])
    assert api.list_flow_results(transport, CLIENT_ID, FLOW_ID) == ["A.B"]
    assert route.calls.last.request.url.path.endswith(f"{FLOW_PATH}/results")
    assert route.calls.last.request.url.query == b""


# -- per-artifact results -----------------------------------------------------


def test_get_flow_results_cursor_only(transport, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results/A.B").respond(200, json=page([{"row": 1}]))
    api.get_flow_results(transport, CLIENT_ID, FLOW_ID, "A.B")
    assert route.calls.last.request.url.query == b""  # no cursor, and never a limit
    api.get_flow_results(transport, CLIENT_ID, FLOW_ID, "A.B", cursor="cur1")
    assert route.calls.last.request.url.query == b"cursor=cur1"


def test_get_flow_results_with_source_path(transport, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results/A.B/src").respond(200, json=page([]))
    api.get_flow_results(transport, CLIENT_ID, FLOW_ID, "A.B", source="src")
    assert route.calls.last.request.url.path.endswith(f"{FLOW_PATH}/results/A.B/src")


def test_get_flow_results_url_encodes_artifact_and_source(transport, mock_api):
    route = mock_api.get(path__regex=r".*/results/.*").respond(200, json=page([]))
    api.get_flow_results(transport, CLIENT_ID, FLOW_ID, "Custom.My Artifact", source="part/two")
    raw_path = route.calls.last.request.url.raw_path
    assert b"/results/Custom.My%20Artifact/part%2Ftwo" in raw_path


def test_iter_flow_results_follows_cursors(transport, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/results/A.B")
    route.side_effect = [
        httpx.Response(200, json=page([{"row": 1}], cursor="next1")),
        httpx.Response(200, json=page([{"row": 2}])),
    ]
    rows = list(api.iter_flow_results(transport, CLIENT_ID, FLOW_ID, "A.B"))
    assert rows == [{"row": 1}, {"row": 2}]
    assert "cursor=next1" in str(route.calls.last.request.url)


# -- logs ----------------------------------------------------------------------


def test_get_flow_logs_cursor_only(transport, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/logs").respond(200, json=page([{"message": "hi"}]))
    api.get_flow_logs(transport, CLIENT_ID, FLOW_ID)
    assert route.calls.last.request.url.path.endswith(f"{FLOW_PATH}/logs")
    assert route.calls.last.request.url.query == b""
    api.get_flow_logs(transport, CLIENT_ID, FLOW_ID, cursor="cur1")
    assert route.calls.last.request.url.query == b"cursor=cur1"


def test_iter_flow_logs_follows_cursors(transport, mock_api):
    route = mock_api.get(f"{FLOW_PATH}/logs")
    route.side_effect = [
        httpx.Response(200, json=page([{"message": "a"}], cursor="next1")),
        httpx.Response(200, json=page([{"message": "b"}])),
    ]
    lines = list(api.iter_flow_logs(transport, CLIENT_ID, FLOW_ID))
    assert lines == [{"message": "a"}, {"message": "b"}]
    assert route.call_count == 2
    assert "cursor=next1" in str(route.calls.last.request.url)
