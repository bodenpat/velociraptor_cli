# Architecture

The design/architecture reference for `vrcli` — the Python library and `vr`
CLI for the Rapid7-hosted Velociraptor REST API. This document explains how
the pieces fit, why the boundaries are where they are, and the load-bearing
invariants (secret redaction, exit codes, chain of custody) that the rest of
the codebase depends on.

Related docs: [configuration.md](configuration.md),
[api-coverage.md](api-coverage.md), [../SECURITY.md](../SECURITY.md),
[../TODO.md](../TODO.md). The plan of record is [../PLAN.md](../PLAN.md); this
doc implements §3.

---

## 1. Overview & layering

`vrcli` is a reusable library with a thin CLI on top. The split is
deliberate (PLAN.md §1, §3): InsightConnect (SOAR) calls the CLI *now*, and
the same library gets wrapped as a native InsightConnect plugin *later*
(PLAN.md §8 Phase 5). Keeping all behavior in the library means the future
plugin is a thin adapter, not a rewrite.

Three layers, strictly ordered — each depends only on the ones below it:

```
        cli/      click command tree; argv parsing, output, exit codes
          |       (the ONLY layer that imports click)
   +------+------+
   |             |
  api/         ops/   composite SOAR verbs (triage, live, ioc-hunt,
   |             |     contain/release, enrich) built ON TOP of api/
   +------+------+
          |
   transport.py   one httpx.Client: auth, retries, redaction, dry-run
          |
   config.py  errors.py  pagination.py   (no network, no click)
```

The rule that makes the future plugin cheap: **`api/` and `ops/` never
import `click`**. They take a `VRTransport` and return plain Python
(dicts/lists/typed exceptions). All click coupling — options, prompts,
stdout, exit-code translation — lives in `cli/`.

### Module map

| Module | Responsibility |
|---|---|
| `__init__.py` | `__version__`, `USER_AGENT` (`vrcli/<version>`). |
| `config.py` | Env-only config loading, the `Secret` wrapper, region/org validation, `base_url`. |
| `transport.py` | `VRTransport` (the one HTTP client): auth header, timeouts, retry/backoff, redaction, dry-run. The secret registry (`register_secret`, `active_secrets`). |
| `errors.py` | The `VRError` hierarchy and `error_for_status`; exit-code constants. |
| `pagination.py` | `paginate`/`collect` over the `{size, cursor, data}` envelope, with a page budget. |
| `api/*` | One module per spec group (`clients`, `labels`, `hunts`, `flows`, `artifacts`) — pure 1:1 bindings, no click. |
| `ops/*` | Composite verbs (`triage`, `live_response`, `ioc_hunt`, `containment`, `enrich`) plus shared helpers `_evidence`, `_audit`, `_wait`, `_collect`. Pure functions taking a `VRTransport`. |
| `cli/*` | `main` (root group + exit-code mapping), `_common` (app context, output contract, shared options), one module per command group, and `ops_*` modules each exporting `COMMANDS`. |

`api/clients.py` is the canonical api-module pattern (see §9). `ops/triage.py`
is the canonical ops-composite pattern (PLAN.md §8 Phase 2 — the vertical
slice).

---

## 2. The request path

Every command flows the same way:

```
cli command  ->  AppCtx.transport()  ->  VRTransport.request(...)  ->  typed result | typed error
```

1. The root group (`cli/main.py:cli`) builds an `AppCtx`
   (`cli/_common.py:AppCtx`) and stores `--output` / `--verbose` on it.
2. A command asks for `app.transport()`. The transport is built **lazily**
   (`AppCtx.transport`) so `vr --help` and `vr --version` need no env vars —
   `load_config()` only runs when a command actually needs the network.
3. The command calls a library function (`api/*` or `ops/*`), which calls
   `VRTransport.request(method, path, *, params=, json_body=, content=,
   headers=)` (`transport.py:VRTransport.request`).

`request()` normalizes inputs and outputs so callers stay simple:

- **`params` with `None` values are dropped** (`request` line 138:
  `{k: v for k, v in (params or {}).items() if v is not None}`). API modules
  pass every filter as a keyword and let `None` mean "omit" — see
  `api/clients.py:list_clients`.
