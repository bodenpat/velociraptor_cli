# TODO

What is left to do on `velociraptor_cli`. Phases 0–3 of [PLAN.md](PLAN.md)
are built and committed on `main` (all 26 REST operations have `vr` commands;
six `vr ops` composites; 341 tests; pre-commit hooks installed; full docs in
[docs/](docs/), including [architecture](docs/architecture.md) and
[development](docs/development.md) guides). The items below are the remaining
work, grouped by what blocks them.

---

## Near-term (do next, no tenant required)

- [ ] **Tag `v0.1.0`.** The install docs and SOAR orchestrator pinning
      already reference `@v0.1.0`, but no git tag exists yet. Cut it from the
      current `main` so `pipx install …@v0.1.0` works and SOAR can pin it.
- [ ] **Assign the spec-drift owner** (PLAN §6 requires a *named* person
      before Phase 1 exit — still TBD) and put `scripts/check_spec_drift.py`
      on a monthly schedule.
- [ ] **Pin & hash dependencies** — `pip-compile --generate-hashes` →
      `requirements.txt`, and wire `pip-audit` into a pre-push hook
      (PLAN §5.5).
- [ ] **Turn on GitHub secret scanning / push protection** on the remote as
      the second net behind the local hooks (PLAN §9.4).

---

## Remaining phases (need the live tenant, not just code)

### Phase 4 — SOAR rollout & playbooks
- [ ] Stand up the four v1 InsightConnect workflows on the orchestrator host
      using the CLI: triage-on-alert, IOC sweep, contain-on-detection,
      enrichment (recipes drafted in [docs/soar-playbooks.md](docs/soar-playbooks.md)).
- [ ] Run the **Phase 0 orchestrator spike** for real: prove InsightConnect
      can exec a local command with an env-injected secret and capture its
      JSON output, and **measure the orchestrator's maximum step duration**
      (gates the `--wait` single-blocking-step contract; if it's below
      worst-case collection time, the `vr ops triage --start`/`--check`
      resume-token path is already built and tested).
- [ ] Write the analyst WSL install guide hand-off and tabletop a real
      investigation end-to-end with the tooling.
- [ ] **Acceptance:** triage-on-alert workflow runs in production producing
      the evidence directory with exit 0, no GUI involvement.
- [ ] Schedule the **rows-only evidence checkpoint** (PLAN §8 Phase 4): for
      the first 3 production incidents (or 60 days), add the postmortem
      question "did the analyst open the GUI to retrieve file content?" —
      any GUI fallback falsifies the rows-only evidence model and escalates
      the file-download ticket below to blocking. Assign a named owner.

### Phase 5 — Native InsightConnect plugin (future)
- [ ] Wrap the `vrcli` library actions in Rapid7's `insight-plugin` SDK (the
      library/CLI split makes this a thin adapter). Only pursue if the SOAR
      exec path from Phase 4 proves insufficient; the CLI stays valuable for
      WSL analysts regardless.

---

## Phase-3 empirical verifications (artifact assumptions baked into the code)

The REST spec defines collection mechanics but **not** artifact parameter
names. The composites assume the standard open-source Velociraptor artifact
definitions; each assumption is flagged in a docstring and must be confirmed
against the **tenant's actual artifact library** before the corresponding
`vr ops` verb is trusted in production.

- [ ] **`vr ops triage`** — `Windows.KapeFiles.Targets`: the target-group
      checkbox is sent as an env param whose *key* is the group name (default
      `KapeTriage`) with value `"Y"`. Confirm the parameter name/shape.
      (`src/vrcli/ops/triage.py`)
- [ ] **`vr ops ioc-hunt`** — `Windows.Search.FileFinder`: parameter names
      `SearchFilesGlob`, `SearchFilesGlobTable`, `YaraRule`, `Calculate_Hash`,
      and the `SearchFilesGlobTable` CSV format (`"Glob\n<g1>\n<g2>"`, no
      quoting). Confirm names and quoting rules. (`src/vrcli/ops/ioc_hunt.py`)
- [ ] **`vr ops contain --quarantine` / `vr ops release`** —
      `Windows.Remediation.Quarantine` and its `RemovePolicy="Y"` parameter.
      Confirm artifact + parameter name. (`src/vrcli/ops/containment.py`)
- [ ] **`vr ops live`** — preset → artifact mapping
      (`Windows.System.Pslist`, `Windows.Network.Netstat`,
      `Windows.Packs.Persistence`, `Windows.System.TaskScheduler`,
      `Windows.Applications.Chrome.History`, `Windows.Sys.Users`). Confirm
      each exists in the tenant. (`src/vrcli/ops/live_response.py`)
- [ ] **Hunt `expires`** — sent as epoch **microseconds**
      `int((time.time() + secs) * 1_000_000)`. Verify the unit empirically.
      (`src/vrcli/api/hunts.py`, PLAN §10.5)
- [ ] **Hunt `desiredState` lifecycle** — verify the full enum semantics
      empirically (resume-after-stop? archive via API?). (PLAN §10.5)

A quick way to check each: `vr artifacts get <Name> --format yaml` against
the tenant and diff the `parameters:` block against the assumptions above.

---

## External dependencies / open questions (PLAN §10)

- [ ] **File / container download** — *the longest-lead-time item.* The REST
      API exposes no endpoint to download uploaded files / collection ZIPs
      (KapeFiles triage output); `HuntStats.available_downloads` lists files
      but nothing serves them. File the Rapid7 support/TAM ticket (owner:
      whoever holds the Rapid7 account relationship). Resolution branches:
      - **yes, it's exposed** → add `vr flows download`.
      - **no, but roadmapped** → keep the Phase 4 checkpoint and wait.
      - **no, and never** → ship the GUI-hybrid playbook step and implement
        the conditional `vr evidence ingest <file>` (the library half,
        `EvidenceDir.add_existing_file`, already exists and is tested; only
        the CLI command is missing).
- [ ] **API key scoping** — does the Insight key support narrower
      scopes/roles, or is it org-wide? Determines how much the two-key
      (SOAR vs analyst) split buys. (PLAN §9.2)
- [ ] **Published rate limits** for `insight-velociraptor` endpoints — tune
      polling defaults and the `--all` page budget once known.

---

## Engineering follow-ups (nice-to-have, no tenant required)

(See also the Near-term section above for the spec-drift owner, dependency
pinning, and GitHub secret scanning.)

- [ ] **CI when available** — pre-commit-only means an unhooked clone can
      still push secrets or broken code. Even one self-hosted runner running
      the same hooks server-side closes that gap. (PLAN §9.10)
- [ ] **Scheduled sweeps (later)** — a cron-driven
      `vr ops ioc-hunt --from-feed` consuming threat-intel exports would
      close the loop from intel to fleet sweep with no human in the path.
      (PLAN §9.9)
- [ ] **Naming note (not a bug):** `vr flows create` exposes the
      `ArtifactCollectorArgs.timeout` field as `--collection-timeout` because
      `--timeout` belongs to the `--wait` deadline. Revisit if it confuses
      users.
