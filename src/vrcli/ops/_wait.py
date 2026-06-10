"""Poll loops for --wait (PLAN.md §3: exit 5 on timeout).

Flow lifecycle: the flow context's `state` is RUNNING until the collection
finishes (FINISHED) or fails (ERROR). Hunts have no single terminal state —
they run until expiry — so hunt completion is a heuristic: STOPPED, or
stats.stopped, or every scheduled client has reported a result or an error.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from ..errors import APIError, WaitTimeout
from ..transport import VRTransport

logger = logging.getLogger("vrcli.ops.wait")

FLOW_TERMINAL_OK = frozenset({"FINISHED"})
FLOW_TERMINAL_FAILED = frozenset({"ERROR"})


def wait_until(
    check: Callable[[], tuple[bool, Any]],
    *,
    timeout: int,
    poll_interval: int,
    describe: str = "operation",
) -> Any:
    """Run check() until it returns (True, value) or `timeout` elapses.

    The clock is monotonic; the first check happens immediately so a fast
    operation never waits a full interval.
    """
    deadline = time.monotonic() + timeout
    while True:
        done, value = check()
        if done:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitTimeout(f"Timed out after {timeout}s waiting for {describe}")
        logger.info("waiting for %s (%.0fs remaining)", describe, remaining)
        time.sleep(min(poll_interval, max(remaining, 0)))


def flow_state(flow: dict) -> str:
    return str(flow.get("state", "")).upper()


def check_flow(transport: VRTransport, client_id: str, flow_id: str) -> tuple[bool, dict]:
    """One poll of a flow. Raises APIError (exit 6) if the flow ended in ERROR."""
    from ..api import flows as flows_api

    flow = flows_api.get_flow(transport, client_id, flow_id)
    state = flow_state(flow)
    if state in FLOW_TERMINAL_FAILED:
        raise APIError(
            f"Flow {flow_id} on {client_id} ended in ERROR: {flow.get('status', '')}",
            detail={"flow_id": flow_id, "client_id": client_id, "state": state},
        )
    return state in FLOW_TERMINAL_OK, flow


def wait_for_flow(
    transport: VRTransport,
    client_id: str,
    flow_id: str,
    *,
    timeout: int,
    poll_interval: int,
) -> dict:
    return wait_until(
        lambda: check_flow(transport, client_id, flow_id),
        timeout=timeout,
        poll_interval=poll_interval,
        describe=f"flow {flow_id} on {client_id}",
    )


def hunt_complete(hunt: dict) -> bool:
    """Heuristic hunt completion — see module docstring."""
    if str(hunt.get("state", "")).upper() in {"STOPPED", "ARCHIVED"}:
        return True
    stats = hunt.get("stats") or {}
    if stats.get("stopped"):
        return True
    scheduled = int(stats.get("total_clients_scheduled") or 0)
    if scheduled <= 0:
        return False
    done = int(stats.get("total_clients_with_results") or 0) + int(
        stats.get("total_clients_with_errors") or 0
    )
    return done >= scheduled


def wait_for_hunt(
    transport: VRTransport,
    hunt_id: str,
    *,
    timeout: int,
    poll_interval: int,
) -> dict:
    from ..api import hunts as hunts_api

    def check() -> tuple[bool, dict]:
        hunt = hunts_api.get_hunt(transport, hunt_id)
        return hunt_complete(hunt), hunt

    return wait_until(
        check,
        timeout=timeout,
        poll_interval=poll_interval,
        describe=f"hunt {hunt_id}",
    )