- An empty `params` becomes `None` on the wire (no trailing `?`).
- **204 or an empty body → `None`** (`_handle_response`).
- **Non-JSON responses → the raw text** (`_parse_body`: only decodes when
  `content-type` contains `json`, falling back to `.text` on a JSON decode
  error). This is what lets `vr artifacts get --format yaml` return YAML
  text unchanged.
- A `>= 400` status becomes a typed `VRError` (see §6), with the body
  summarized into the message and the full body redacted into `.detail`.

---

## 3. Configuration & the Secret model

Configuration is **environment-only** (PLAN.md §5). There is no config file
holding the key, no `--api-key` flag, and no prompt that echoes. See
[configuration.md](configuration.md) for the operator-facing reference; the
mechanics live in `config.py`.

Recognized variables:

| Variable | Role |
|---|---|
| `R7_VR_API_KEY` | The key itself (SOAR credential-store injection). |
| `R7_VR_API_KEY_FILE` | Path to a `0600` file containing only the key (systemd `LoadCredential` / analyst key file). |
| `R7_VR_REGION` | Insight region slug. |
| `R7_VR_ORG_ID` | Rapid7 org ID (UUID). |
| `R7_VR_AUDIT_LOG` | Optional central append-only audit log (`ops/_audit.py:ENV_AUDIT_LOG`). |
| `R7_VR_QUARANTINE_DENY_LABELS` | Comma-separated extra quarantine deny labels (`ops/containment.py:ENV_DENY_LABELS`). |

`load_config()` (`config.py`) builds a frozen `Config`:

- Exactly one of `R7_VR_API_KEY` / `R7_VR_API_KEY_FILE` may be set; both is a
  `ConfigError`, neither is a `ConfigError`.
- The key file is read by `_read_key_file`, which **refuses any
  group/world-accessible file** (checks `S_IRGRP | S_IWGRP | S_IROTH |
  S_IWOTH`), rejects directories, empty files, and multi-line content —
  pushing operators toward `chmod 600`.
- `region` is format-validated against `_REGION_RE` (`^[a-z]{2}\d?$`), not
  allowlisted, so a new Rapid7 region needs no code change; `KNOWN_REGIONS`
  only feeds the error hint.
- `org_id` must match `_ORG_ID_RE` (UUID shape) and is lowercased.
- `base_url` (a `Config` property) is
  `https://{region}.api.insight.rapid7.com/insight-velociraptor/v1/orgs/{org_id}`.

### The `Secret` wrapper

`config.Secret` keeps the key out of every accidental sink:

- `__slots__ = ("_value",)`; the constructor uses `object.__setattr__`.
- `__repr__` and `__str__` both return `"***"` (`config.REDACTED`), so
  f-strings, `print`, and pytest assertion output never show the key.
- `__setattr__` raises `AttributeError("Secret is immutable")` — there is no
  second write path.
- `__reduce__` raises `TypeError("Secret cannot be pickled")` — it can't be
  serialized into a cache or a multiprocessing payload.
- `__eq__` / `__hash__` / `__bool__` work without revealing the value.
- **`.reveal()` is the only escape**, and the codebase calls it in exactly
  one place: `VRTransport.__init__` reads `config.api_key.reveal()` to set
  the `X-Api-Key` header (`transport.py` line 95).

---

## 4. Secret redaction (defense in depth)

A single leak of the Insight key is the worst-case bug for this tool, so
redaction is layered — every plausible escape path is closed independently.

1. **The `Secret` wrapper** (§3): the key never round-trips through `str`,
   `repr`, or pickle.

2. **The logging filter + the child-logger gotcha.**
   `transport.SecretRedactingFilter` scrubs every registered secret out of a
   record's rendered message. The subtlety, documented in
   `register_secret`'s docstring: **a `logging.Filter` attached to a parent
   logger does NOT run for records emitted by a child logger** — propagation
   only invokes ancestor *handlers*, not ancestor *filters*. So
   `register_secret(value)` installs `_REDACTING_FILTER` on `vrcli` **and on
   every already-created `vrcli.*` logger**. All vrcli modules create their
   loggers at import time (`logging.getLogger("vrcli.transport")` etc.), so
   by the time a transport exists every logger is covered. The filter
   **mutates `record.msg` in place** (and clears `record.args`) so the
   already-scrubbed message is what propagates up to the root handler.

