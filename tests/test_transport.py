"""transport.py: auth header, retries, error mapping, dry-run, redaction."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from vrcli.errors import APIError, AuthError, NetworkError, NotFoundError
from vrcli.transport import VRTransport

from .conftest import BASE, SENTINEL_KEY


def test_auth_header_and_user_agent(transport, mock_api):
    route = mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    transport.request("GET", "/clients")
    request = route.calls.last.request
    assert request.headers["X-Api-Key"] == SENTINEL_KEY
    assert request.headers["User-Agent"].startswith("vrcli/")
    assert request.headers["Accept"] == "application/json"


def test_none_params_dropped(transport, mock_api):
    route = mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    transport.request("GET", "/clients", params={"limit": 1, "hostname": None})
    url = str(route.calls.last.request.url)
    assert "limit=1" in url
    assert "hostname" not in url


def test_204_returns_none(transport, mock_api):
    mock_api.delete("/clients/C.1").respond(204)
    assert transport.request("DELETE", "/clients/C.1") is None


@pytest.mark.parametrize(
    ("status", "exc"), [(401, AuthError), (403, AuthError), (404, NotFoundError), (400, APIError), (500, APIError)]
)
def test_error_mapping(config, status, exc):
    with VRTransport(config) as transport, respx.mock(base_url=BASE) as router:
        router.get("/clients/C.x").respond(status, json="boom")
        # disable retries for the 500 case by exhausting quickly
        transport.config = config.__class__(
            api_key=config.api_key, region=config.region, org_id=config.org_id, max_retries=0
        )
        with pytest.raises(exc):
            transport.request("GET", "/clients/C.x")


def test_retry_on_429_then_success(config, mock_api, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    route = mock_api.get("/hunts")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"size": 0, "data": []}),
    ]
    with VRTransport(config) as transport:
        result = transport.request("GET", "/hunts")
    assert result == {"size": 0, "data": []}
    assert route.call_count == 2


def test_retries_exhaust_to_api_error(config, mock_api, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    mock_api.get("/hunts").respond(503, json="unavailable")
    with VRTransport(config) as transport, pytest.raises(APIError) as excinfo:
        transport.request("GET", "/hunts")
    assert excinfo.value.status == 503


def test_network_error_maps_to_exit_7(config, mock_api, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    mock_api.get("/clients").mock(side_effect=httpx.ConnectError("dns failure"))
    with VRTransport(config) as transport, pytest.raises(NetworkError):
        transport.request("GET", "/clients")


def test_mutating_methods_not_sent_in_dry_run(config, mock_api):
    route = mock_api.post("/hunts")
    with VRTransport(config, dry_run=True) as transport:
        described = transport.request("POST", "/hunts", json_body={"client_limit": 5})
    assert route.call_count == 0
    assert described["dry_run"] is True
    assert described["method"] == "POST"
    assert described["url"].endswith("/hunts")
    assert described["body"] == {"client_limit": 5}


def test_dry_run_still_sends_reads(config, mock_api):
    route = mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    with VRTransport(config, dry_run=True) as transport:
        transport.request("GET", "/clients")
    assert route.call_count == 1


# -- redaction --------------------------------------------------------------


def test_error_detail_redacts_key(config, mock_api):
    """If the API ever echoed the key back, it must not survive into errors."""
    mock_api.get("/clients").respond(400, json={"message": f"bad key {SENTINEL_KEY}"})
    cfg = config.__class__(
        api_key=config.api_key, region=config.region, org_id=config.org_id, max_retries=0
    )
    with VRTransport(cfg) as transport, pytest.raises(APIError) as excinfo:
        transport.request("GET", "/clients")
    assert SENTINEL_KEY not in str(excinfo.value)
    assert SENTINEL_KEY not in str(excinfo.value.to_dict())


def test_logging_filter_scrubs_key(config, mock_api, caplog):
    mock_api.get("/clients").respond(200, json={"size": 0, "data": []})
    with VRTransport(config) as transport:
        with caplog.at_level(logging.DEBUG, logger="vrcli"):
            logging.getLogger("vrcli.transport").warning("key is %s", SENTINEL_KEY)
        transport.request("GET", "/clients")
    for record in caplog.records:
        assert SENTINEL_KEY not in record.getMessage()


def test_dry_run_headers_redacted(config):
    with VRTransport(config, dry_run=True) as transport:
        described = transport.request(
            "POST", "/artifacts", content="name: X", headers={"X-Api-Key": SENTINEL_KEY}
        )
    assert SENTINEL_KEY not in str(described)
