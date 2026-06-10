"""`vr ops` — investigation-grade composites InsightConnect calls (PLAN.md §4.2).

Each composite lives in its own ops_* module and exports COMMANDS; this
module only assembles the group.
"""

from __future__ import annotations

import click


@click.group()
def ops() -> None:
    """Composite SOAR operations (triage, live response, IOC hunts, containment)."""


def _register() -> None:
    from . import ops_contain, ops_enrich, ops_ioc_hunt, ops_live, ops_triage

    for module in (ops_triage, ops_live, ops_ioc_hunt, ops_contain, ops_enrich):
        for command in module.COMMANDS:
            ops.add_command(command)


_register()
