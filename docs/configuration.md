# Configuration

Configuration is **environment-only** (PLAN.md §5). There is no config
file containing the key, no `--api-key` flag (it does not exist, by
design), and no interactive prompt. Misconfiguration is reported on stderr
and exits with code `2`. Read [SECURITY.md](../SECURITY.md) for the
delivery patterns (SOAR credential-store injection vs. analyst key file).

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `R7_VR_API_KEY` | one of the two | The Insight platform API key itself. This is how InsightConnect injects the key from its credential store at step execution time. |
| `R7_VR_API_KEY_FILE` | one of the two | Path to a file containing **only the key, on one line**. The analyst/WSL and systemd-`LoadCredential` pattern. |
| `R7_VR_REGION` | yes | Insight platform region slug (see below). |
| `R7_VR_ORG_ID` | yes | Your Rapid7 organization ID (UUID format). |
| `R7_VR_AUDIT_LOG` | no | Path to a central, append-only audit JSONL written by every `vr ops` command (see below). |
| `R7_VR_QUARANTINE_DENY_LABELS` | no | Comma-separated labels added to the quarantine deny set for `vr ops contain --quarantine` (see below). |

### The API key

Exactly one of `R7_VR_API_KEY` / `R7_VR_API_KEY_FILE` must be set —
setting **both is an error** (exit `2`), and so is setting neither.
Whichever source is used, the key is held in a `Secret` wrapper whose
`str`/`repr` is `***`, and a logging filter plus response/exception
redaction scrub it from any output, including `--verbose` dumps.

**Key-file permission rule:** `vr` refuses to read a key file that is
group- or world-accessible (any of the group/other read **or** write bits)
— the file must effectively be `0600`. Fix with:

```bash
chmod 600 ~/.config/r7vr/key
```

The file must exist, must not be a directory, must not be empty, and must
contain the key on a single line (trailing whitespace/newline is
stripped). Any violation is a configuration error, exit `2`.

```bash
# Analyst (WSL) session setup — key file outside any git working tree:
export R7_VR_API_KEY_FILE=~/.config/r7vr/key
export R7_VR_REGION=us
export R7_VR_ORG_ID=11111111-2222-3333-4444-555555555555
```

### Region slugs

`R7_VR_REGION` is your Insight platform region: two lowercase letters
optionally followed by one digit (the value is lowercased for you).
Known slugs: `us`, `us2`, `us3`, `eu`, `ca`, `au`, `ap`. The check is
**format-based, not an allowlist**, so a new Rapid7 region works without a
code change; the known list only feeds the error hint.

### Org ID

`R7_VR_ORG_ID` must be UUID-formatted and is lowercased. Region and org ID
together determine the base URL:

```
https://{region}.api.insight.rapid7.com/insight-velociraptor/v1/orgs/{orgId}
```

(In this repo's docs and tests, UUIDs are always placeholders — every hex
group a single repeated character, like the example above. A pre-commit
hook rejects anything else as a potential key.)

### `R7_VR_AUDIT_LOG` — central audit log

When set, every `vr ops` command appends one JSON line per invocation to
this path: timestamp (`ts`), operator identity (`user`, `host`), the
command, its argv (defensively scrubbed of the key — which cannot be an
argument in the first place), the process ID, and any hunt/flow IDs created.
The argv scrub reads the live secret registry, so it strips the key
**regardless of source** — whether it came from `R7_VR_API_KEY` or
`R7_VR_API_KEY_FILE`. Parent directories are created if needed; the file is
only ever appended to.

This is **in addition to** the `audit.jsonl` written inside each evidence
directory by the collection verbs (`vr ops triage`, `vr ops live`).
Commands without an evidence directory (`enrich`, `ioc-hunt`, `contain`,
`release`, and triage `--start`/non-terminal `--check`) audit only to the
central log — so set it on the SOAR host. Keep audit logs outside any git
working tree (`.gitignore` blocks the common paths anyway).

### `R7_VR_QUARANTINE_DENY_LABELS` — quarantine guardrail

