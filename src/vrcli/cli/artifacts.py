"""`vr artifacts` — 1:1 with the Artifact API."""

from __future__ import annotations

import click


@click.group()
def artifacts() -> None:
    """Inspect and manage artifact definitions."""
