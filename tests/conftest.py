"""Shared fixtures: a fake tenant config, respx-mocked transport, CLI runner.

SENTINEL_KEY is the canary for redaction tests — it must never appear in any
captured output. BASE is the exact URL the vendored spec's server template
expands to for the fake tenant.
"""

from __future__ import annotations

import json

import pytest
import respx
from click.testing import CliRunner

from vrcli.config import Config, Secret
from vrcli.transport import VRTransport

SENTINEL_KEY = "11111111-2222-3333-4444-555555555555"
REGION = "us"
ORG_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BASE = f"https://{REGION}.api.insight.rapid7.com/insight-velociraptor/v1/orgs/{ORG_ID}"

ENV = {
    "R7_VR_API_KEY": SENTINEL_KEY,
    "R7_VR_REGION": REGION,
    "R7_VR_ORG_ID": ORG_ID,
}


@pytest.fixture
def config() -> Config:
    return Config(api_key=Secret(SENTINEL_KEY), region=REGION, org_id=ORG_ID)


@pytest.fixture
def transport(config) -> VRTransport:
    with VRTransport(config) as t:
        yield t


@pytest.fixture
def mock_api():
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner with the fake tenant env. Use runner.invoke(cli, [...])."""
    return CliRunner(env=ENV)


@pytest.fixture
def invoke(runner, mock_api):
    """invoke(args...) -> (result, parsed_stdout_json). Asserts nothing itself."""
    from vrcli.cli.main import cli

    def _invoke(*args: str, expect_json: bool = True):
        result = runner.invoke(cli, list(args), catch_exceptions=False)
        parsed = None
        if expect_json and result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
            except json.JSONDecodeError:
                parsed = None
        return result, parsed

    return _invoke


def page(data: list, cursor: str | None = None) -> dict:
    """Build a {size, cursor, data} envelope like the API returns."""
    body = {"size": len(data), "data": data}
    if cursor is not None:
        body["cursor"] = cursor
    return body