3. **Response/exception body redaction.** `_handle_response` runs the
   summarized error message through `transport.redact()` and the full body
   through `_redact_any` (recursively walks dicts/lists/strings) before it
   becomes the `VRError.detail`. So even if the API echoes the key back in an
   error body, it is `***` by the time it reaches stdout.

4. **Dry-run header redaction.** `_describe_dry_run` replaces any
   `X-Api-Key` header value with `***` and runs raw `content` through
   `redact()`, so `--dry-run` output is safe to paste into a SOAR workflow
   or a ticket.

5. **Source-independent audit redaction.** The audit writer
   (`ops/_audit.py:_scrub`) scrubs argv using `transport.active_secrets()`,
   **not** `os.environ["R7_VR_API_KEY"]`. This matters: the registry is
   populated from the *revealed* key regardless of whether it came from the
   env var or the key file, so a file-sourced key is scrubbed from
   `audit.jsonl` too. (This was a review finding — see §10.)

6. **TLS cannot be disabled.** There is no flag, parameter, or env var in
   this codebase that turns off certificate verification (`transport.py`
   module docstring). Corporate proxy CAs go through the standard
   `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` mechanisms httpx honors.

7. **No `--api-key` flag.** The key can never appear in `ps`, shell history,
   or SOAR job logs because the option does not exist (`cli/main.py` group
   docstring).

The redaction guarantee is enforced by **SENTINEL-key tests**
(`tests/test_transport.py`, using `SENTINEL_KEY` from `tests/conftest.py`):
they inject a canary key and assert it appears in zero output across the
success path, the auth-echo path, the 500-echo path, the logging path, and
the dry-run path.

---

## 5. Transport behavior

`VRTransport` (`transport.py`) is a thin, fully-redacted wrapper around one
`httpx.Client`.

- **Timeouts** are explicit: connect `10s`, read `60s`
  (`Config.timeout_connect` / `timeout_read`, wired through
  `httpx.Timeout(read, connect=connect)`).
- **Retries.** Status codes in `RETRY_STATUSES = {429, 500, 502, 503, 504}`
  are retried up to `config.max_retries` (default 4). Backoff is **capped
  exponential with full jitter**: `_sleep_backoff` computes
  `min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt) * random()` (base `0.5s`, cap
  `30s`). A numeric **`Retry-After` header overrides** the computed delay,
  capped at `RETRY_AFTER_CAP = 120s`; an HTTP-date `Retry-After` can't be
  parsed as a float and falls back to the computed backoff. This is the
  rate-limit etiquette of PLAN.md §9.6 — a runaway SOAR loop can't hammer
  the tenant.
- **Retryable exceptions.** Connect/read/pool timeouts and dropped
  connections (`httpx.TimeoutException`, `httpx.NetworkError`) are retried.
  **A TLS verification failure is NOT retried** — `_is_retryable_exception`
  special-cases `httpx.ConnectError` whose text contains
  `CERTIFICATE_VERIFY_FAILED` and returns `False` (retrying a cert failure
  would only waste time and could mask a MITM).
- **Dry-run.** With `dry_run=True`, a request whose method is in
  `MUTATING_METHODS = {POST, PUT, PATCH, DELETE}` is **described, not sent**
  (`_describe_dry_run` returns a dict with `dry_run/method/url` and
  redacted params/body/headers). GET requests still go to the network even
  under dry-run, so read-only context-gathering inside a composite still
  works.

---

## 6. Error model & exit codes

The exit-code contract (PLAN.md §3) is what SOAR workflows branch on, so it
is encoded directly in the exception hierarchy. Each `VRError` subclass
carries its `exit_code` (`errors.py`):

