# PLAN.md — Rapid7 Velociraptor REST API CLI Toolkit

**Project:** `velociraptor_cli` — a Python library + CLI for the Rapid7-hosted Velociraptor REST API, built for Rapid7 InsightConnect (SOAR) evidence-collection automation and interactive analyst use.

**Status:** v1.1 (2026-06-10). Decisions below were confirmed in the project kickoff interview. Revised same day after an /office-hours review: phases resequenced vertical-slice-first; full API coverage remains in scope — **only sequencing changed**, not scope. Design doc: `~/.gstack/projects/bodenpat-velociraptor_cli/pboden-main-design-20260610-173112.md`.

---

## 1. Confirmed scope and decisions

| Decision | Choice |
|---|---|
| API surface | **REST only** — the Rapid7-hosted (paid) Velociraptor API. The open-source gRPC API (`pyvelociraptor`) is explicitly out of scope. |
| Architecture | **Reusable Python library with a CLI on top.** SOAR calls the CLI first; the same library gets wrapped as a native InsightConnect plugin in a later phase. |
| Secrets | **Environment variables only.** InsightConnect credential store injects the key into the orchestrator at runtime; analysts in WSL export the same env vars manually. No secrets in files inside the repo, ever. |
| Runtime | Dedicated automation host (InsightConnect orchestrator / jump box) + analyst workstations (WSL). |
| v1 use cases | Triage collection, targeted live response, fleet-wide IOC hunts, host containment/labeling. |
| Docs | Markdown in repo + **auto-generated CLI reference** that cannot drift from the code. |
| CI | **Pre-commit hooks only** (no CI runner available). Secret scanning and lint run locally on every commit. |

---

## 2. Verified API facts (source: official OpenAPI 3.1 spec)

