# Development guide

Contributor guide for `vrcli` (the `vr` CLI). Covers dev setup, the test
suite, the pre-commit hooks (which **are** the CI), the auto-generated CLI
reference, spec-drift checking, the recipe for adding an endpoint, and the
release process.

Related reading: [installation.md](installation.md) (end-user install and
tag-pinning), [configuration.md](configuration.md) (env vars, output and
exit-code contracts), [architecture.md](architecture.md) (the design and how
the codebase was built), [api-coverage.md](api-coverage.md) (spec operation ↔
command map), [../SECURITY.md](../SECURITY.md), [../PLAN.md](../PLAN.md), and
[../TODO.md](../TODO.md).

---

## 1. Dev environment

Clone, create a virtualenv, install editable with the `dev` extras, and
install the git hooks. The hooks are **required** — there is no separate CI
runner (see §3), so the only thing standing between a bad commit and the
repo is `pre-commit`.

```bash
git clone <repo-url> velociraptor_cli
cd velociraptor_cli

python3 -m venv .venv
source .venv/bin/activate

pip install -e '.[dev]'         # runtime + dev deps from pyproject.toml
pre-commit install --install-hooks
```

`pip install -e '.[dev]'` pulls the runtime deps (`httpx`, `click`, `PyYAML`)
plus the dev extras declared in `pyproject.toml` `[project.optional-dependencies]`:
`pytest`, `respx`, `ruff`, `pre-commit`, `detect-secrets`, `pip-tools`.
Python 3.10+ is required (`requires-python = ">=3.10"`). The editable install
also installs the `vr` console script (`[project.scripts]` → `vrcli.cli.main:main`),
so `vr --help` works immediately.

**WSL environment note.** On Debian/Ubuntu WSL the system Python is
externally managed (PEP 668), so two wrinkles are common:

- Creating the venv may need the OS package first:
  `sudo apt install python3-venv`.
- If you skip the venv and install for your user, system `pip` will refuse
  unless you pass `--break-system-packages`, e.g.
  `pip install --user --break-system-packages -e '.[dev]'`. A venv is the
  cleaner path; the flag is only for analyst boxes where a venv is awkward.

No real API key, network access, or Rapid7 tenant is needed to develop or
test — the suite is fully mocked (§2).

---

## 2. Running tests

```bash
python3 -m pytest          # 341 tests; pytest config lives in pyproject.toml
```

`[tool.pytest.ini_options]` sets `testpaths = ["tests"]` and `addopts = "-q"`,
so a bare `python3 -m pytest` collects the right tree and runs quietly. The
whole suite is **offline**: HTTP is mocked with `respx`, no network is
touched, and **no real API key** is ever used.

### The conftest fixtures

`tests/conftest.py` is the shared harness. The pieces you will use in almost
every test:

- **`SENTINEL_KEY`** — `"11111111-2222-3333-4444-555555555555"`, the
  redaction canary. It is a placeholder UUID (see §3) used as the fake API
  key; redaction tests assert it **never** appears in captured stdout or
  stderr on any path (success, an auth echo, a 500 echo, verbose logging).
- **`BASE`** — the exact base URL the vendored spec's server template expands
  to for the fake `us` tenant and placeholder org ID. `respx` is mounted at
  this base.
- **`ENV`** — the fake-tenant environment (`R7_VR_API_KEY`, `R7_VR_REGION`,
  `R7_VR_ORG_ID`). Tests that go through `main()` inject it with
  `monkeypatch.setenv`; the `runner` fixture bakes it into the `CliRunner`.
- **`config` / `transport`** — a `Config` (with `Secret(SENTINEL_KEY)`) and an
  open `VRTransport` for testing API bindings directly.
- **`mock_api`** — a `respx` router (`assert_all_called=False`) mounted at
  `BASE`. Assert on `route.calls.last.request` (method, `url.raw_path`,
  `url.params`, `content`) to pin the exact request a binding sends.
- **`runner`** — a `click.testing.CliRunner` pre-loaded with `ENV`.
- **`invoke`** — `invoke("clients", "list") -> (result, parsed_stdout_json)`.
  It calls `runner.invoke(cli, [...], catch_exceptions=False)` and parses
  stdout as JSON. Use this for **success-path** assertions.
