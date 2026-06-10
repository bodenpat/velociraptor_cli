"""`vr labels` — 1:1 with the Label API."""

from __future__ import annotations

import click


@click.group()
def labels() -> None:
    """Manage client labels."""