| Code | Constant | Raised by |
|---|---|---|
| 0 | `EXIT_OK` | Success (`main()` returns 0). |
| 1 | `EXIT_INTERNAL` | The `main()` catch-all — any non-`VRError` exception. |
| 2 | `EXIT_USAGE` | `UsageError` / `ConfigError` — bad args or invalid local input/config. |
| 3 | `EXIT_AUTH` | `AuthError` — 401/403. |
| 4 | `EXIT_NOT_FOUND` | `NotFoundError` — 404, or a hostname resolving to zero clients. |
| 5 | `EXIT_WAIT_TIMEOUT` | `WaitTimeout` — `--wait` exceeded `--timeout`. |
| 6 | `EXIT_API` | `APIError` — other 4xx/5xx, **and a flow that ends in `ERROR`**. |
| 7 | `EXIT_NETWORK` | `NetworkError` — connection/DNS/TLS/timeout before a response. |

`error_for_status(status, message, detail=)` maps a status to the right
subclass: 401/403 → `AuthError`, 404 → `NotFoundError`, everything else →
`APIError` (which also records the numeric `status`). `transport.py` calls
this from `_handle_response`.

`main()` (`cli/main.py`) is where exceptions become exit codes, and it
**guarantees one JSON document on stdout in every case** (PLAN.md §3 — SOAR
parses stdout):

- `VRError` → `emit_error(exc.to_dict())` to stdout, human line to stderr,
  return `exc.exit_code`.
- `click.ClickException` (bad flags) → a `UsageError`-shaped JSON doc + exit
  2.
- `click.Exit` (`--help`/`--version`) and `click.Abort` are handled
  separately.
- **The catch-all `except Exception`** (added in review — see §10) emits a
  generic `{"type", "message": "unexpected internal error", "exit_code": 1}`
  doc to stdout and a one-line `internal error: ...` to stderr, then returns
  1. A SOAR step never sees a bare traceback on stdout; it can always
  `json.loads` the result. The detailed message stays on stderr so it isn't
  swallowed.

Local filesystem failures are deliberately **usage** errors, not internal
ones: `cli/_common.py:dump_jsonl` and every `EvidenceDir` write map `OSError`
to `UsageError` (exit 2).

---

## 7. Pagination

Paged endpoints return the `{size, cursor, data}` envelope (PLAN.md §2).
`pagination.paginate(fetch_page, *, max_items=, page_budget=)` yields items
across pages; `fetch_page(cursor)` performs one GET and returns the decoded
response. It handles three response shapes:

- a **dict envelope** → items from `response["data"]`, next cursor from
  `response["cursor"]`;
- a **bare array** → a single page by definition (`items = response`,
  `cursor = None`); this is why list endpoints that return a plain array
  still work through the same iterator;
- `None` (empty/204) → stop;
- anything else → `APIError("Unexpected paged response type: ...")`.

Two stop guards beyond exhausting the data:

- **Cursor-loop guard.** Cursors already seen are tracked in
  `seen_cursors`; a repeated cursor stops iteration (defends against a buggy
  server returning a stuck cursor).
- **Page budget.** The loop runs at most `page_budget` pages
  (`DEFAULT_PAGE_BUDGET = 100`). Hitting the budget **raises `UsageError`**
  with a "narrow the query or raise `--page-budget`" hint — it never
  silently truncates, so an analyst is never handed a partial result set
  that looks complete.

`collect(fetch_page, **kwargs)` is the eager `list(...)` convenience wrapper.

---

## 8. Evidence & audit (chain of custody)

Chain of custody is on by default (PLAN.md §4.2, §9.1): every ops command
that persists collection output writes it through an `EvidenceDir` with a
SHA-256 manifest and an audit JSONL record.

### `EvidenceDir` (`ops/_evidence.py`)

- **Refuses to mix collections.** The constructor raises `UsageError` if the
  target directory exists and is non-empty — evidence from two collections
  never lands in one directory.
- **Hashes everything it writes.** `write_json` / `write_jsonl` /
  `write_bytes` / `add_existing_file` each record `{name, sha256,
  size_bytes, written_at}` (and `ingested_from` for externally-supplied
  files). `add_existing_file` is the hook for attaching a GUI-downloaded
  container (PLAN.md §2 known gap — the REST API serves result rows but no
  uploaded-file download).