- **`page(data, cursor=None)`** — builds a `{size, cursor, data}` envelope
  exactly like the API returns, for mocking paginated list endpoints.

### The exit-code testing convention (important)

There are two ways to drive a command, and they are **not** interchangeable:

- **Success paths go through the `invoke` fixture.** It passes
  `catch_exceptions=False`, which means a typed `VRError` raised inside a
  command would propagate out of `runner.invoke` as a real exception instead
  of being mapped to an exit code. That is fine for the happy path (no error
  is raised) and gives clean assertions on the parsed JSON.

- **Error paths (and the exit codes they produce) go through `main()`.** The
  exit-code contract lives entirely in `vrcli.cli.main.main()` — it catches
  `VRError`/`ClickException`/the catch-all and maps each to a code (see
  `src/vrcli/errors.py`: 2 usage/config, 3 auth, 4 not-found, 5 wait-timeout,
  6 API, 7 network, 1 unexpected-internal). Because `invoke` deliberately
  does **not** catch exceptions, you cannot observe those codes through it.
  Error tests therefore call `main(["labels", "list", CLIENT_ID])` directly,
  capture stdout/stderr with `capsys`, and assert on both the returned code
  and the single JSON error document on stdout.

The common pattern in the CLI test files (e.g. `tests/test_cli_labels.py`,
`tests/test_cli_main.py`):

```python
def run_main(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err

def test_list_404_is_exit_4(env, mock_api, capsys):
    mock_api.get(f"/clients/{CLIENT_ID}/labels").respond(404, json="...")
    code, out, _err = run_main(capsys, "labels", "list", CLIENT_ID)
    assert code == 4
    assert json.loads(out)["error"]["type"] == "NotFoundError"
```

Follow this split when you add tests: assert the **shape** of a successful
result via `invoke`, and assert **exit codes** via `main()`.

---

## 3. The pre-commit hooks (the CI)

`.pre-commit-config.yaml` is the CI. PLAN.md §1 records that no hosted CI
runner is available, so every check that would normally run in CI runs as a
**blocking** pre-commit hook. Install them once per clone
(`pre-commit install --install-hooks`) and run the full set at any time with:

```bash
pre-commit run --all-files
```

The hooks, in order, and what each blocks:

1. **gitleaks** (`gitleaks/gitleaks`) — scans the diff for secret-shaped
   strings (tokens, keys, high-entropy blobs) and blocks the commit on a hit.
2. **detect-secrets** (`Yelp/detect-secrets`) — a second, baseline-aware
   secret scanner. Runs with `--baseline .secrets.baseline` so known,
   reviewed non-secrets don't re-trigger, and `exclude: ^spec/` so the large
   vendored OpenAPI document isn't rescanned on every commit. If you
   intentionally add a string it flags, audit and update the baseline with
   `detect-secrets scan --baseline .secrets.baseline` (review the diff).
3. **ruff** (`astral-sh/ruff-pre-commit`) — runs with `--fix`, so it
   auto-formats/auto-fixes where it safely can and fails on the rest. Lint
   config is in `pyproject.toml` `[tool.ruff]`: line length 100,
   `target-version = "py310"`, selected rules `E, F, W, I, UP, B` plus the
   security rules `S110, S105, S106, S107` (no silently-swallowed excepts, no
   hardcoded secrets); `E501` is ignored, and tests waive `S105/S106/S107`
   (fixtures legitimately carry the fake key).
4. **`no-insight-api-keys`** (local, `scripts/check_no_keys.py`) — rejects
   any UUID that isn't an obvious placeholder. Rapid7 Insight API keys are
   UUID-formatted, so a real-looking UUID anywhere in the tree is treated as
   a possible leaked key. **The placeholder rule:** a UUID passes only if
   every hyphen-separated hex group is a single repeated character — e.g.
   `11111111-2222-3333-4444-555555555555` or
   `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee`. Use that style for all keys and
   org IDs in docs and tests; mixed-hex UUIDs fail the commit.
5. **`cli-docs-fresh`** (local, `scripts/gen_cli_docs.py --check`) — fails the
   commit if `docs/cli-reference.md` no longer matches the live click tree
   (`pass_filenames: false`; triggered on changes under `src/vrcli/`, the
   generator, or the reference itself). See §4.

