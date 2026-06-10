# `vrcli` documentation

Documentation for `vrcli` / the `vr` CLI. Start at the top-level
[README](../README.md) for install + quickstart; the guides below go deep.

## Using `vr`

- **[installation.md](installation.md)** — pipx (WSL analysts), `pip --user`
  fallback, orchestrator tag-pinning, and the dev setup.
- **[configuration.md](configuration.md)** — every environment variable, the
  JSON-stdout output contract, the exit-code table, `--wait` / `--all` /
  `--dry-run`, pagination budgets, TLS and corporate-proxy CAs.
- **[cli-reference.md](cli-reference.md)** — every command and option,
  **auto-generated** from the live click tree. Never hand-edited; it cannot
  drift from the code.
- **[soar-playbooks.md](soar-playbooks.md)** — copy-pasteable InsightConnect
  recipes for triage-on-alert, IOC sweep, contain-on-detection, and
  enrichment, with the JSON each step returns and exit-code branching.

## Understanding & extending `vr`

- **[architecture.md](architecture.md)** — the design: library/CLI layering,
  the request path, the secret/redaction model, transport behavior, the
  error/exit-code model, evidence + audit chain-of-custody, the ops composite
  pattern, and how the codebase was built and reviewed.
- **[development.md](development.md)** — contributor guide: dev setup, running
  tests, the pre-commit hooks (which are the CI), the generated CLI reference,
  spec-drift checking, adding a new endpoint, and the release process.
- **[api-coverage.md](api-coverage.md)** — all 26 spec operations mapped to
  their `vr` commands, the composite→primitive orchestration map, and the
  known file-download gap.

## Project-level documents (repo root)

- **[../README.md](../README.md)** — overview, install, 5-minute quickstart.
- **[../SECURITY.md](../SECURITY.md)** — secret handling, key rotation
  runbook, exposure response.
- **[../PLAN.md](../PLAN.md)** — the full project plan: decisions,
  architecture, phases, open questions.
- **[../TODO.md](../TODO.md)** — remaining work and next steps.
- **[../CHANGELOG.md](../CHANGELOG.md)** — release history.