Downloaded 2026-06-10 from `https://docs.rapid7.com/_api/velociraptor-api-v1.yaml` (linked from https://docs.rapid7.com/insightidr/api/velociraptor/). A copy is vendored at `spec/velociraptor-api-v1.yaml` and treated as the contract.

- **Base URL:** `https://{region}.api.insight.rapid7.com/insight-velociraptor/v1/orgs/{orgId}`
  (`region` is your Insight platform region, e.g. `us`, `us2`, `eu`, `ca`, `au`, `ap`; `orgId` is your Rapid7 organization ID.)
- **Auth:** API key in the `X-Api-Key` request header (Rapid7 Insight platform API key).
- **Pagination:** cursor-based (`limit` + `cursor` query params; responses return `size`, `cursor`, `data`).

### Full endpoint inventory (26 operations)

| Group | Method + Path | Purpose |
|---|---|---|
| Client | `GET /clients` | List clients; filters: `hostname`, `os`, `label`, `status` (ALL/ONLINE/OFFLINE), `limit`, `cursor` |
| Client | `GET /clients/{clientId}` | Get one client |
| Client | `PUT /clients/{clientId}` | Update client metadata (`add` / `remove` keys) |
| Client | `DELETE /clients/{clientId}` | Delete a client |
| Label | `GET /clients/{clientId}/labels` | List labels on a client |
| Label | `PATCH /clients/{clientId}/labels/{label}` | Add a label |
| Label | `DELETE /clients/{clientId}/labels/{label}` | Remove a label |
| Label | `POST /clients/bulk-labels` | Add one label to many clients (`client_ids[]`, `label`) |
| Hunt | `GET /hunts` | List hunts; filters: `state` (UNSET/PAUSED/RUNNING/STOPPED/ARCHIVED/DELETED), `sort` (NEWEST/OLDEST), paging |
| Hunt | `POST /hunts` | Create a hunt (`Hunt` schema: `start_request` = ArtifactCollectorArgs, `condition` = include/exclude labels + OS, `client_limit`, `expires`, `hunt_description`, `tags`) |
| Hunt | `GET /hunts/{huntId}` | Hunt details + stats (clients scheduled / with results / with errors) |
| Hunt | `PATCH /hunts/{huntId}?desiredState=` | Change hunt state (PAUSED/RUNNING/…, default RUNNING) |
| Hunt | `GET /hunts/{huntId}/results` | Paged hunt results |
| Hunt | `GET /hunts/{huntId}/errors` | Per-client hunt errors |
| Flow | `GET /clients/{clientId}/flows` | List flows for a client; filters: `creator`, `sort`, paging |
| Flow | `POST /clients/{clientId}/flows` | Start a collection (`ArtifactCollectorArgs`: `artifacts[]`, `specs[]` with per-artifact parameters, `timeout`, `cpu_limit`, `max_upload_bytes`, `urgent`, …) |
| Flow | `GET /clients/{clientId}/flows/{flowId}` | Flow status/details |
| Flow | `GET /clients/{clientId}/flows/{flowId}/results` | List which artifact result sets exist |
| Flow | `GET /clients/{clientId}/flows/{flowId}/results/{artifact}` | Get result rows for an artifact |
| Flow | `GET /clients/{clientId}/flows/{flowId}/results/{artifact}/{source}` | Result rows for a specific source |
| Flow | `GET /clients/{clientId}/flows/{flowId}/logs` | Flow execution logs |
| Artifact | `GET /artifacts` | List artifacts; filters: `type` (CLIENT/CLIENT_EVENT/SERVER/SERVER_EVENT/NOTEBOOK), `os` (WINDOWS/LINUX/DARWIN), `includeBuiltIn`, `includeCustom` |
| Artifact | `GET /artifacts/{name}` | Get an artifact definition |
| Artifact | `POST /artifacts` | Add a custom artifact (JSON **or** YAML body) |
| Artifact | `PATCH /artifacts/{name}` | Modify an artifact (JSON or YAML) |
| Artifact | `DELETE /artifacts/{name}` | Remove an artifact |

### Known gap to verify with Rapid7

The REST spec returns collection **result rows** (JSON) but exposes **no endpoint to download uploaded files / collection containers** (e.g., the ZIP a `Windows.KapeFiles.Targets` triage produces — `HuntStats.available_downloads` lists files but nothing serves them). Action item in Phase 1: confirm with Rapid7 support/TAM whether file retrieval is exposed elsewhere; until then, evidence preservation = persisting result rows + logs with integrity manifests, and triage collections may need GUI download for the raw files.

---

## 3. Architecture

```
velociraptor_cli/
├── pyproject.toml              # package: vrcli, console script: vr
├── README.md
├── SECURITY.md                 # secret handling, key rotation, reporting
├── CHANGELOG.md                # Keep-a-Changelog format
├── .gitignore                  # .env, *.key, evidence output dirs, etc.
├── .pre-commit-config.yaml     # gitleaks, detect-secrets, ruff, docs-freshness
├── .secrets.baseline           # detect-secrets baseline
├── spec/
│   └── velociraptor-api-v1.yaml    # vendored OpenAPI contract
├── src/vrcli/
│   ├── config.py               # env-var loading, Secret wrapper, region/org validation
│   ├── transport.py            # httpx client: auth header, retries/backoff, redaction
│   ├── errors.py               # typed exceptions → CLI exit codes
│   ├── pagination.py           # cursor iterator (`--all` support)
│   ├── api/                    # one module per API group (pure library, no CLI deps)
│   │   ├── clients.py  labels.py  hunts.py  flows.py  artifacts.py
│   ├── ops/                    # composite SOAR operations built on api/
│   │   ├── triage.py  live_response.py  ioc_hunt.py  containment.py
│   └── cli/                    # click command tree; thin layer over api/ and ops/
├── tests/                      # pytest + respx (mocked httpx), golden fixtures
├── docs/
│   ├── installation.md  configuration.md  security.md
│   ├── cli-reference.md        # AUTO-GENERATED — never hand-edited
│   ├── soar-playbooks.md       # InsightConnect integration recipes
│   └── api-coverage.md         # endpoint ↔ command mapping table
└── scripts/
    ├── gen_cli_docs.py         # walks the click tree → docs/cli-reference.md
    └── check_spec_drift.py     # re-downloads spec, diffs against spec/, reports drift
```

**Stack (deliberately minimal for supply-chain security):**

- Python ≥ 3.10 (WSL Ubuntu default and automation-host friendly)
- `httpx` — HTTP client (explicit timeouts, event hooks for redaction)
- `click` — CLI framework (mature, tiny dependency tree)
- `PyYAML` — only for artifact YAML handling
- Dev-only: `pytest`, `respx`, `ruff`, `pre-commit`, `pip-tools`
- **No other runtime dependencies.** Models are dataclasses; validation is explicit. Every new dependency requires a written justification in the PR.

**Library/CLI contract for SOAR:**

- stdout = **JSON only** (machine-parseable, one document per invocation; `--output table` opt-in for humans)
- stderr = logs/progress (never secrets)
- Exit codes: `0` success · `2` usage error · `3` auth/permission · `4` not found · `5` wait-timeout · `6` API error (4xx/5xx) · `7` network/TLS
- Long-running verbs support `--wait --timeout <sec> --poll-interval <sec>` so a single SOAR step can block until a flow/hunt completes.

---

## 4. CLI command catalog

Console script: **`vr`**. Every endpoint gets a 1:1 command; `vr ops` adds investigation-grade composites.

### 4.1 Primitives (1:1 with the API)

```
vr clients list      [--hostname H] [--os OS] [--label L] [--status online|offline|all] [--limit N] [--all]
vr clients get       <client_id>
vr clients resolve   <hostname>                  # helper: hostname → client_id (errors on 0 or >1 match unless --first)
vr clients update    <client_id> --add k=v ... [--remove k ...]
vr clients delete    <client_id> --yes

vr labels list       <client_id>
vr labels add        <client_id> <label>
vr labels remove     <client_id> <label>
vr labels bulk-add   <label> --client-id ID ... | --from-file ids.txt | --stdin

vr hunts list        [--state running|paused|stopped|archived] [--sort newest|oldest] [--limit N] [--all]
vr hunts create      --artifact A [--param A:key=val ...] [--label L ...] [--exclude-label L ...]
                     [--os windows|linux|darwin] [--client-limit N] [--expires 7d]
                     [--description TEXT] [--tag T ...] [--wait]
vr hunts get         <hunt_id>
vr hunts pause       <hunt_id>            # PATCH desiredState=PAUSED
vr hunts resume      <hunt_id>            # PATCH desiredState=RUNNING
vr hunts stop        <hunt_id> --yes
vr hunts results     <hunt_id> [--all] [--out FILE]
vr hunts errors      <hunt_id>

vr flows list        <client_id> [--creator C] [--sort newest|oldest] [--limit N]
vr flows create      <client_id> --artifact A [--param A:key=val ...]
                     [--timeout SEC] [--cpu-limit PCT] [--max-upload-bytes N] [--urgent] [--wait]
vr flows get         <client_id> <flow_id>
vr flows results     <client_id> <flow_id> [--artifact NAME [--source S]] [--all] [--out FILE]
vr flows logs        <client_id> <flow_id>

vr artifacts list    [--type client|client_event|server|server_event|notebook]
                     [--os windows|linux|darwin] [--builtin/--no-builtin] [--custom/--no-custom]
vr artifacts get     <name> [--format yaml|json]
vr artifacts add     --file artifact.yaml
vr artifacts update  <name> --file artifact.yaml
vr artifacts delete  <name> --yes

vr status                                  # auth + connectivity smoke test (1 cheap GET); SOAR health check
```

Conventions: hostnames are accepted anywhere a `<client_id>` is (auto-resolve, prefix `host:`); destructive verbs require `--yes`; `--param` uses `artifact:key=value` so multi-artifact collections stay unambiguous.

### 4.2 SOAR composites (`vr ops`) — the investigation verbs InsightConnect calls

| Command | What it does (under the hood) |
|---|---|
| `vr ops triage <host> [--targets KapeTriage] [--out DIR] [--wait]` | Resolve host → start `Windows.KapeFiles.Targets` flow → poll → write all result rows + flow logs to `DIR` with a SHA-256 evidence manifest. |
| `vr ops live <host> --preset pslist\|netstat\|persistence\|tasks\|browser-history\|users [--out DIR]` | Curated artifact presets for targeted live response (e.g., `Windows.System.Pslist`, `Windows.Network.Netstat`, `Windows.Sys.Programs`); collect, wait, persist. |
| `vr ops ioc-hunt --hash SHA256 ... \| --filename GLOB \| --yara FILE [--label SCOPE] [--os windows] [--expires 7d] [--wait]` | Build the right hunt (e.g., `Windows.Search.FileFinder` / YARA artifacts) with label/OS conditions, launch fleet-wide, optionally block until complete, emit per-client hit summary. |
| `vr ops contain <host> [--label under-investigation] [--quarantine]` | Tag the asset with the investigation label; with `--quarantine`, additionally launch `Windows.Remediation.Quarantine` and verify it scheduled. |
| `vr ops release <host>` | Remove containment labels and (if quarantined) run the unquarantine flow. |
| `vr ops enrich <host>` | One-shot enrichment blob for an InsightIDR investigation: client info + labels + last-seen + recent flows, single JSON document. |

Every `ops` command writes an **audit record** (JSONL: timestamp, operator/env identity, command, args with secrets stripped, hunt/flow IDs created) and an **evidence manifest** (file list + SHA-256 + collection timestamps) — chain-of-custody by default.

---

## 5. Secret handling (zero-leak design)

**Configuration is environment-only. There is no config file containing the key, no `--api-key` flag, and no interactive prompt that echoes.**

| Variable | Meaning |
|---|---|
| `R7_VR_API_KEY` | Insight platform API key (required) |
| `R7_VR_REGION` | Region slug, e.g. `us`, `eu` (required) |
| `R7_VR_ORG_ID` | Rapid7 org ID (required) |
| `R7_VR_API_KEY_FILE` | Optional alternative: path to a `0600` file containing only the key (for systemd `LoadCredential` / SOAR runners that prefer file injection). Refused if world/group-readable. |

Defense-in-depth measures, all implemented in Phase 1 and tested:

1. **No argv exposure.** The key can never be passed as a CLI argument (visible in `ps`, shell history, SOAR job logs). The flag simply does not exist.
2. **Redaction everywhere.** The key lives in a `Secret` wrapper whose `repr`/`str` is `***`; an httpx event hook + logging filter scrub `X-Api-Key` from any debug output, exception, traceback, or `--verbose` dump. A unit test greps captured output for the test key and fails on any hit.
3. **TLS always.** Certificate verification cannot be disabled by flag. Corporate proxy CA bundles are supported only via the standard `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` env vars.
4. **Repo hygiene (pre-commit, blocking):** `gitleaks` + `detect-secrets` on every commit; `.gitignore` ships with `.env*`, `*.key`, `evidence/`, and audit-log paths pre-blocked; a hook rejects any file matching the Insight API key format outright.
5. **Supply chain:** dependencies pinned with hashes via `pip-tools --generate-hashes`; `pip-audit` run by a pre-push hook; minimal dependency policy (see §3).
6. **Runtime delivery:**
   - *SOAR:* the key lives in the InsightConnect credential store and is injected into the orchestrator step as `R7_VR_API_KEY` at execution time — never written to disk.
   - *WSL analysts:* documented pattern is a `0600` key file outside any repo (e.g. `~/.config/r7vr/key`, referenced by `R7_VR_API_KEY_FILE`) or `export R7_VR_API_KEY=$(cat ...)` in the session — explicitly **not** hard-coded in `.bashrc` and never inside a git working tree.
7. **SECURITY.md** documents key scoping (a dedicated service account/key for SOAR, separate from analyst keys), a rotation runbook, and incident steps if a key is exposed.

---

## 6. Documentation strategy (cannot go stale)

- `docs/cli-reference.md` is **generated** by `scripts/gen_cli_docs.py` from the live click command tree. A pre-commit hook regenerates it and **fails the commit if the file changed**, so the reference is always in sync with the code on every commit.
- `docs/api-coverage.md` maps every spec operation to its `vr` command; `scripts/check_spec_drift.py` re-downloads the official OpenAPI YAML, diffs it against `spec/`, and reports new/changed endpoints (run monthly and before each release — this is how we notice Rapid7 shipping new API surface). **Owner: TBD — a named person must be assigned before Phase 1 exit; "the team" does not count.**
- `docs/soar-playbooks.md` holds copy-pasteable InsightConnect recipes: triage-on-alert, IOC sweep from a threat-intel feed, contain-on-detection, and the JSON each step returns.
- `CHANGELOG.md` (Keep a Changelog + SemVer) is updated in the same PR as any behavior change; releases are git tags.
- `README.md` covers install (pipx from the internal git remote), env-var setup for both SOAR and WSL, and a 5-minute quickstart.

---

## 7. Testing

- **Unit/contract:** pytest + respx; every API module tested against request/response fixtures derived from the vendored spec (URLs, headers, query params, body shapes asserted exactly).
- **Redaction tests:** inject a sentinel key, capture all logging/exception output across failure modes, assert zero occurrences.
- **Pagination/wait-loop tests:** cursor iteration, timeout behavior, exit codes.
- **Smoke test:** `vr status` against the live tenant (manual, documented) validates auth, region, and orgId before SOAR rollout.

---

## 8. Phases

**Phase 0 — Security baseline, scaffolding & orchestrator spike** (do first, before any code)
Repo layout, `pyproject.toml`, pre-commit with gitleaks/detect-secrets/ruff, `.gitignore`, `SECURITY.md`, vendored spec. **File the Rapid7 file-download ticket now** (§2 gap — longest lead-time item; owner: whoever holds the Rapid7 account relationship). **Orchestrator spike** (half a day, zero code): prove the InsightConnect orchestrator can execute a local CLI command with an env-injected secret and capture its JSON output, and in the same visit measure the orchestrator's maximum step duration. The exec capability is the load-bearing assumption of the CLI-first SOAR strategy — if it fails, only the SOAR delivery path pivots to the native plugin (Phase 5); the CLI remains fully valuable for WSL analysts. *Acceptance: committing a fake API key is blocked by hooks; spike results documented.*

**Phase 1 — Core library**
`config.py`, `Secret` wrapper, `transport.py` (auth, timeouts, retry w/ exponential backoff + jitter on 429/5xx, redaction hooks, `--dry-run` plumbing), `errors.py`, `pagination.py`, `vr status`. *Acceptance: `vr status` succeeds against the tenant; redaction tests pass.*

**Phase 2 — Triage vertical slice**
One verb end-to-end: `vr ops triage` plus exactly the 7 endpoints it needs (clients list, client get, flow create, flow get, flow list-results, flow results-per-artifact, flow logs — the per-source results variant stays in the Phase 3 backfill), `--wait` polling, evidence directory + SHA-256 manifest, audit JSONL, KapeFiles preset, `--dry-run` on flow create. Long-collection fallback contract (specified now, **built only if the Phase 0 spike showed step-duration caps below worst-case collection time**): `vr ops triage --start` returns a resume token encoding client ID, flow ID, and output directory; `vr ops triage --check <token>` polls, and the terminal `--check` writes the evidence directory and manifest exactly as `--wait` would. *Acceptance: one InsightConnect workflow step runs `vr ops triage <host> --wait` against a lab host enrolled in the production tenant and produces the evidence directory with exit code 0 — no GUI involvement.*

**Phase 3 — Remaining verbs + full API coverage**
The other `vr ops` composites (`live`, `ioc-hunt`, `contain`/`release`, `enrich`), backfilling primitives and endpoints verb-by-verb until 26/26; tail endpoints serving no current persona directly (artifact CRUD, client delete) land last. Verify hunt `desiredState` lifecycle semantics empirically here. **Conditional scope** (only if the Rapid7 ticket resolves "no file download, and not on the roadmap"): `vr evidence ingest <file>` — copy a manually-downloaded file into the evidence directory, add its SHA-256 to the manifest, append an audit JSONL entry recording operator and source. *Acceptance: 26/26 spec operations mapped in `docs/api-coverage.md`; `--dry-run` present on every mutating command.*

**Phase 4 — SOAR rollout & playbooks**
InsightConnect workflows for the four v1 use cases using the CLI on the orchestrator host; `docs/soar-playbooks.md`; analyst WSL install guide; tabletop a real investigation with the tooling. *Acceptance: triage-on-alert workflow runs in production; post-rollout review scheduled with a named owner.* **Post-rollout review (rows-only evidence checkpoint):** for the first 3 production incidents using `vr ops triage` (or 60 days post-rollout, whichever comes first), the incident postmortem checklist gains one item — "Did the analyst open the hosted GUI to retrieve file content for this incident? (yes/no, what for)". Decision rule: any GUI fallback → rows-only evidence is falsified, escalate the Rapid7 file-download ticket to blocking for declaring triage fully automated and retiring the GUI-hybrid step; fewer than 3 incidents with zero fallback at day 60 → extend the window once by 60 days, then decide at the next quarterly review.

**Phase 5 (future) — Native InsightConnect plugin**
Wrap `vrcli` library actions in Rapid7's `insight-plugin` SDK (the library/CLI split in §3 makes this a thin adapter). Revisit gRPC/open-source support only if the deployment mix changes.

---

## 9. Suggested improvements (recommendations beyond the original ask)

1. **Chain of custody by default** — the evidence manifests + audit JSONL in §4.2 turn "automation output" into defensible evidence. Strongly recommended for an IR shop; cheap to build now, painful to retrofit.
2. **Two API keys, not one** — a SOAR service key and separate analyst keys, so rotation or compromise of one doesn't halt the other, and audit logs distinguish automated vs. human actions.
3. **Spec-drift watch** (§6) — Rapid7 will extend this young API; a monthly drift check is how you find new endpoints (e.g., the missing file-download capability) the week they ship instead of a year later.
4. **GitHub-side secret scanning as a second net** — even with no CI runner, enable GitHub push protection / secret scanning on the org (GitHub Advanced Security or the free tier for public-pattern detection). Pre-commit hooks only protect machines that installed them.
5. **Dry-run mode** — `--dry-run` on every mutating command prints the exact request without sending it; invaluable when developing SOAR workflows against production. *(Adopted 2026-06-10: plumbing is a Phase 1 deliverable; the flag is an acceptance criterion of whichever phase each mutating command lands in — see §8.)*
6. **Rate-limit etiquette** — honor `Retry-After`/429 with capped exponential backoff so a runaway SOAR loop can't hammer the tenant; cap `--all` pagination with a sane default page budget.
7. **Quarantine guardrails** — require an explicit allowlist label (e.g., never quarantine assets labeled `critical-infrastructure`) checked client-side before `vr ops contain --quarantine` fires. SOAR + quarantine is where automation accidents hurt most.
8. **pipx distribution** — analysts install with `pipx install git+ssh://...` for an isolated, easily-upgraded environment; pin a tested tag in SOAR so workflows don't break on upgrades.
9. **Later: scheduled sweeps** — a cron-driven `vr ops ioc-hunt --from-feed` consuming your threat-intel exports would close the loop from intel to fleet sweep with no human in the path.
10. **Revisit CI when possible** — pre-commit-only means an unhooked clone can still push secrets or broken code. Even a single self-hosted runner executing the same hooks server-side would close that gap.

---

## 10. Open questions

1. **File/container download** — can triage ZIPs (KapeFiles uploads) be retrieved via any hosted API? (Rapid7 ticket, filed in Phase 0.) Resolution branches: yes → add `vr flows download`; no-but-roadmapped → keep the Phase 4 checkpoint and wait; no-and-never → GUI-hybrid playbook step + `vr evidence ingest` (Phase 3 conditional scope).
2. **API key scoping** — does the Insight platform key used for Velociraptor support narrower scopes/roles, or is it org-wide? Determines how much §9.2 helps.
3. **Rate limits** — published limits for `insight-velociraptor` endpoints? (Affects polling defaults.)
4. **InsightConnect maximum step duration** — gates the `--wait` single-blocking-step contract; answered by the Phase 0 spike, with the `--start`/`--check` fallback contract specified in §8 Phase 2.
5. **Hunt `desiredState` semantics** — verify the full lifecycle enum (resume-after-stop? archive via API?) empirically in Phase 3 (the phase that implements hunts — the triage slice contains no hunt endpoints).
