# InsightConnect SOAR playbooks

Copy-pasteable recipes for the four v1 use cases (PLAN.md §6, §8 Phase 4):
triage-on-alert, IOC sweep from a threat-intel feed, contain-on-detection,
and enrichment. Each recipe gives the orchestrator step setup, the exact
command, the JSON the step returns on stdout, exit-code branching, and where
the audit/evidence ends up.

All examples use placeholder values only: org ID
`11111111-2222-3333-4444-555555555555`, client ID `C.1111111111111111`, flow
ID `F.2222222222222222`, hunt ID `H.3333333333333333`. Substitute your own.

## Orchestrator step setup (applies to every recipe)

`vr` runs as a local command on the InsightConnect orchestrator host
(validated by the Phase 0 exec spike). The contract:

- **stdout** is exactly one JSON document per invocation — parse it directly
  in the workflow. Errors are *also* a JSON document on stdout
  (`{"error": {...}}`), so the step output is always parseable.
- **stderr** carries logs/progress only, never secrets.
- The step branches on the **exit code** (table below) and/or fields in the
  output document.

### Environment injection — never argv

Configuration is environment-only (SECURITY.md). The API key lives in the
**InsightConnect credential store** and is injected into the step's
environment at execution time. It is never written to disk on the
orchestrator, never echoed, and **cannot** appear on the command line —
`vr` has no `--api-key` flag by design, so it cannot leak into `ps` output,
shell history, or SOAR job logs.

Configure the step's environment as:

| Variable | Value | Source |
|---|---|---|
| `R7_VR_API_KEY` | the SOAR service-account key | InsightConnect credential store (injected at runtime) |
| `R7_VR_REGION` | `us` (your Insight region slug) | workflow constant |
| `R7_VR_ORG_ID` | `11111111-2222-3333-4444-555555555555` | workflow constant |
| `R7_VR_AUDIT_LOG` | `/var/log/vrcli/audit.jsonl` | workflow constant — central append-only audit trail |

Use a **dedicated SOAR service key**, separate from analyst keys
(SECURITY.md "Key scoping"), and pin a tested `vrcli` release tag on the
orchestrator so workflows do not break on upgrades (PLAN.md §9.8).

Sanity check from any step: `vr status` performs one cheap authenticated GET
and exits 0 on success — use it as the workflow's health-check step.

### Exit-code branching (all recipes)

| Exit code | Meaning | Typical SOAR branch |
|---|---|---|
| `0` | success | continue; parse stdout JSON |
| `2` | usage/config error — bad arguments, missing env vars, ambiguous hostname, **quarantine guardrail refusal** | stop; route to a human (the workflow or its inputs are wrong) |
| `3` | auth/permission denied (401/403) | stop; page the key owner — key revoked, rotated, or under-scoped |
| `4` | not found (404, or hostname resolved to zero clients) | branch: asset not enrolled in Velociraptor — fall back to other tooling |
| `5` | wait-timeout (`--wait` exceeded `--timeout`) | the operation is **still running server-side**; re-check later or use the `--start`/`--check` pattern |
| `6` | API error (other 4xx/5xx, or a flow that ended in ERROR) | retry once, then route to a human with the stdout error document |
| `7` | network/TLS failure | retry with backoff; check orchestrator egress |

On any non-zero exit, stdout still carries one JSON error document:

```json
{
  "error": {
    "type": "WaitTimeout",
    "message": "Timed out after 600s waiting for flow F.2222222222222222 on C.1111111111111111",
    "exit_code": 5
  }
}
```

---

## 1. Triage-on-alert

Trigger: an InsightIDR alert fires for a host. Goal: a forensically-sound
KapeFiles triage collection with zero GUI involvement.

### Single blocking step (orchestrator allows long steps)

```bash
vr ops triage "WS-FINANCE-07" --wait --timeout 1800 --poll-interval 15
```

`WS-FINANCE-07` can be a hostname (auto-resolved; exact match wins, 0 matches
= exit 4, multiple = exit 2) or a `C.…` client ID. The defaults collect
`Windows.KapeFiles.Targets` with the `KapeTriage` target group; override with
`--artifact` / `--targets` if your playbook needs a different target set.

Step output on success (exit 0):

```json
{
  "done": true,
  "client_id": "C.1111111111111111",
  "flow_id": "F.2222222222222222",
  "out": "evidence/triage-C.1111111111111111-F.2222222222222222",
  "artifacts": {
    "Windows.KapeFiles.Targets": 1342
  },
  "log_lines": 87,
  "manifest_files": 4
}
```