- **Path-traversal-safe.** `_safe_path` strips leading `/`, rejects `\\`,
  resolves the result, and raises `UsageError` unless it stays inside the
  evidence dir (`is_relative_to`). It also reserves `manifest.json`. This
  matters because artifact names become file names
  (`results/<artifact>.jsonl`).
- **`finalize()`** writes `manifest.json`: manifest version, vrcli version,
  operator identity (`getpass.getuser()` + hostname), the collection
  context, start/finalize timestamps, and the hashed file list.
- All write failures map `OSError` → `UsageError` (exit 2).

### Audit JSONL (`ops/_audit.py`)

One line per ops invocation: `ts`, `operator`, `command`, redacted `argv`,
`vrcli_pid`, plus any created `flow_id`/`client_id` and command-specific
`extra`. There are two sinks:

- the **central** append-only log at `$R7_VR_AUDIT_LOG` (`_append_central`,
  written for every ops invocation; a failed write raises `UsageError`, so a
  broken audit path fails loudly);
- the **in-evidence-dir** `audit.jsonl`, for commands that produce an
  evidence directory.

**The key design point:** `record_evidence_audit(evidence, ...)` writes
`audit.jsonl` **into the evidence dir BEFORE `evidence.finalize()`**, so the
audit record is itself hashed into `manifest.json`. Chain of custody
therefore covers the audit record, not just the collected data. The terminal
triage path enforces this ordering in
`ops/triage.py:_collect_finalize_audit`: collect → `record_evidence_audit`
→ `finalize`. (Use `write_audit` instead for commands with no evidence dir —
e.g. `--start`/`--check` polling and `ops contain`/`release`, which audit
centrally only.)

### Wait / terminal states (`ops/_wait.py`)

`wait_until(check, *, timeout, poll_interval)` runs `check()` on a
**monotonic** clock, checks immediately (a fast operation doesn't wait a full
interval), and raises `WaitTimeout` (exit 5) past the deadline.

- **Flows have real terminal states**: `FLOW_TERMINAL_OK = {"FINISHED"}`,
  `FLOW_TERMINAL_FAILED = {"ERROR"}`. `check_flow` raises `APIError` (exit 6)
  the moment a flow is `ERROR`, rather than waiting out the timeout.
- **Hunts have no single terminal state** — they run until expiry — so
  `hunt_complete` is a heuristic: state `STOPPED`/`ARCHIVED`, or
  `stats.stopped`, or every scheduled client has reported a result or an
  error (`with_results + with_errors >= total_scheduled`).

---

## 9. The ops composite pattern

Every composite follows the same two-file shape, with `ops/triage.py` +
`cli/ops_triage.py` as the canonical example:

1. **`ops/<name>.py`** — pure functions taking a `VRTransport` and returning
   a dict. No click. `run_triage` / `start_triage` / `check_triage` resolve
   the host (`api.clients.resolve_client_arg`), create a flow
   (`api.flows.create_flow`), wait/poll (`ops._wait`), collect evidence
   (`ops._collect.collect_flow_evidence`), and audit. Because they only
   touch `api/` + the `ops/_*` helpers, the future InsightConnect plugin can
   call them directly.

2. **`cli/ops_<name>.py`** — a thin click command that parses options,
   resolves `--dry-run` / `--wait` / `--start` / `--check`, calls the
   library function, and `emit()`s the single result document. Each module
   **exports a `COMMANDS` list**; `cli/ops.py:_register` walks every ops_*
   module and adds each command to the `vr ops` group. Adding a composite is:
   write the two files, list it in `_register`.

Standard composite controls:

- **`--dry-run`** flows down to the transport so mutating requests are
  described, not sent. Composites that mutate (e.g.
  `ops/containment.py:contain`) build the request descriptions and return
  `{"dry_run": True, "requests": [...]}`.
