"""Client API bindings: path encoding, metadata body, hostname resolution.

These bindings (getClient/updateClientMetadata/deleteClient) had no
request-level coverage before; the URL-encoding tests are the regression
guard for the path-traversal gap the review found.
"""

from __future__ import annotations

import httpx
import pytest

from vrcli.api import clients as api
from vrcli.errors import APIError, NotFoundError, UsageError


def test_get_client_encodes_path_segment(transport, mock_api):
    route = mock_api.route(method="GET").respond(200, json={"client_id": "C.a/b"})
    api.get_client(transport, "C.a/b")
    # The '/' must be percent-encoded so the request hits /clients/<seg>, not a
    # traversed path.
    assert route.calls.last.request.url.raw_path.endswith(b"/clients/C.a%2Fb")


def test_update_client_encodes_segment_and_sends_body(transport, mock_api):
    route = mock_api.route(method="PUT").respond(204)
    api.update_client_metadata(transport, "C.x/y", add={"owner": "ir"}, remove=["stale"])
    request = route.calls.last.request
    assert request.url.raw_path.endswith(b"/clients/C.x%2Fy")
    import json

    assert json.loads(request.content) == {"add": {"owner": "ir"}, "remove": ["stale"]}


def test_delete_client_encodes_segment(transport, mock_api):
    route = mock_api.route(method="DELETE").respond(204)
    api.delete_client(transport, "C.a/../secret")
    assert route.calls.last.request.url.raw_path.endswith(b"/clients/C.a%2F..%2Fsecret")


def test_get_client_plain_id_unchanged(transport, mock_api):
    route = mock_api.get("/clients/C.1234").respond(200, json={"client_id": "C.1234"})
    api.get_client(transport, "C.1234")
    assert route.calls.last.request.url.path.endswith("/clients/C.1234")


# -- resolve_client_arg ------------------------------------------------------


def test_resolve_client_arg_passthrough_for_client_id(transport, mock_api):
    # A C.-prefixed value is used verbatim with no lookup.
    assert api.resolve_client_arg(transport, "C.deadbeef") == "C.deadbeef"
    assert len(mock_api.calls) == 0


def test_resolve_client_arg_resolves_hostname(transport, mock_api):
    mock_api.get("/clients").respond(
        200, json={"size": 1, "data": [{"client_id": "C.99", "hostname": "host1"}]}
    )
    assert api.resolve_client_arg(transport, "host1") == "C.99"


def test_resolve_client_arg_host_prefix(transport, mock_api):
    mock_api.get("/clients").respond(
        200, json={"size": 1, "data": [{"client_id": "C.5", "hostname": "C.looks-like-id"}]}
    )
    # `host:` forces hostname resolution even for a value that looks like an ID.
    assert api.resolve_client_arg(transport, "host:C.looks-like-id") == "C.5"


def test_resolve_client_arg_missing_client_id_raises_apierror(transport, mock_api):
    # A resolved record without a client_id must not raise a bare KeyError.
    mock_api.get("/clients").respond(200, json={"size": 1, "data": [{"hostname": "host1"}]})
    with pytest.raises(APIError, match="no client_id"):
        api.resolve_client_arg(transport, "host1")


def test_resolve_hostname_no_match_is_not_found(transport, mock_api):
    mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    with pytest.raises(NotFoundError):
        api.resolve_hostname(transport, "ghost")


def test_resolve_hostname_ambiguous_without_first(transport, mock_api):
    mock_api.get("/clients").respond(
        200,
        json={
            "size": 2,
            "data": [
                {"client_id": "C.1", "hostname": "web-1"},
                {"client_id": "C.2", "hostname": "web-10"},
            ],
        },
    )
    # Substring match returns two non-exact hits -> ambiguous.
    with pytest.raises(UsageError, match="matches 2 clients"):
        api.resolve_hostname(transport, "web")


def test_resolve_hostname_exact_match_wins(transport, mock_api):
    mock_api.get("/clients").mock(
        return_value=httpx.Response(
            200,
            json={
                "size": 2,
                "data": [
                    {"client_id": "C.1", "hostname": "web-1"},
                    {"client_id": "C.2", "hostname": "web-10"},
                ],
            },
        )
    )
    # Exact (case-insensitive) hostname equality disambiguates without --first.
    assert api.resolve_hostname(transport, "web-1")["client_id"] == "C.1"
