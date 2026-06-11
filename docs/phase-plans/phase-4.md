# Phase 4 — SOAR rollout & playbooks (execute-level plan)

**Goal (PLAN.md §8):** the four v1 InsightConnect workflows run in production
against the hosted tenant using the `vr` CLI on the orchestrator host, with
an analyst WSL install guide and a tabletop behind them.

**Definition of done (phase acceptance):** the **triage-on-alert** workflow
runs in production and produces the evidence directory with **exit code 0,
no GUI involvement**, *and* a named owner is assigned for the post-rollout
review.

**Read first:** [../soar-playbooks.md](../soar-playbooks.md) (the four
recipes, with exact commands + the JSON each returns),
[../configuration.md](../configuration.md) (env vars, exit codes),
[../../SECURITY.md](../../SECURITY.md) (key delivery), PLAN.md §8 Phase 4 +
§10 open questions.

---

## Preconditions (gather before starting)

All `[tenant]`/`[human]` — an agent cannot acquire these:

- [ ] **`[tenant]`** Access to the InsightConnect console and the orchestrator
      host (shell).
- [ ] **`[tenant]`** A **dedicated SOAR service-account** Insight API key
      (separate from analyst keys — SECURITY.md "Key scoping"), loaded into
      the InsightConnect **credential store**.
- [ ] **`[tenant]`** The Insight **region slug** and **org ID** for the tenant.
- [ ] **`[tenant]`** A **lab host enrolled in the production tenant** to be
      the acceptance target (so the triage runs end-to-end without touching a
      real incident).
- [ ] **`[human]`** Confirmation that `v0.1.0` is the tag to deploy (or a
      newer tested tag).

---

## Task group A — Orchestrator exec spike (the load-bearing carryover)

This is the Phase 0 spike, still owed. Everything else assumes it passes.
PLAN.md §8 Phase 0 + §10.4.

- [ ] **A1 `[tenant]` Install the pinned CLI on the orchestrator host.**
      ```bash
      pipx install git+https://github.com/bodenpat/velociraptor_cli@v0.1.0
      vr --version    # expect: vr, version 0.1.0
      ```
      *Gate:* `vr --version` prints `0.1.0`.

- [ ] **A2 `[tenant]` Prove env-injected-secret exec from a workflow step.**
      Create a throwaway InsightConnect workflow with one command step that
      runs `vr status`, with the step environment configured per
      [../soar-playbooks.md](../soar-playbooks.md) "Orchestrator step setup":
      `R7_VR_API_KEY` injected from the credential store, `R7_VR_REGION` and
      `R7_VR_ORG_ID` as workflow constants.
      *Gate:* the step exits **0** and stdout is the `vr status` JSON
      (`{"status": "ok", ...}`). The key appears **nowhere** in the step's
      logs (confirm — this validates the no-argv / redaction design).

- [ ] **A3 `[tenant] [human]` Measure the maximum step duration.** In the same
      visit, determine the orchestrator's hard cap on a single step's wall
      time (run a deliberately slow step, or read the platform setting).
      *Gate:* a number is recorded.

- [ ] **A4 `[agent → human]` Record the spike results.** Agent creates
      `docs/phase-plans/spike-results.md` from this template; human fills the
      values:
      ```
      # Orchestrator exec spike — results
      Date / who:
      vr version deployed:
      A2 vr status exit code:        (expect 0)
      Key leaked into step logs?:    (expect NO)
      A3 max step duration (s):
      DECISION (see below):          --wait  |  --start/--check
      ```
      **Decision rule:** if max step duration ≥ worst-case triage collection
      time → use the single blocking step (`vr ops triage --wait`). If it is
      **below** that → use the resume-token pattern (`--start` + a polling
      loop on `--check`), already built and tested. Both are documented in
      [../soar-playbooks.md](../soar-playbooks.md) §1.
      *Gate:* `spike-results.md` committed with the decision recorded.

> **If A2 fails** (the orchestrator cannot exec a local command with an
> injected secret), the CLI-first SOAR path is invalidated — stop Phase 4 and
> go to [phase-5.md](phase-5.md) (native plugin). The CLI remains fully valid
> for WSL analysts regardless (task group B still applies).

---

## Task group B — Analyst WSL install guide

- [ ] **B1 `[agent]` Write `docs/analyst-guide.md`.** A WSL-analyst-focused
      quickstart distilled from [../installation.md](../installation.md) and
      [../../SECURITY.md](../../SECURITY.md): the `0600` key-file pattern
      (`R7_VR_API_KEY_FILE`), `pipx install …@v0.1.0`, `vr status`, and the
      three or four commands an analyst actually runs day to day
      (`vr clients resolve`, `vr ops enrich`, `vr ops triage … --wait`,
      `vr ops live … --preset …`). No new behavior — document what exists.
      *Gate:* the guide's commands match `vr --help` / `docs/cli-reference.md`;
      `python3 scripts/check_no_keys.py docs/analyst-guide.md` passes.

- [ ] **B2 `[tenant] [human]` Validate the guide on a real WSL box.** A human
      follows it start to finish on a clean WSL Ubuntu install and confirms
      `vr status` returns exit 0 against the tenant.
      *Gate:* one analyst other than the author completes the guide unaided.

---

## Task group C — Build the four production workflows

For each use case the agent produces a **workflow-spec document** (exact step
config, the command, env vars, the output-parsing expression, and the
exit-code branch table) that a human then recreates/imports in the
InsightConnect console. The recipes already exist in
[../soar-playbooks.md](../soar-playbooks.md); these tasks turn each into a
build-ready spec and then a live workflow.

