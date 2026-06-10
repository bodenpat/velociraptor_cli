# vrcli — `vr`

Python library + CLI for the **Rapid7-hosted Velociraptor REST API**, built
for InsightConnect (SOAR) evidence-collection automation and interactive
analyst use from WSL.

Scope (PLAN.md §1):

- **REST only** — the Rapid7-hosted (paid) Velociraptor API. The open-source
  gRPC API (`pyvelociraptor`) is explicitly out of scope.
- **Reusable library with a CLI on top.** SOAR calls the `vr` CLI today; the
  same library gets wrapped as a native InsightConnect plugin later.
- **Secrets via environment variables only.** No config file with the key,
  no `--api-key` flag (it does not exist, by design), no interactive prompt.
- v1 use cases: triage collection, targeted live response, fleet-wide IOC
  hunts, host containment/labeling — plus 1:1 commands for all 26 spec
  operations.

Every `vr ops` command writes an audit JSONL record, and the collection
verbs persist result rows + logs into an evidence directory with a SHA-256
manifest — chain-of-custody by default.

## Install

Analysts (WSL) install with pipx from the internal git remote:

```bash
pipx install git+https://github.com/bodenpat/velociraptor_cli
```

The SOAR orchestrator host pins a **tested tag** instead, so workflows never
break on upgrade:

```bash
pipx install git+https://github.com/bodenpat/velociraptor_cli@v0.1.0
```

See [docs/installation.md](docs/installation.md) for the `python3-venv`
note on WSL Ubuntu, the `pip --user` fallback, and the dev setup
(pre-commit hooks are mandatory — they are the CI).

## Configure (environment only)

Read [SECURITY.md](SECURITY.md) first — it is short and it is the contract.

**InsightConnect (SOAR):** keep the key in the InsightConnect credential
store and inject it into the orchestrator step as `R7_VR_API_KEY` at
execution time. Never write it to disk on the orchestrator.

**Analyst (WSL):** keep the key in a `0600` file outside any git working
tree and point the CLI at it:

```bash
chmod 600 ~/.config/r7vr/key
export R7_VR_API_KEY_FILE=~/.config/r7vr/key
export R7_VR_REGION=us
export R7_VR_ORG_ID=11111111-2222-3333-4444-555555555555
```

(`vr` refuses a key file that is group- or world-readable.) Do **not**
hard-code the key in `.bashrc`, and never pass it on a command line — there
is no flag for it. Full variable semantics:
[docs/configuration.md](docs/configuration.md).

## 5-minute quickstart

```bash
# 1. Configure for this session (analyst pattern above, or SOAR injection).
export R7_VR_API_KEY_FILE=~/.config/r7vr/key
export R7_VR_REGION=us
export R7_VR_ORG_ID=11111111-2222-3333-4444-555555555555

# 2. Auth + connectivity smoke test (one cheap GET; the SOAR health check).
vr status

# 3. Hostname -> client record (errors on 0 or >1 matches unless --first).
vr clients resolve ws-finance-007

# 4. One-shot enrichment blob for an investigation:
#    client record + labels + last-seen + recent flows, one JSON document.
vr ops enrich ws-finance-007

# 5. KapeFiles triage collection, blocking until the flow finishes;
#    writes result rows + logs + SHA-256 manifest + audit record to
#    ./evidence/triage-<client_id>-<flow_id>/.
vr ops triage ws-finance-007 --wait
```

Anywhere a `<client_id>` is expected you may pass a hostname (auto-resolved;
prefix `host:` to force resolution, `C.`-prefixed values are used as-is).

stdout is always exactly **one JSON document** (`--output table` is the
human opt-in); logs and progress go to stderr. SOAR steps parse stdout and
branch on the exit code:

| Exit code | Meaning |
|---|---|
| `0` | success |
| `1` | unexpected internal error (still one JSON doc on stdout, never a bare traceback) |
| `2` | usage or configuration error |
| `3` | auth or permission denied (401/403) |
| `4` | not found (404, or a hostname that resolved to zero clients) |
| `5` | wait-timeout (`--wait` exceeded `--timeout`) |
| `6` | API error (other 4xx/5xx) |
| `7` | network / TLS failure |

## Documentation

- [docs/installation.md](docs/installation.md) — pipx, pip fallback,
  orchestrator pinning, dev setup
- [docs/configuration.md](docs/configuration.md) — env vars, output
  contract, exit codes, `--wait` / `--all` / `--dry-run`, TLS and proxies
- [docs/cli-reference.md](docs/cli-reference.md) — auto-generated from the
  live command tree; never hand-edited, cannot drift from the code
- [SECURITY.md](SECURITY.md) — secret handling, key rotation runbook,
  exposure response