`vr ops contain --quarantine` refuses to quarantine a client that carries
any label in the deny set, **client-side, before any mutating request is
sent** (exit `2`). The deny set is the built-ins `critical-infrastructure`
and `no-quarantine`, plus this comma-separated variable, plus any
`--deny-label` options. Matching is case-insensitive.

## Output contract

SOAR steps depend on this; it holds for every command:

- **stdout** carries exactly **one JSON document** per invocation — even on
  failure, where it is `{"error": {"type": ..., "message": ...,
  "exit_code": ...}}`.
- `--output table` (a global flag: `vr --output table clients list`) is the
  opt-in human format. (`vr artifacts get --format yaml` is the other
  explicit non-JSON output.)
- **stderr** carries logs and progress only — never secrets. `--verbose`
  enables debug logging on stderr, always redacted. The human-readable
  error line also goes to stderr.

## Exit codes

`vr` implements the PLAN.md §3 contract (see `src/vrcli/errors.py`);
workflows branch on these:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | unexpected internal error (a bug — stdout still carries one JSON error document, never a bare traceback) |
| `2` | usage error or invalid environment configuration |
| `3` | auth or permission denied (HTTP 401/403) |
| `4` | not found (HTTP 404, or a hostname that resolved to zero clients) |
| `5` | wait-timeout: a `--wait` poll loop exceeded `--timeout` |
| `6` | API error (any other 4xx/5xx; also a flow that ends in ERROR) |
| `7` | network, DNS, TLS, or timeout failure before an HTTP response |

Code `1` is the catch-all: any unexpected exception is still reported as one
JSON document on stdout with an in-contract exit code, so a SOAR step can
always parse the result.

## Waiting: `--wait`, `--timeout`, `--poll-interval`

Long-running verbs (`vr flows create`, `vr hunts create`, `vr ops triage`,
`vr ops ioc-hunt`) take `--wait` to block until the flow/hunt completes,
so a single SOAR step can cover the whole operation:

- `--timeout SEC` — give up after this many seconds (default **600**) and
  exit `5`. The operation itself keeps running server-side.
- `--poll-interval SEC` — seconds between polls (default **10**).

`vr ops live` always waits (same `--timeout`/`--poll-interval` options,
no `--wait` flag). `vr ops triage` additionally offers the resume contract
for SOAR step-duration caps: `--start` returns an opaque token, and
`--check <token>` polls once per invocation — the terminal `--check`
writes the evidence directory exactly as `--wait` would.

## Pagination: `--limit`, `--all`, `--page-budget`

Paged list commands accept:

- `--limit N` — maximum results to return.
- `--all` — follow cursors and fetch every page.
- `--page-budget N` — cap on pages fetched with `--all` (default **100**).
  Exceeding the budget is an **error** (exit `2`), never a silent
  truncation: narrow the query or raise the budget.

## `--dry-run`

Every mutating command takes `--dry-run`: it prints the exact API request
(method, URL, params, body — with anything secret-shaped redacted) as the
command's JSON output **without sending it**, and writes no evidence and
no flow/hunt-creation audit records. Invaluable when developing SOAR
workflows against production. Note that read-only requests still happen
(e.g. hostname resolution and the label fetch that feeds the quarantine
guardrail, so a dry run still surfaces a guardrail refusal).

## TLS and corporate proxies

**TLS certificate verification cannot be disabled.** There is no flag,
parameter, or environment variable in this codebase that turns it off.
Corporate proxy / TLS-inspection CA bundles are supported only through the
standard mechanisms httpx honors:

```bash
export SSL_CERT_FILE=/etc/ssl/certs/corp-proxy-ca.pem
# or
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/corp-proxy-ca.pem
```

A certificate-verification failure is a network error (exit `7`) and is
deliberately not retried.

## HTTP behavior (fixed, not configurable)

Connect timeout 10 s, read timeout 60 s. 429 and 5xx responses are retried
up to 4 times with capped exponential backoff + jitter, honoring
`Retry-After` when present — a runaway SOAR loop cannot hammer the tenant.
The API key only ever appears in the `X-Api-Key` request header.