`artifacts` maps each collected artifact to its result-row count. Attach
`out`, `flow_id`, and the row counts to the investigation.

### Resume-token pattern (orchestrator caps step duration)

If the orchestrator's maximum step duration is shorter than a worst-case
triage collection, split the operation into a start step plus a polling loop
(PLAN.md §8 Phase 2 contract):

**Step A — start the collection** (returns in seconds):

```bash
vr ops triage "WS-FINANCE-07" --start
```

```json
{
  "done": false,
  "client_id": "C.1111111111111111",
  "flow_id": "F.2222222222222222",
  "token": "<opaque-base64url-resume-token>",
  "out": "evidence/triage-C.1111111111111111-F.2222222222222222"
}
```

Store `token` as a workflow artifact. (`--start` is also the default when
neither `--wait` nor `--check` is given.)

**Step B — poll in a loop** (e.g. every 2–5 minutes, with a loop cap):

```bash
vr ops triage --check "<opaque-base64url-resume-token>"
```

While the flow is still running the step **exits 0** with:

```json
{
  "done": false,
  "state": "RUNNING",
  "token": "<opaque-base64url-resume-token>"
}
```

Branch the loop on the `done` field, **not** the exit code. The terminal
`--check` (flow FINISHED) writes the evidence directory and manifest exactly
as `--wait` would and returns the same `"done": true` document shown above.
A flow that ends in ERROR makes `--check` exit 6.

### Audit and evidence

- Evidence directory (`out`): `flow.json` (the flow record),
  `results/<Artifact>.jsonl` (every result row), `logs.jsonl` (execution
  logs), `manifest.json` (SHA-256 + size + timestamp for every file, plus
  operator identity and collection context), and `audit.jsonl`. The default
  location is `./evidence/triage-<client_id>-<flow_id>` relative to the
  step's working directory — set `--out DIR` to put it on the evidence
  share. The directory must not already contain files.
- Central audit: one JSONL record per invocation appended to
  `$R7_VR_AUDIT_LOG` (timestamp, operator, command, redacted argv, IDs
  created). `--start`/non-terminal `--check` invocations audit centrally
  only, since the evidence directory does not exist yet.
- Known limitation: result rows + logs are the evidence package; the raw
  uploaded files (the triage ZIP) have **no REST download endpoint** —
  see `docs/api-coverage.md` "Known gap". Rapid7 ticket pending.

---

## 2. IOC sweep from a threat-intel feed

Trigger: a new SHA-256 indicator lands in the threat-intel feed. Goal: a
fleet-wide hunt scoped by label, with a per-client hit summary the workflow
can branch on.

```bash
vr ops ioc-hunt \
  --hash "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  --label "windows-workstations" \
  --os windows \
  --expires 24h \
  --description "TI feed sweep: <feed item ID>" \
  --wait --timeout 3600 --poll-interval 60
```

`--hash`, `--filename GLOB`, and `--yara FILE` are combinable and repeatable
(at least one is required). Hash IOCs make the hunt compute file hashes over
`--glob` (default `C:\Users\**`); matching against your hashes happens
client-side in the summary step. `--label` restricts targeting; add
`--client-limit N` for a canary run before a full-fleet sweep.

Step output with `--wait` (exit 0):

```json
{
  "hunt_id": "H.3333333333333333",
  "complete": true,
  "clients_with_hits": {
    "C.1111111111111111": 2
  },
  "total_hits": 2,
  "clients_with_errors": 1,
  "stats": {
    "total_clients_scheduled": 240,
    "total_clients_with_results": 239,
    "total_clients_with_errors": 1
  }
}
```

(`stats` is the hunt's server-side stats object passed through as-is; treat
its fields as representative, not contractual.) Branch on `total_hits > 0`
to open an investigation and fan out to the containment recipe per client in
`clients_with_hits`.

Without `--wait` the step returns immediately with `{"hunt_id":
"H.3333333333333333"}`; check progress later with `vr hunts get` /
`vr hunts results`. On a `--wait` timeout (exit 5) the hunt **keeps
running** until its `--expires`; re-summarize later from the primitives.

### Audit and evidence

- No evidence directory is produced. The audit record (hunt ID created,
  operator, redacted argv) is appended to `$R7_VR_AUDIT_LOG`.
