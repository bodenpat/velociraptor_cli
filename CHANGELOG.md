# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-06-11

### Added

- `docs/phase-plans/` — pick-up-and-execute plans for the remaining work:
  Phase 4 (SOAR rollout) and Phase 5 (native InsightConnect plugin), each an
  ordered task list with `[agent]` / `[tenant]` / `[human]` tags and per-task
  acceptance gates, plus an index. Wired into the README, docs index, and
  TODO. Adversarially verified against the codebase (zero findings).

## [0.1.0] - 2026-06-11

First tagged release. Implements PLAN.md Phases 0–3: the full library + `vr`
CLI with 1:1 coverage of all 26 REST operations and the six `vr ops`
investigation composites. 341 tests; pre-commit hooks are the CI.

### Added

- Phase 0: repo scaffolding, pre-commit security baseline (gitleaks,
  detect-secrets, ruff, Insight-key-format reject hook, docs-freshness
  hook), vendored OpenAPI spec as the contract, SECURITY.md.
- Phase 1: core library — environment-only config with `Secret` wrapper and
  0600 key-file support, redacting httpx transport with retry/backoff and
  Retry-After handling, typed errors mapped to the CLI exit-code contract
  (0/2/3/4/5/6/7), cursor pagination with page budget, `vr status`.
- Phases 2–3: 1:1 CLI coverage of all 26 spec operations (`vr clients`,
  `vr labels`, `vr hunts`, `vr flows`, `vr artifacts`) and SOAR composites
  (`vr ops triage|live|ioc-hunt|contain|release|enrich`) with evidence
  manifests, audit JSONL, `--wait`/`--start`/`--check`, `--dry-run` on
  every mutating command, and quarantine guardrails.
- Documentation: auto-generated CLI reference, API coverage map, SOAR
  playbooks, installation/configuration/security guides, architecture and
  development guides, and a docs index.

### Fixed (adversarial review pass)

- URL-encode the `client_id` path segment in `vr clients get/update/delete`
  so a crafted ID can no longer traverse out of `/clients/{id}`.
- Audit-log argv redaction now scrubs the key regardless of source
  (`R7_VR_API_KEY` **or** `R7_VR_API_KEY_FILE`) by reading the live secret
  registry, closing a cleartext-key path to the audit JSONL.
- `main()` has a catch-all so an unexpected exception still emits one JSON
  document on stdout and an in-contract exit code (`1`) instead of a bare
  traceback; local filesystem write failures map to exit `2`.
- Evidence collection unwraps the `{size,cursor,data}` envelope from
  `getAvailableFlowResults` instead of treating its keys as artifact names.
- The per-collection audit record is written into the evidence directory
  **before** the manifest is finalized, so `audit.jsonl` is hashed into the
  SHA-256 manifest (chain-of-custody now covers the audit record itself).
- `resolve_client_arg` raises a typed `APIError` instead of a bare
  `KeyError` when a resolved client record lacks `client_id`.

[Unreleased]: https://github.com/bodenpat/velociraptor_cli/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/bodenpat/velociraptor_cli/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/bodenpat/velociraptor_cli/releases/tag/v0.1.0
