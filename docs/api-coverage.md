# API coverage — spec operation ↔ `vr` command map

Source of truth: the vendored OpenAPI contract `spec/velociraptor-api-v1.yaml`
(downloaded 2026-06-10 from docs.rapid7.com). The spec defines **26
operations**; every one is mapped below. `scripts/check_spec_drift.py`
re-downloads the official YAML and diffs it against the vendored copy, so new
or changed Rapid7 endpoints show up as drift — re-verify this table whenever
drift is reported (PLAN.md §6; run monthly and before each release).

Coverage status: **26/26 operations have a `vr` command.** No gaps.

## Primitive coverage (1:1)

### Client API

| Method + Path | operationId | `vr` command |
|---|---|---|
| `GET /clients` | `getClients` | `vr clients list` |
| `GET /clients/{clientId}` | `getClient` | `vr clients get <client_id>` |
| `PUT /clients/{clientId}` | `updateClientMetadata` | `vr clients update <client_id> --add k=v / --remove k` |
| `DELETE /clients/{clientId}` | `deleteClient` | `vr clients delete <client_id> --yes` |

### Label API

| Method + Path | operationId | `vr` command |
|---|---|---|
| `GET /clients/{clientId}/labels` | `getLabels` | `vr labels list <client_id>` |
| `PATCH /clients/{clientId}/labels/{label}` | `addLabel` | `vr labels add <client_id> <label>` |
| `DELETE /clients/{clientId}/labels/{label}` | `deleteLabel` | `vr labels remove <client_id> <label>` |
| `POST /clients/bulk-labels` | `bulkAddLabels` | `vr labels bulk-add <label> --client-id/--from-file/--stdin` |

### Hunt API

| Method + Path | operationId | `vr` command |
|---|---|---|
| `GET /hunts` | `getHunts` | `vr hunts list` |
| `POST /hunts` | `createHunt` | `vr hunts create --artifact A …` |
| `GET /hunts/{huntId}` | `getHunt` | `vr hunts get <hunt_id>` |
| `PATCH /hunts/{huntId}` | `updateHuntStatus` | `vr hunts pause` / `vr hunts resume` / `vr hunts stop --yes` (one endpoint, three verbs via `desiredState`) |
| `GET /hunts/{huntId}/results` | `getHuntResults` | `vr hunts results <hunt_id>` |
| `GET /hunts/{huntId}/errors` | `getHuntErrors` | `vr hunts errors <hunt_id>` |

### Flow API

| Method + Path | operationId | `vr` command |
|---|---|---|
| `GET /clients/{clientId}/flows` | `getClientFlows` | `vr flows list <client_id>` |
| `POST /clients/{clientId}/flows` | `createFlow` | `vr flows create <client_id> --artifact A …` |
| `GET /clients/{clientId}/flows/{flowId}` | `getClientFlow` | `vr flows get <client_id> <flow_id>` |
| `GET /clients/{clientId}/flows/{flowId}/results` | `getAvailableFlowResults` | `vr flows results <client_id> <flow_id>` (without `--artifact`) |
| `GET /clients/{clientId}/flows/{flowId}/results/{artifact}` | `getFlowResults` | `vr flows results <client_id> <flow_id> --artifact NAME` |
| `GET /clients/{clientId}/flows/{flowId}/results/{artifact}/{source}` | `getFlowResults_1` | `vr flows results <client_id> <flow_id> --artifact NAME --source S` |
| `GET /clients/{clientId}/flows/{flowId}/logs` | `getFlowLogs` | `vr flows logs <client_id> <flow_id>` |

### Artifact API

| Method + Path | operationId | `vr` command |
|---|---|---|
| `GET /artifacts` | `listArtifacts` | `vr artifacts list` |
| `GET /artifacts/{name}` | `getArtifact` | `vr artifacts get <name> [--format yaml]` |
| `POST /artifacts` | `addArtifact` | `vr artifacts add --file artifact.yaml` |
| `PATCH /artifacts/{name}` | `modifyArtifact` | `vr artifacts update <name> --file artifact.yaml` |
| `DELETE /artifacts/{name}` | `deleteArtifact` | `vr artifacts delete <name> --yes` |

Helper commands that reuse a primitive rather than add API surface:

| `vr` command | Endpoint reused | Purpose |
|---|---|---|
| `vr status` | `getClients` (`limit=1`) | Auth + connectivity smoke test; SOAR health check. |
| `vr clients resolve <hostname>` | `getClients` (`hostname=` filter) | Hostname → client record; errors on 0 or >1 matches unless `--first`. |
| hostname auto-resolve (any `<client_id>`/HOST argument) | `getClients` | `C.…` IDs pass through; anything else (or an explicit `host:` prefix) resolves via the hostname filter. |

## Composite coverage (`vr ops` → primitive orchestration)

| Composite | Primitive endpoints orchestrated (in order) |
|---|---|
| `vr ops triage <host> --wait` | `getClients` (resolve) → `createFlow` → `getClientFlow` (poll until FINISHED) → `getClientFlow` (final record) → `getAvailableFlowResults` → `getFlowResults` (paged, per artifact) → `getFlowLogs` (paged) |
| `vr ops triage <host> --start` | `getClients` (resolve) → `createFlow` (returns a resume token; nothing else runs) |
| `vr ops triage --check <token>` | `getClientFlow` (one poll); on FINISHED additionally `getClientFlow` + `getAvailableFlowResults` + `getFlowResults` (paged) + `getFlowLogs` (paged) — identical evidence output to `--wait` |
| `vr ops live <host> --preset P` | `getClients` (resolve) → `createFlow` (unioned preset artifacts, one flow) → `getClientFlow` (poll) → `getClientFlow` → `getAvailableFlowResults` → `getFlowResults` (paged) → `getFlowLogs` (paged) |
| `vr ops ioc-hunt …` | `createHunt`; with `--wait` additionally `getHunt` (poll until complete) → `getHuntResults` (all pages) → `getHuntErrors` |
| `vr ops contain <host>` | `getClients` (resolve) → `getLabels` → `addLabel`; with `--quarantine` additionally `createFlow` (Windows.Remediation.Quarantine) → `getClientFlow` (verify scheduled) → `addLabel` (`vr-quarantined` marker). The quarantine deny-label guardrail runs client-side on the `getLabels` result before any mutation. |
| `vr ops release <host>` | `getClients` (resolve) → `getLabels` → `deleteLabel`; if the `vr-quarantined` marker is present, additionally `createFlow` (Quarantine with `RemovePolicy=Y`) → `deleteLabel` (marker) |
| `vr ops enrich <host>` | `getClients` (resolve) → `getClient` → `getLabels` → `getClientFlows` (`sort=NEWEST`) |

Notes on the composites:

- All `--wait`/poll loops obey `--timeout` (exit 5 on expiry) and
  `--poll-interval`; hunt completion is a heuristic (STOPPED/ARCHIVED state,
  `stats.stopped`, or every scheduled client reported) because hunts have no
  single terminal state.
- Artifact parameter names used by the composites (KapeFiles target groups,
  FileFinder `SearchFilesGlob`/`YaraRule`/`Calculate_Hash`, Quarantine
  `RemovePolicy`) come from the open-source Velociraptor artifact library, not
  the REST spec — the spec carries only opaque key/value specs. They are
  flagged in the source for empirical verification against the tenant
  artifacts (PLAN.md §8 Phase 3).

## Known gap: no file/container download (PLAN.md §2)

The REST spec returns collection **result rows** (JSON) but exposes **no
endpoint that downloads uploaded files or collection containers** — e.g. the
ZIP a `Windows.KapeFiles.Targets` triage produces. The `HuntStats` schema
even lists an `available_downloads` field, but nothing in the API serves
those files. A Rapid7 support ticket (filed in Phase 0) is pending; until it
resolves:

- Evidence preservation = result rows + flow logs persisted with SHA-256
  manifests (`vr ops triage` / `vr ops live` do this automatically).
- Raw uploaded files require a GUI download. The library exposes
  `EvidenceDir.add_existing_file()` to hash a manually-downloaded file into
  an existing manifest; a `vr evidence ingest` CLI verb is conditional Phase 3
  scope, **not yet implemented** (PLAN.md §8, §10.1).

This is a Rapid7 API gap, not a `vr` coverage gap — there is no endpoint to
map.
