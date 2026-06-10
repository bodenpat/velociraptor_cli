# Security

`vrcli` drives endpoint collection and containment against production fleet
assets with an org-scoped API key. Treat this repo and every host that runs
it accordingly.

## Secret handling (the rules)

Configuration is **environment-only**. There is no config file containing
the key, no `--api-key` flag (it does not exist, by design), and no
interactive prompt.

| Variable | Meaning |
|---|---|
| `R7_VR_API_KEY` | Insight platform API key (required) |
| `R7_VR_REGION` | Insight region slug, e.g. `us`, `eu` (required) |
| `R7_VR_ORG_ID` | Rapid7 organization ID (required) |
| `R7_VR_API_KEY_FILE` | Alternative to `R7_VR_API_KEY`: path to a `0600` file containing only the key. Refused if group/world-readable. |
| `R7_VR_AUDIT_LOG` | Optional: central append-only audit JSONL path for `vr ops` commands. |

Delivery patterns:

- **InsightConnect (SOAR):** keep the key in the InsightConnect credential
  store; inject it into the orchestrator step as `R7_VR_API_KEY` at
  execution time. Never write it to disk on the orchestrator.
- **Analyst WSL:** keep the key in a `0600` file outside any git working
  tree (e.g. `~/.config/r7vr/key`) and set
  `R7_VR_API_KEY_FILE=~/.config/r7vr/key`, or
  `export R7_VR_API_KEY=$(cat ~/.config/r7vr/key)` per session. Do **not**
  hard-code the key in `.bashrc`, and never place it inside a repo.

Defense-in-depth implemented in the code (tested in `tests/`):

- The key lives in a `Secret` wrapper; `str`/`repr`/f-strings render `***`.
- A logging filter and response/exception redaction scrub the key from any
  debug output, traceback, or `--verbose` dump.
- TLS verification cannot be disabled — no flag, no env var. Corporate
  proxy CAs go via the standard `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`.
- The key cannot be passed as a CLI argument, so it cannot land in `ps`
  output, shell history, or SOAR job logs.

## Key scoping

Use **two keys, not one** (PLAN.md §9.2):

1. a dedicated service-account key for the InsightConnect orchestrator, and
2. individual analyst keys for interactive WSL use.

Rotation or compromise of one then never halts the other, and audit logs
distinguish automated from human actions.

## Rotation runbook

1. Generate the replacement key in the Insight platform (Settings → API keys)
   with the same scope.
2. Update the InsightConnect credential-store entry (SOAR key) or the
   analyst's `~/.config/r7vr/key` file (analyst key).
3. Run `vr status` on the affected host(s) — exit 0 confirms the new key.
4. Revoke the old key in the Insight platform.
5. Append a note (who, when, why) to your team's key-management log.

Rotate on: personnel change, any suspected exposure, and at most every 180
days as routine hygiene.

## If a key is exposed

1. **Revoke the key immediately** in the Insight platform — before any
   investigation. Velociraptor keys can quarantine hosts and delete clients.
2. Issue a replacement via the rotation runbook and restore SOAR/analyst
   service.
3. Review Velociraptor audit logs and the `vr` audit JSONL
   (`R7_VR_AUDIT_LOG`, evidence-dir `audit.jsonl`) for actions taken with
   the exposed key between exposure and revocation: unexplained hunts,
   flows, label changes, quarantines, client deletions.
4. If the key was committed to git: revoke first, then purge the commit
   (history rewrite), then investigate how it bypassed the hooks and close
   that gap.
5. File the incident through the team's normal IR process.

## Repo hygiene

- Pre-commit hooks are the CI (no runner available): `gitleaks`,
  `detect-secrets` (with `.secrets.baseline`), `ruff`, a hook that rejects
  any non-placeholder UUID (the Insight API key format), and a docs
  freshness check. Install them in every clone:
  `pre-commit install --install-hooks`. A clone without hooks has **no**
  secret protection — see PLAN.md §9.10.
- In docs, tests, and examples, only placeholder UUIDs are allowed: every
  hex group a single repeated character, e.g.
  `11111111-2222-3333-4444-555555555555`.
- `.gitignore` pre-blocks `.env*`, `*.key`, `evidence/`, `collections/`,
  and audit logs. Keep evidence output outside the repo anyway.
- Enable GitHub push protection / secret scanning on the remote as the
  second net (PLAN.md §9.4) — hooks only protect machines that installed
  them.

## Supply chain

Runtime dependencies are deliberately minimal: `httpx`, `click`, `PyYAML`.
Every new runtime dependency requires a written justification in the PR.
Pin with hashes via `pip-compile --generate-hashes`; run `pip-audit` before
release.

## Reporting a vulnerability

Report security issues in this tool to the infosec engineering team
directly (internal channels), not via public issue trackers.
