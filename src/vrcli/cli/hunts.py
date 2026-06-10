"""`vr hunts` — 1:1 with the Hunt API."""

from __future__ import annotations

import click


@click.group()
def hunts() -> None:
    """Create and manage fleet-wide hunts."""
