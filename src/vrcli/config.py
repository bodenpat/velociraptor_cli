"""Environment-only configuration (PLAN.md §5).

The API key is never accepted as a CLI argument, never read from a config
file inside a repo, and never echoed. Sources, in order:

    R7_VR_API_KEY        the key itself (SOAR credential-store injection)
    R7_VR_API_KEY_FILE   path to a 0600 file containing only the key
                         (systemd LoadCredential / analyst key file)

`R7_VR_REGION` and `R7_VR_ORG_ID` are always required.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import ConfigError

ENV_API_KEY = "R7_VR_API_KEY"  # pragma: allowlist secret — env var *name*, not a secret
ENV_API_KEY_FILE = "R7_VR_API_KEY_FILE"  # pragma: allowlist secret
ENV_REGION = "R7_VR_REGION"
ENV_ORG_ID = "R7_VR_ORG_ID"

# Insight platform region slugs (docs.rapid7.com). Format-validated rather
# than allowlisted so a new Rapid7 region doesn't require a code change;
# KNOWN_REGIONS only feeds the error hint.
KNOWN_REGIONS = ("us", "us2", "us3", "eu", "ca", "au", "ap")
_REGION_RE = re.compile(r"^[a-z]{2}\d?$")
_ORG_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

REDACTED = "***"


class Secret:
    """Wrapper that keeps the API key out of repr/str/logs/tracebacks.

    The raw value is only reachable via .reveal(); everything else —
    str(), repr(), f-strings, pytest assertion output — shows ***.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str):
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        return object.__getattribute__(self, "_value")

    def __setattr__(self, name, value):  # immutable: no second write path
        raise AttributeError("Secret is immutable")

    def __repr__(self) -> str:
        return REDACTED

    def __str__(self) -> str:
        return REDACTED

    def __eq__(self, other) -> bool:
        if isinstance(other, Secret):
            return self.reveal() == other.reveal()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.reveal())

    def __bool__(self) -> bool:
        return bool(self.reveal())

    # Block accidental serialization
    def __reduce__(self):
        raise TypeError("Secret cannot be pickled")


@dataclass(frozen=True)
class Config:
    api_key: Secret
    region: str
    org_id: str
    timeout_connect: float = 10.0
    timeout_read: float = 60.0
    max_retries: int = 4
    extra: dict = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return (
            f"https://{self.region}.api.insight.rapid7.com"
            f"/insight-velociraptor/v1/orgs/{self.org_id}"
        )


def _read_key_file(path: str) -> str:
    """Read the key from a file, refusing group/world-readable files."""
    try:
        st = os.stat(path)
    except OSError as exc:
        raise ConfigError(f"{ENV_API_KEY_FILE} points to an unreadable path: {exc.strerror}") from exc
    if stat.S_ISDIR(st.st_mode):
        raise ConfigError(f"{ENV_API_KEY_FILE} points to a directory, not a file")
    if st.st_mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
        raise ConfigError(
            f"{ENV_API_KEY_FILE} is group/world-accessible; refusing to read it. "
            f"Fix with: chmod 600 <file>"
        )
    with open(path, encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key:
        raise ConfigError(f"{ENV_API_KEY_FILE} file is empty")
    if "\n" in key:
        raise ConfigError(f"{ENV_API_KEY_FILE} file must contain only the key on one line")
    return key


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from environment variables. Raises ConfigError (exit 2)."""
    env = os.environ if env is None else env

    key = (env.get(ENV_API_KEY) or "").strip()
    key_file = (env.get(ENV_API_KEY_FILE) or "").strip()
    if key and key_file:
        raise ConfigError(f"Set {ENV_API_KEY} or {ENV_API_KEY_FILE}, not both")
    if not key and key_file:
        key = _read_key_file(key_file)
    if not key:
        raise ConfigError(
            f"No API key: set {ENV_API_KEY} (or {ENV_API_KEY_FILE} pointing to a 0600 key file)"
        )

    region = (env.get(ENV_REGION) or "").strip().lower()
    if not region:
        raise ConfigError(f"{ENV_REGION} is required (e.g. {', '.join(KNOWN_REGIONS)})")
    if not _REGION_RE.match(region):
        raise ConfigError(
            f"{ENV_REGION}={region!r} does not look like an Insight region slug "
            f"(expected e.g. {', '.join(KNOWN_REGIONS)})"
        )

    org_id = (env.get(ENV_ORG_ID) or "").strip()
    if not org_id:
        raise ConfigError(f"{ENV_ORG_ID} is required (your Rapid7 organization ID)")
    if not _ORG_ID_RE.match(org_id):
        raise ConfigError(f"{ENV_ORG_ID} does not look like a Rapid7 org ID (UUID)")

    return Config(api_key=Secret(key), region=region, org_id=org_id.lower())
