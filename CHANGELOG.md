# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  playbooks, installation/configuration/security guides.