- Persist results when needed: `vr hunts results H.3333333333333333 --all
  --out hits.jsonl`.
- Tip for workflow development: add `--dry-run` to print the exact
  `createHunt` request without launching anything.

---

## 3. Contain-on-detection

Trigger: a high-confidence detection on a host. Goal: label the asset for
investigation and (optionally) network-quarantine it — with a guardrail so
automation can never quarantine the wrong class of machine.

```bash
vr ops contain "WS-FINANCE-07" --quarantine
```

Without `--quarantine` the command only adds the investigation label
(default `under-investigation`; override with `--label`).

### The quarantine guardrail (read before enabling `--quarantine`)

Before **any** mutating request is sent, the client's current labels are
checked client-side against the quarantine deny set (PLAN.md §9.7):

- built-in deny labels: `critical-infrastructure`, `no-quarantine`
- plus the comma-separated **`R7_VR_QUARANTINE_DENY_LABELS`** environment
  variable (set it in the orchestrator step env, e.g.
  `R7_VR_QUARANTINE_DENY_LABELS=domain-controllers,prod-servers`)
- plus any `--deny-label LABEL` options on the command itself

Matching is case-insensitive. A match refuses the **whole operation** with
exit 2 before anything is labeled or quarantined:

```json
{
  "error": {
    "type": "UsageError",
    "message": "Refusing to quarantine C.1111111111111111: client carries deny label(s) 'critical-infrastructure' (quarantine guardrail, PLAN.md §9.7)",
    "exit_code": 2
  }
}
```

Route that branch to a human — it means your automation targeted a protected
asset. Maintain the deny labels in Velociraptor (e.g. via
`vr labels bulk-add no-quarantine --from-file protected.txt`) as part of
asset onboarding.

Step output on success (exit 0):

```json
{
  "client_id": "C.1111111111111111",
  "host": "WS-FINANCE-07",
  "labels_added": ["under-investigation", "vr-quarantined"],
  "quarantine": {
    "flow_id": "F.2222222222222222",
    "state": "RUNNING"
  }
}
```

`quarantine.state` is the quarantine flow's state right after scheduling
(`RUNNING` means it was accepted; the endpoint applies it when it next
checks in). Without `--quarantine`, `labels_added` is just the containment
label and `quarantine` is `null`. The `vr-quarantined` marker label is how
release detects quarantine state.

To undo after the investigation closes:

```bash
vr ops release "WS-FINANCE-07"
```

```json
{
  "client_id": "C.1111111111111111",
  "host": "WS-FINANCE-07",
  "labels_removed": ["under-investigation", "vr-quarantined"],
  "unquarantine": {
    "flow_id": "F.2222222222222222"
  }
}
```

### Audit and evidence

- No evidence directory. One audit record per invocation (client ID,
  quarantine/unquarantine flow IDs created) to `$R7_VR_AUDIT_LOG`.
- `--dry-run` describes every request that *would* be sent — and still
  enforces the guardrail, so a dry run surfaces the refusal. Use it when
  developing the workflow against production.

---

## 4. Enrichment

Trigger: any InsightIDR investigation touching a host. Goal: one read-only
JSON blob to attach to the investigation — client record, labels, last-seen,
recent collection activity.

```bash
vr ops enrich "WS-FINANCE-07" --flows 10
```

Step output (exit 0):

```json
{
  "client": {
    "client_id": "C.1111111111111111",
    "os_info": { "hostname": "WS-FINANCE-07" }
  },
  "labels": ["windows-workstations", "under-investigation"],
  "last_seen_at": "2026-06-10T17:31:12Z",
  "recent_flows": [
    {
      "flow_id": "F.2222222222222222",
      "state": "FINISHED",
      "create_time": "2026-06-10T16:05:00Z",
      "artifacts": ["Windows.KapeFiles.Targets"]
    }
  ]
}
```

`client` is the full client record passed through from the API (the spec
leaves its schema open — the snippet above is abbreviated); `last_seen_at`
is a best-effort extraction (`null` if the record carries no last-seen
field); `recent_flows` is newest-first, capped by `--flows`
(default 10). Attach the whole document as-is.

Exit-code branching: the interesting branch is `4` — the host is not
enrolled in Velociraptor, which is itself a useful enrichment fact.

### Audit and evidence

- Read-only: nothing is created or mutated, and no evidence directory is
  written. One audit record (client ID, flow count returned) still goes to
  `$R7_VR_AUDIT_LOG`, so even lookups are accounted for.