- [ ] **C1 `[agent → tenant]` Triage-on-alert (acceptance-critical).**
      - Agent: write `docs/phase-plans/workflows/triage-on-alert.md` — trigger
        (InsightIDR alert → host), the exact step command (`--wait` or
        `--start`/`--check` per the A4 decision), step env, how to read
        `done`/`out`/`flow_id`/`artifacts` from stdout, the exit-code branch
        table (from soar-playbooks §1), and where evidence/audit land.
      - Tenant: build the workflow; run it against the **lab host** from
        preconditions.
      *Gate (= phase acceptance):* the workflow run exits **0** and the
      evidence directory exists with `manifest.json` + `audit.jsonl`, **no GUI
      used**. Capture the run as evidence in `spike-results.md` or a linked
      note.

- [ ] **C2 `[agent → tenant]` IOC sweep from a threat-intel feed.**
      - Agent: write `docs/phase-plans/workflows/ioc-sweep.md` — feed item →
        `vr ops ioc-hunt --hash … --label … --os windows --expires 24h
        --wait`; parsing `total_hits` / `clients_with_hits`; fan-out to C3 per
        hit. **Before production use, verify the FileFinder artifact
        parameter names** against the tenant (TODO.md Phase-3 item) — note
        this as a blocking sub-step in the spec.
      - Tenant: build it; dry-run first (`--dry-run`) against production to
        confirm the `createHunt` body, then a `--client-limit` canary.
      *Gate:* a canary hunt completes and the per-client summary parses; the
      FileFinder param names are confirmed.

- [ ] **C3 `[agent → tenant]` Contain-on-detection (highest-risk).**
      - Agent: write `docs/phase-plans/workflows/contain-on-detection.md` —
        `vr ops contain <host> --quarantine`; **document the guardrail
        prominently**: set `R7_VR_QUARANTINE_DENY_LABELS` in the step env
        (e.g. `domain-controllers,prod-servers`), and stand up the deny-label
        onboarding (`vr labels bulk-add no-quarantine --from-file
        protected.txt`). Include the exit-2 guardrail-refusal branch → route
        to human. **Verify the `Windows.Remediation.Quarantine` artifact +
        `RemovePolicy` parameter** against the tenant first (TODO.md Phase-3).
      - Tenant: build it; **test the guardrail first** — run against a host
        labelled `no-quarantine` and confirm exit 2 with nothing mutated
        (use `--dry-run` to confirm, then for real on a safe lab host).
      *Gate:* guardrail refuses a protected host (exit 2, no mutation); a
      normal contain + `vr ops release` round-trips on the lab host.

- [ ] **C4 `[agent → tenant]` Enrichment.**
      - Agent: write `docs/phase-plans/workflows/enrichment.md` —
        `vr ops enrich <host>`; attach the JSON to the InsightIDR
        investigation; the interesting branch is exit 4 (host not enrolled).
      - Tenant: build it.
      *Gate:* the enrichment document attaches to a test investigation.

---

## Task group D — Tabletop a real investigation

- [ ] **D1 `[human] [tenant]` Run a tabletop** end to end with the tooling:
      alert → enrich → triage → (if warranted) contain → release. Use the lab
      host. Capture friction and any GUI fallback.
      *Gate:* the team agrees the toolchain covers the investigation path; any
      gaps are filed as issues / TODO items.

---

## Task group E — Post-rollout review (rows-only evidence checkpoint)

PLAN.md §8 Phase 4 decision rule. This is how we find out whether the
"evidence = rows + logs, no file download" model holds in practice.

- [ ] **E1 `[agent]` Draft the postmortem checklist item.** Add to the team's
      incident-postmortem template (or a doc here if none exists) the line:
      *"Did the analyst open the hosted GUI to retrieve file content for this
      incident? (yes/no; if yes, what for?)"*
      *Gate:* the checklist item text is written and handed off.

- [ ] **E2 `[human]` Assign a named owner and schedule the review.** Track the
      **first 3 production incidents** using `vr ops triage` (or 60 days,
      whichever first).
      **Decision rule:** any GUI fallback → the rows-only model is falsified;
      escalate the Rapid7 file-download ticket (TODO.md / PLAN.md §10.1) to
      *blocking* for "triage fully automated", and keep the GUI-hybrid step.
      Fewer than 3 incidents with zero fallback at day 60 → extend once by 60
      days, then decide at the next quarterly review.
      *Gate (= second half of phase acceptance):* a named person owns this,
      with a calendar checkpoint.

---

## Dependencies & conditional work

- **File-download ticket (PLAN.md §10.1, TODO.md).** Independent of the above,
  but its resolution drives E2 and may unlock:
  - `[agent]` **`vr evidence ingest <file>`** — the conditional Phase-3 verb,
    only built if the ticket resolves "no download, not on the roadmap". The
    library half exists and is tested (`EvidenceDir.add_existing_file`); this
    task is just the thin CLI command + tests + `api-coverage.md` update.
    Buildable now if the team wants the GUI-hybrid path ready.
- **Phase-3 artifact verifications (TODO.md).** C2 and C3 are blocked on
  confirming the FileFinder and Quarantine artifact parameter names against
  the tenant. C1 is blocked on confirming the KapeFiles target-group key.

## Phase-4 completion checklist

- [ ] A4 spike results committed, `--wait` vs `--start/--check` decided.
- [ ] `docs/analyst-guide.md` written and validated (B1, B2).
- [ ] Four workflow-spec docs written; four workflows live (C1–C4).
- [ ] Triage-on-alert acceptance run: exit 0, evidence dir, no GUI (C1).
- [ ] Tabletop completed (D1).
- [ ] Postmortem checklist item added; post-rollout review owner named (E1, E2).