- **`--wait` / `--start` / `--check`** are mutually exclusive on triage
  (enforced in `cli/ops_triage.py`). `--start` returns an opaque base64url
  resume token (`encode_resume_token`: `{v, client_id, flow_id, out,
  host}`) so a SOAR step with a duration cap can fire the collection in one
  step and resume in another; `--check <token>` polls and, on `FINISHED`,
  writes the evidence directory exactly as `--wait` would (PLAN.md §8
  Phase 2 resume contract).

### Quarantine guardrail

`ops/containment.py` enforces a **client-side** quarantine deny-label
guardrail (PLAN.md §9.7) **before any mutating request is sent**.
`quarantine_deny_set` is the union of the built-ins
`{critical-infrastructure, no-quarantine}`, the comma-split
`$R7_VR_QUARANTINE_DENY_LABELS`, and any `--deny-label` values; matching is
case-insensitive. `contain(..., quarantine=True)` fetches the client's
current labels and calls `_check_quarantine_guardrail`, which raises
`UsageError` (exit 2) on any match — and the check runs even under
`--dry-run`, so a dry run surfaces the refusal. SOAR + quarantine is where
automation accidents hurt most, so the refusal happens before the network.

### Artifact-parameter assumptions

Some composites encode artifact-specific parameter names that come from the
open-source Velociraptor artifact library, **not** the REST spec — e.g. the
KapeFiles target-group key in `build_triage_args` and the
`Windows.Remediation.Quarantine` `RemovePolicy` parameter, both passed using
Velociraptor's `"Y"`/`"N"` boolean-string convention. These are flagged
in-code as Phase-3 tenant-verification items; the full list lives in
[../TODO.md](../TODO.md) and the command/endpoint mapping in
[api-coverage.md](api-coverage.md).

---

## 10. How this codebase was built & verified

The foundation and the canonical patterns were **hand-written**:
`config.py`, `transport.py`, `errors.py`, `pagination.py`, the api/ops
pattern exemplars (`api/clients.py`, `ops/triage.py`), and the test
fixtures. With those in place, a **multi-agent BUILD workflow** filled in
the rest — one agent per API group, then one per ops composite, then docs —
each new module copying the established pattern.

The result then went through a **multi-agent ADVERSARIAL REVIEW**: four
dimension-finders (spec conformance, secret redaction, CLI contract, code
quality) raising findings independently, each finding **verified by a
separate skeptic** before it survived. The review confirmed **7 findings (6
distinct — the `client_id` encoding issue was reported at two severities)**.
The fixes double as design rationale for some of the choices above:

1. **URL-encode `client_id` path segments** in `api/clients.py` (`_seg` /
   `quote(..., safe="")`) — it was the only module not encoding, a
   path-traversal risk.
2. **Source-independent audit redaction** — `ops/_audit.py` reads
   `transport.active_secrets()` so a key-file-sourced key is scrubbed from
   `audit.jsonl`, not just an env-var key (§4 item 5).
3. **The `main()` catch-all** — one JSON doc on stdout + exit 1 for any
   unexpected error, never a bare traceback (§6); local write failures map
   to exit 2 via `dump_jsonl` / `EvidenceDir` `OSError` handling.
4. **Envelope unwrapping in `_collect`** — `collect_flow_evidence` unwraps
   the `{size, cursor, data}` envelope from the list-results endpoint
   instead of iterating its keys as artifact names.
5. **Audit-before-finalize** — `record_evidence_audit` writes `audit.jsonl`
   before `finalize()`, so it is hashed into the manifest (§8).
6. **Typed error on a malformed record** — `resolve_client_arg` raises
   `APIError`, not a bare `KeyError`, when a resolved record lacks
   `client_id`.

Separately, an earlier **Phase-1** fix closed the **child-logger redaction**
gap (§4 item 2): `register_secret` installs the filter on every `vrcli.*`
logger and mutates `record.msg` in place, because a filter on a parent
logger does not run for a child logger's records. It surfaced while writing
the SENTINEL-key redaction tests, not in the review.

CI is **pre-commit-as-CI** (PLAN.md §1, §5): gitleaks/detect-secrets, ruff,
and a UUID-format gate run on every commit, since no CI runner is available.
**341 tests** pass and ruff is clean.