### The `allowlist-secret` pragma

`detect-secrets` flags `R7_VR_API_KEY` in `src/vrcli/config.py` as a possible
secret — but it is an environment-variable **name**, not a value. Those two
lines carry an inline pragma so the scanner stands down:

```python
ENV_API_KEY = "R7_VR_API_KEY"  # pragma: allowlist secret — env var *name*, not a secret
ENV_API_KEY_FILE = "R7_VR_API_KEY_FILE"  # pragma: allowlist secret
```

Use this pragma sparingly and only for provable false positives (an env-var
name, a documented example placeholder) — never to silence a real value.

---

## 4. The generated CLI reference

`docs/cli-reference.md` is **auto-generated** by `scripts/gen_cli_docs.py`
from the live click command tree — it walks `vrcli.cli.main:cli`, renders
each command's `--help` at a fixed terminal width, and writes the result.
**Never hand-edit it.** The file header says so, and the `cli-docs-fresh`
hook (§3) enforces it.

Regenerate after any change that affects the CLI surface (a new command, a
renamed option, an edited help string):

```bash
python3 scripts/gen_cli_docs.py            # rewrites docs/cli-reference.md
python3 scripts/gen_cli_docs.py --check    # exit 1 if stale (what the hook runs)
```

If a commit is rejected with "`docs/cli-reference.md` is stale", run the
generator (no `--check`), then re-stage and re-commit.

---

## 5. Spec-drift checking

`scripts/check_spec_drift.py` re-downloads the official Velociraptor OpenAPI
spec and diffs it against the vendored copy `spec/velociraptor-api-v1.yaml`
(the contract `api-coverage.md` maps against). It is fully standalone — it
imports nothing from `vrcli` and never reads the `R7_VR_*` env vars; the
published spec is a public document fetched with no auth header.

Two comparison layers:

- **Operation inventory** — the sets of `(METHOD, path, operationId)` from
  both documents; added/removed operations are reported explicitly. This is
  how we'd notice Rapid7 shipping new API surface (e.g. the file-download
  endpoint that is the known gap in PLAN.md §2 / `api-coverage.md`).
- **Full-text unified diff** for everything else (schemas, parameters,
  descriptions); summarized by default, printed in full with `--verbose`.

```bash
python3 scripts/check_spec_drift.py            # report drift
python3 scripts/check_spec_drift.py --verbose  # full unified diff
python3 scripts/check_spec_drift.py --update    # re-vendor the spec, then commit the diff
```

Exit codes (the vrcli convention, duplicated locally so cron/SOAR wrappers
see one scheme): `0` no drift, `1` drift found (also returned with
`--update`, so wrappers still notice), `2` usage error (vendored spec
missing/unreadable), `7` network failure downloading the spec.

Run it **monthly and before every release** (PLAN.md §6). The pure
comparison layer is unit-tested in `tests/test_spec_drift.py` (no network).
This is a scheduled chore with **no named owner yet** — assigning one is an
open item in [../TODO.md](../TODO.md).

---

## 6. Adding a new endpoint / command

The canonical pattern to copy is the clients module:
`src/vrcli/api/clients.py` (the pure binding) and `src/vrcli/cli/clients.py`
(the thin command). The codebase is split into a **library layer**
(`src/vrcli/api/`, `src/vrcli/transport.py`, ...) and a **CLI layer**
(`src/vrcli/cli/`); keep that separation.

**Step 1 — add the API binding** to the right `src/vrcli/api/<group>.py`.
Bindings are pure functions: `transport: VRTransport` is the first argument,
they call `transport.request(METHOD, path, ...)`, and they URL-encode every
user-supplied path segment with `quote(value, safe="")` (clients.py wraps
this in a small `_seg()` helper; labels.py inlines `quote(...)`). This is not
optional — it is the path-traversal guard. Cite the spec `operationId` in the
docstring:

```python
def get_client(transport: VRTransport, client_id: str) -> Any:
    """GET /clients/{clientId}"""
    return transport.request("GET", f"/clients/{_seg(client_id)}")
```

For list endpoints, add both a single-page function and an `iter_*` wrapper
that drives `pagination.paginate(...)`, following `list_clients` /
`iter_clients`.

