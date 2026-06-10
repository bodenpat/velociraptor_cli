"""`vr flows` — 1:1 with the Flow API."""

from __future__ import annotations

import click


@click.group()
def flows() -> None:
    """Create and manage per-client collections."""
