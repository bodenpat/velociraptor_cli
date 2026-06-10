"""Host containment composites: `vr ops contain` / `vr ops release` (PLAN.md §4.2 rows 4-5).

Containment = tag the asset with an investigation label so analysts and SOAR
scoping see it immediately; with quarantine, additionally launch the
Windows.Remediation.Quarantine artifact and mark the client with the
`vr-quarantined` label so release() can detect (and undo) quarantine state.

Quarantine guardrail (PLAN.md §9.7): quarantine is refused CLIENT-SIDE,
before any mutating request is sent, when the client carries any label in
the deny set — the built-ins {critical-infrastructure, no-quarantine}, plus
the comma-separated $R7_VR_QUARANTINE_DENY_LABELS, plus any --deny-label
values. Matching is case-insensitive. SOAR + quarantine is where automation
accidents hurt most.

Spec operations used: getLabels, addLabel, deleteLabel, createFlow,
getClientFlow.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

from ..api import flows as flows_api
from ..api import labels as labels_api
from ..api.clients import resolve_client_arg
from ..errors import UsageError
from ..transport import VRTransport
from ._audit import write_audit

QUARANTINE_ARTIFACT = "Windows.Remediation.Quarantine"
QUARANTINE_MARKER_LABEL = "vr-quarantined"
DEFAULT_CONTAIN_LABEL = "under-investigation"
BUILTIN_DENY_LABELS = frozenset({"critical-infrastructure", "no-quarantine"})
ENV_DENY_LABELS = "R7_VR_QUARANTINE_DENY_LABELS"


def quarantine_deny_set(extra: Iterable[str] = ()) -> set[str]:
    """The effective quarantine deny set, lowercased (PLAN.md §9.7).

    Built-in labels ∪ comma-split $R7_VR_QUARANTINE_DENY_LABELS ∪ `extra`.
    """
    deny = set(BUILTIN_DENY_LABELS)
    env_value = os.environ.get(ENV_DENY_LABELS, "")
    deny.update(part.strip().lower() for part in env_value.split(",") if part.strip())
    deny.update(str(label).strip().lower() for label in extra if str(label).strip())
    return deny


def _check_quarantine_guardrail(
    client_id: str, current_labels: list, extra_deny: Iterable[str]
) -> None:
    """Raise UsageError (exit 2) if any current label is in the deny set.

    Runs entirely client-side, before any mutating request is sent.
    """
    deny = quarantine_deny_set(extra_deny)
    matched = [str(label) for label in current_labels if str(label).strip().lower() in deny]
    if matched:
        raise UsageError(
            f"Refusing to quarantine {client_id}: client carries deny "
            f"label(s) {', '.join(repr(m) for m in matched)} "
            f"(quarantine guardrail, PLAN.md §9.7)"
        )


def _quarantine_body(*, remove: bool = False) -> dict:
    """ArtifactCollectorArgs for Windows.Remediation.Quarantine (spec: createFlow).

    The artifact name and its RemovePolicy parameter come from the
    open-source Velociraptor artifact library, not the REST spec, and
    Velociraptor boolean artifact parameters are the strings "Y"/"N" —
    verify both against the tenant artifact in Phase 3.
    """
    if not remove:
        return {"artifacts": [QUARANTINE_ARTIFACT], "specs": []}
    return {
        "artifacts": [QUARANTINE_ARTIFACT],
        "specs": [
            {
                "artifact": QUARANTINE_ARTIFACT,
                "parameters": {"env": [{"key": "RemovePolicy", "value": "Y"}]},
            }
        ],
    }


def _matching(labels: list, name: str) -> list[str]:
    """Labels equal to `name` case-insensitively, original casing preserved."""
    wanted = name.strip().lower()
    return [str(label) for label in labels if str(label).strip().lower() == wanted]


def contain(
    transport: VRTransport,
    host: str,
    *,
    label: str = DEFAULT_CONTAIN_LABEL,
    quarantine: bool = False,
    extra_deny_labels: Sequence[str] = (),
) -> dict:
    """Label a host for investigation; optionally quarantine it.

    Resolves `host` (client ID or hostname), fetches its current labels
    (spec: getLabels), enforces the quarantine guardrail (only with
    quarantine=True, client-side, before any mutation), adds the containment
    label (spec: addLabel), and with quarantine=True creates a
    Windows.Remediation.Quarantine flow (spec: createFlow), verifies it
    scheduled (spec: getClientFlow), and adds the QUARANTINE_MARKER_LABEL.

    With a dry-run transport the mutating requests are described instead of
    sent: returns {"dry_run": True, "requests": [...]} (the guardrail is
    still enforced so a dry run surfaces the refusal).
    """
    client_id = resolve_client_arg(transport, host)
    current = labels_api.get_labels(transport, client_id) or []
    if quarantine:
        _check_quarantine_guardrail(client_id, current, extra_deny_labels)

    if transport.dry_run:
        requests = [labels_api.add_label(transport, client_id, label)]
        if quarantine:
            requests.append(flows_api.create_flow(transport, client_id, _quarantine_body()))
            requests.append(labels_api.add_label(transport, client_id, QUARANTINE_MARKER_LABEL))
        write_audit(
            "ops contain",
            out_dir=None,
            created={"client_id": client_id},
            extra={"dry_run": True},
        )
        return {"dry_run": True, "client_id": client_id, "host": host, "requests": requests}

    labels_api.add_label(transport, client_id, label)
    labels_added = [label]
    quarantine_info = None
    if quarantine:
        flow_id = flows_api.create_flow(transport, client_id, _quarantine_body())
        flow = flows_api.get_flow(transport, client_id, flow_id)
        state = str(flow.get("state", "")).upper() if isinstance(flow, dict) else ""
        labels_api.add_label(transport, client_id, QUARANTINE_MARKER_LABEL)
        labels_added.append(QUARANTINE_MARKER_LABEL)
        quarantine_info = {"flow_id": flow_id, "state": state}

    created = {"client_id": client_id}
    if quarantine_info is not None:
        created["quarantine_flow_id"] = quarantine_info["flow_id"]
    write_audit("ops contain", out_dir=None, created=created)
    return {
        "client_id": client_id,
        "host": host,
        "labels_added": labels_added,
        "quarantine": quarantine_info,
    }


def release(
    transport: VRTransport,
    host: str,
    *,
    label: str = DEFAULT_CONTAIN_LABEL,
) -> dict:
    """Undo contain(): remove the containment label and unquarantine if marked.

    Resolves `host`, fetches its labels (spec: getLabels), and removes the
    containment label if present (spec: deleteLabel; case-insensitive match,
    actual label casing used in the request). If the QUARANTINE_MARKER_LABEL
    is present, creates a Windows.Remediation.Quarantine flow with
    RemovePolicy="Y" (spec: createFlow) and removes the marker. RemovePolicy
    and the "Y"/"N" boolean convention are artifact-library assumptions, not
    REST-spec facts — verify against the tenant artifact in Phase 3.

    With a dry-run transport the mutating requests are described instead of
    sent: returns {"dry_run": True, "requests": [...]}.
    """
    client_id = resolve_client_arg(transport, host)
    current = labels_api.get_labels(transport, client_id) or []
    label_matches = _matching(current, label)
    marker_matches = _matching(current, QUARANTINE_MARKER_LABEL)

    if transport.dry_run:
        requests = [
            labels_api.remove_label(transport, client_id, actual) for actual in label_matches
        ]
        if marker_matches:
            requests.append(
                flows_api.create_flow(transport, client_id, _quarantine_body(remove=True))
            )
            requests += [
                labels_api.remove_label(transport, client_id, actual)
                for actual in marker_matches
            ]
        write_audit(
            "ops release",
            out_dir=None,
            created={"client_id": client_id},
            extra={"dry_run": True},
        )
        return {"dry_run": True, "client_id": client_id, "host": host, "requests": requests}

    labels_removed = []
    for actual in label_matches:
        labels_api.remove_label(transport, client_id, actual)
        labels_removed.append(actual)
    unquarantine = None
    if marker_matches:
        flow_id = flows_api.create_flow(transport, client_id, _quarantine_body(remove=True))
        for actual in marker_matches:
            labels_api.remove_label(transport, client_id, actual)
            labels_removed.append(actual)
        unquarantine = {"flow_id": flow_id}

    created = {"client_id": client_id}
    if unquarantine is not None:
        created["unquarantine_flow_id"] = unquarantine["flow_id"]
    write_audit("ops release", out_dir=None, created=created)
    return {
        "client_id": client_id,
        "host": host,
        "labels_removed": labels_removed,
        "unquarantine": unquarantine,
    }