**Step 2 — add the click command** to `src/vrcli/cli/<group>.py`. Commands
are thin: resolve arguments, call the binding, and `emit()` the result
**exactly once** (`emit` is the single output funnel; the JSON-only stdout
contract depends on every command going through it). Reuse the decorators
from `cli/_common.py`:

- `@dry_run_option` on any **mutating** verb (PUT/POST/PATCH/DELETE). The
  transport short-circuits a mutating request under `--dry-run` and emits a
  `{dry_run, method, url, ...}` description instead of sending it — your
  command needs no special-casing, but the tests assert nothing was sent.
- `@yes_option` plus `require_yes(yes, "...")` on any **destructive** verb
  (there is no interactive prompt; `--yes` is mandatory).
- `resolve_client_arg(transport, value)` wherever an argument accepts a
  client ID **or** a hostname (`C.*` is used verbatim; `host:` forces
  hostname resolution; anything else is auto-resolved). It raises a typed
  `APIError`/`NotFoundError`/`UsageError`, never a bare `KeyError`.

**Step 3 — add tests.** Mirror `tests/test_api_<group>.py` (assert the exact
method / `raw_path` / params / body via `respx`) and
`tests/test_cli_<group>.py` (success via `invoke`, exit codes via `main()` —
see §2). Always include a path-encoding regression test for any user path
segment, and a `--dry-run` test asserting `len(mock_api.calls) == 0`.

**Step 4 — regenerate docs.** `python3 scripts/gen_cli_docs.py` (§4), and
update `docs/api-coverage.md` to map the new spec operation to its command.

**Minimal-dependency policy.** `pyproject.toml` documents it and PLAN.md §3
mandates it: the runtime deps are `httpx`, `click`, `PyYAML` and that's the
budget. Every **new runtime dependency** requires a written justification in
the PR. Dev-only tooling goes in the `dev` extra, not `dependencies`.

---

## 7. Release process

Releases are git tags (the SOAR orchestrator pins a tested tag and never
tracks the default branch — see [installation.md](installation.md)). To cut
one:

1. Bump the version in **both** places — they must match (CI / `vr --version`
   read the second one): `version` in `pyproject.toml` and `__version__` in
   `src/vrcli/__init__.py`. Follow SemVer.
2. Move the `## [Unreleased]` notes in `CHANGELOG.md` under a new dated
   version heading and refresh the compare links at the bottom (the file
   follows Keep a Changelog + SemVer).
3. **Regenerate the CLI reference** — `python3 scripts/gen_cli_docs.py`. Its
   header embeds `__version__`, so a version bump makes it stale and the
   `cli-docs-fresh` hook will block the commit until you regenerate.
4. Run the release gate: `python3 -m pytest`, `pre-commit run --all-files`,
   and `python3 scripts/check_spec_drift.py` (PLAN.md §6 requires the
   drift check before release).
5. Commit, then tag the release (`git tag -a vX.Y.Z`) and push both `main`
   and the tag.

---

## 8. Repo conventions

- **JSON-only stdout.** Exactly one JSON document per invocation (errors
  included, as `{"error": {...}}`); `--output table` is the opt-in human
  format. Everything else — logs, progress — goes to stderr. Every command
  emits through `emit()` once.
- **The exit-code contract.** `0` ok, `1` unexpected internal (still a JSON
  doc, never a bare traceback), `2` usage/config, `3` auth, `4` not-found,
  `5` wait-timeout, `6` API error, `7` network. Defined in
  `src/vrcli/errors.py`, enforced in `cli/main.py`; SOAR branches on these.
- **Evidence and audit always.** Composite ops write an evidence directory
  with a hashed `manifest.json` and an `audit.jsonl` that is itself hashed
  into the manifest (chain-of-custody). See [architecture.md](architecture.md).
- **No secrets, ever.** Environment-only config, the `Secret` wrapper, argv
  and log redaction, and placeholder UUIDs only in docs/tests (§3). See
  [../SECURITY.md](../SECURITY.md).

A note on workflow: `CLAUDE.md` routes certain tasks (planning, review,
ship/deploy, investigation, QA) to gstack skills via the Skill tool — see its
skill-routing table when you want the project's preferred path for those.
