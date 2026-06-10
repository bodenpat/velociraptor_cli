"""Hunt API bindings: exact paths, query params, body shapes, normalization."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from vrcli.api import hunts as api
from vrcli.errors import APIError, UsageError

from .conftest import page

# -- list / iter ---------------------------------------------------------------


def test_list_hunts_params_uppercased(transport, mock_api):
    route = mock_api.get("/hunts").respond(200, json=page([{"hunt_id": "H.1"}]))
    result = api.list_hunts(transport, state="running", sort="oldest", limit=5, cursor="c0")

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path.endswith("/hunts")
    assert dict(request.url.params) == {
        "state": "RUNNING",
        "sort": "OLDEST",
        "limit": "5",
        "cursor": "c0",
    }
    assert result["data"] == [{"hunt_id": "H.1"}]


def test_list_hunts_drops_unset_params(transport, mock_api):
    route = mock_api.get("/hunts").respond(200, json=page([]))
    api.list_hunts(transport)
    assert str(route.calls.last.request.url.query, "ascii") == ""


def test_iter_hunts_follows_cursor(transport, mock_api):
    route = mock_api.get("/hunts")
    route.side_effect = [
        httpx.Response(200, json=page([{"hunt_id": "H.1"}], cursor="c1")),
        httpx.Response(200, json=page([{"hunt_id": "H.2"}])),
    ]
    items = list(api.iter_hunts(transport, state="stopped"))

    assert [item["hunt_id"] for item in items] == ["H.1", "H.2"]
    assert route.call_count == 2
    first, second = route.calls
    assert "cursor" not in dict(first.request.url.params)
    assert dict(second.request.url.params)["cursor"] == "c1"
    assert dict(second.request.url.params)["state"] == "STOPPED"


# -- create ----------------------------------------------------------------


def test_create_hunt_posts_body_and_returns_id_string(transport, mock_api):
    route = mock_api.post("/hunts").respond(200, json="H.123")
    body = {"start_request": {"artifacts": ["A.B"], "specs": []}}
    assert api.create_hunt(transport, body) == "H.123"

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path.endswith("/hunts")
    assert json.loads(request.content) == body


@pytest.mark.parametrize("payload", [{"hunt_id": "H.9"}, {"id": "H.9"}])
def test_create_hunt_normalizes_dict_response(transport, mock_api, payload):
    mock_api.post("/hunts").respond(200, json=payload)
    assert api.create_hunt(transport, {"start_request": {}}) == "H.9"


def test_create_hunt_rejects_unrecognized_dict(transport, mock_api):
    mock_api.post("/hunts").respond(200, json={"weird": True})
    with pytest.raises(APIError, match="unrecognized"):
        api.create_hunt(transport, {"start_request": {}})


def test_create_hunt_dry_run_describes_request(config):
    from vrcli.transport import VRTransport

    with VRTransport(config, dry_run=True) as dry:
        body = {"start_request": {"artifacts": ["A.B"], "specs": []}}
        described = api.create_hunt(dry, body)
    assert described["dry_run"] is True
    assert described["method"] == "POST"
    assert described["url"].endswith("/hunts")
    assert described["body"] == body


# -- get / state -------------------------------------------------------------


def test_get_hunt_path(transport, mock_api):
    route = mock_api.get("/hunts/H.1").respond(200, json={"hunt_id": "H.1"})
    assert api.get_hunt(transport, "H.1") == {"hunt_id": "H.1"}
    assert route.calls.last.request.method == "GET"


def test_get_hunt_quotes_path_segment(transport, mock_api):
    route = mock_api.get(url__regex=r".*/hunts/.*").respond(200, json={})
    api.get_hunt(transport, "h 1/../x")
    assert route.calls.last.request.url.raw_path.endswith(b"/hunts/h%201%2F..%2Fx")


@pytest.mark.parametrize("desired", ["paused", "RUNNING", "stopped"])
def test_set_hunt_state_patches_desired_state(transport, mock_api, desired):
    route = mock_api.patch("/hunts/H.1").respond(204)
    assert api.set_hunt_state(transport, "H.1", desired) is None

    request = route.calls.last.request
    assert request.method == "PATCH"
    assert dict(request.url.params) == {"desiredState": desired.upper()}


# -- results / errors --------------------------------------------------------


def test_get_hunt_results_params(transport, mock_api):
    route = mock_api.get("/hunts/H.1/results").respond(200, json=page([{"row": 1}]))
    result = api.get_hunt_results(transport, "H.1", limit=50, cursor="c2")

    request = route.calls.last.request
    assert request.method == "GET"
    assert dict(request.url.params) == {"limit": "50", "cursor": "c2"}
    assert result["data"] == [{"row": 1}]


def test_iter_hunt_results_follows_cursor(transport, mock_api):
    route = mock_api.get("/hunts/H.1/results")
    route.side_effect = [
        httpx.Response(200, json=page([{"row": 1}], cursor="c1")),
        httpx.Response(200, json=page([{"row": 2}])),
    ]
    rows = list(api.iter_hunt_results(transport, "H.1"))
    assert rows == [{"row": 1}, {"row": 2}]
    assert dict(route.calls.last.request.url.params)["cursor"] == "c1"


def test_get_hunt_errors_bare_array(transport, mock_api):
    payload = [{"client_id": "C.1", "errors": [{"artifact": "A.B", "error_message": "boom"}]}]
    route = mock_api.get("/hunts/H.1/errors").respond(200, json=payload)
    assert api.get_hunt_errors(transport, "H.1") == payload
    assert route.calls.last.request.method == "GET"


# -- build_hunt_body ----------------------------------------------------------


def test_build_hunt_body_full_shape():
    start_request = {"artifacts": ["A.B"], "specs": [], "urgent": True}
    before = time.time()
    body = api.build_hunt_body(
        start_request,
        labels=("scope", "ir"),
        excluded_labels=("critical-infrastructure",),
        os="darwin",
        client_limit=100,
        expires_in=3600,
        description="sweep",
        tags=("case-1",),
    )
    after = time.time()

    assert body["start_request"] is start_request
    assert body["condition"] == {
        "labels": {"label": ["scope", "ir"]},
        "excluded_labels": {"label": ["critical-infrastructure"]},
        "os": {"os": "OSX"},
    }
    assert body["client_limit"] == 100
    assert body["hunt_description"] == "sweep"
    assert body["tags"] == ["case-1"]
    # expires is epoch MICROSECONDS, one hour from now (PLAN.md §10.5 assumption).
    assert (before + 3600) * 1_000_000 <= body["expires"] <= (after + 3601) * 1_000_000


def test_build_hunt_body_minimal_omits_unset_keys():
    body = api.build_hunt_body({"artifacts": ["A.B"], "specs": []})
    assert body == {"start_request": {"artifacts": ["A.B"], "specs": []}}


@pytest.mark.parametrize(
    ("cli_os", "api_os"),
    [("windows", "WINDOWS"), ("linux", "LINUX"), ("darwin", "OSX"), ("all", "ALL")],
)
def test_build_hunt_body_os_mapping(cli_os, api_os):
    body = api.build_hunt_body({"artifacts": [], "specs": []}, os=cli_os)
    assert body["condition"] == {"os": {"os": api_os}}


def test_build_hunt_body_rejects_unknown_os():
    with pytest.raises(UsageError, match="Unknown OS"):
        api.build_hunt_body({"artifacts": [], "specs": []}, os="beos")
