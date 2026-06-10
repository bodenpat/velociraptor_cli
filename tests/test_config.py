"""config.py: env loading, Secret redaction, key-file permission checks."""

from __future__ import annotations

import pytest

from vrcli.config import Config, Secret, load_config
from vrcli.errors import ConfigError

from .conftest import ENV, ORG_ID, SENTINEL_KEY


def test_load_config_happy_path():
    cfg = load_config(ENV)
    assert cfg.api_key.reveal() == SENTINEL_KEY
    assert cfg.region == "us"
    assert cfg.org_id == ORG_ID
    assert cfg.base_url == (
        f"https://us.api.insight.rapid7.com/insight-velociraptor/v1/orgs/{ORG_ID}"
    )


@pytest.mark.parametrize("missing", ["R7_VR_API_KEY", "R7_VR_REGION", "R7_VR_ORG_ID"])
def test_missing_required_env_raises(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(ConfigError):
        load_config(env)


@pytest.mark.parametrize("region", ["US East", "useast1", "Ω", ""])
def test_bad_region_rejected(region):
    with pytest.raises(ConfigError):
        load_config({**ENV, "R7_VR_REGION": region})


def test_bad_org_id_rejected():
    with pytest.raises(ConfigError):
        load_config({**ENV, "R7_VR_ORG_ID": "not-a-uuid"})


def test_both_key_sources_rejected(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text(SENTINEL_KEY)
    key_file.chmod(0o600)
    with pytest.raises(ConfigError, match="not both"):
        load_config({**ENV, "R7_VR_API_KEY_FILE": str(key_file)})


def test_key_file_happy_path(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text(SENTINEL_KEY + "\n")
    key_file.chmod(0o600)
    env = {k: v for k, v in ENV.items() if k != "R7_VR_API_KEY"}
    cfg = load_config({**env, "R7_VR_API_KEY_FILE": str(key_file)})
    assert cfg.api_key.reveal() == SENTINEL_KEY


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o604])
def test_key_file_loose_permissions_refused(tmp_path, mode):
    key_file = tmp_path / "key"
    key_file.write_text(SENTINEL_KEY)
    key_file.chmod(mode)
    env = {k: v for k, v in ENV.items() if k != "R7_VR_API_KEY"}
    with pytest.raises(ConfigError, match="group/world"):
        load_config({**env, "R7_VR_API_KEY_FILE": str(key_file)})


def test_key_file_missing(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "R7_VR_API_KEY"}
    with pytest.raises(ConfigError):
        load_config({**env, "R7_VR_API_KEY_FILE": str(tmp_path / "nope")})


# -- Secret wrapper ---------------------------------------------------------


def test_secret_never_prints_value():
    secret = Secret(SENTINEL_KEY)
    for rendered in (str(secret), repr(secret), f"{secret}", f"{secret!r}", str([secret]), str({"k": secret})):
        assert SENTINEL_KEY not in rendered
        assert "***" in rendered
    assert secret.reveal() == SENTINEL_KEY


def test_secret_not_in_config_repr():
    cfg = Config(api_key=Secret(SENTINEL_KEY), region="us", org_id=ORG_ID)
    assert SENTINEL_KEY not in repr(cfg)
    assert SENTINEL_KEY not in str(cfg)


def test_secret_immutable_and_unpicklable():
    import pickle

    secret = Secret(SENTINEL_KEY)
    with pytest.raises(AttributeError):
        secret._value = "other"  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        pickle.dumps(secret)


def test_secret_equality():
    assert Secret("a") == Secret("a")
    assert Secret("a") != Secret("b")
