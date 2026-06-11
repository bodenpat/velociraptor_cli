# Phase 5 — Native InsightConnect plugin (execute-level plan)

**Goal (PLAN.md §8):** wrap the `vrcli` **library** actions in Rapid7's
`insight-plugin` SDK so the same logic runs as a native InsightConnect plugin
instead of (or alongside) the CLI. The library/CLI split (§3) was designed for
exactly this — the plugin is a **thin adapter over the existing `ops/` and
`api/` functions**, not a reimplementation.

**Trigger:** PLAN.md frames Phase 5 as "future" — pursue it when the CLI exec
path is insufficient (e.g. Phase 4 task A2 failed, or the org standardizes on
native plugins). The plan below is complete regardless of when it starts.

**Definition of done (phase acceptance):** the plugin **validates** with the
SDK, and a **triage action** run through `insight-plugin run` against the lab
host produces the **same evidence directory + JSON** the CLI produces
(`vr ops triage`), with the API key supplied via the plugin **connection**
(never an env var on a command line, never logged).

**Read first:** [../architecture.md](../architecture.md) (the library/CLI
layering and the public functions to wrap), [../api-coverage.md](../api-coverage.md),
[../../SECURITY.md](../../SECURITY.md). External: Rapid7's **InsightConnect
Plugin SDK** docs — they are authoritative for exact `insight-plugin`
subcommand syntax and the `plugin.spec.yaml` schema for the SDK version you
install; confirm with `insight-plugin --help` after install.

---

## Preconditions

- [ ] **`[agent]`** Python 3.10+ and this repo checked out.
- [ ] **`[agent/human]`** The **`insight-plugin` SDK** installed
      (`pip install insight-plugin`) and **Docker** available (the SDK builds
      and runs the plugin in a container).
- [ ] **`[human]`** A decision that Phase 5 is being pursued (see Trigger).

---

## Key integration facts (verified against this codebase)

These are the load-bearing facts that make the adapter thin. The plugin code
must reuse them, not duplicate them:

- **Build the client without env vars.** The CLI loads config from the
  environment (`config.load_config`), but `Config`, `Secret`, and
  `VRTransport` are plain public constructors. The plugin builds the transport
  directly from **connection inputs**:
  ```python
  from vrcli.config import Config, Secret
  from vrcli.transport import VRTransport
  cfg = Config(api_key=Secret(conn["api_key"]), region=conn["region"], org_id=conn["org_id"])
  transport = VRTransport(cfg)
  ```
  This reuses the entire redaction/retry/TLS stack for free.
- **The action bodies already exist** as pure functions taking a
  `VRTransport` and returning a JSON-able dict — one plugin action per
  function:

  | Plugin action | `vrcli` function | Returns |
  |---|---|---|
  | `status` | `transport.request("GET", "/clients", params={"limit": 1})` | health dict |
  | `triage` | `vrcli.ops.triage.run_triage(...)` (or `start_triage`/`check_triage`) | evidence summary |
  | `live_response` | `vrcli.ops.live_response.live_response(...)` | evidence summary |
  | `ioc_hunt` | `vrcli.ops.ioc_hunt.launch_ioc_hunt(...)` + `wait_and_summarize(...)` | hunt summary |
  | `contain` / `release` | `vrcli.ops.containment.contain(...)` / `release(...)` | label/quarantine result |
  | `enrich` | `vrcli.ops.enrich.enrich_host(...)` | enrichment doc |
  | primitives (optional) | `vrcli.api.clients/labels/hunts/flows/artifacts.*` | per-endpoint |

- **Exit codes → plugin errors.** The CLI maps `VRError` subclasses to exit
  codes (`errors.py`). The plugin maps the **same exceptions** to the SDK's
  error type (raise `PluginException` with the `VRError.message` /
  `.to_dict()`), so failure semantics stay identical. Catch `vrcli.errors.VRError`
  in each action's `run()`.
- **Evidence directory in a container.** The CLI writes evidence to a local
  dir. In a containerized plugin, either (a) return the result rows/summary
  inline as the action output (the manifest + rows are already in the return
  dict / on disk), or (b) write to a **mounted/persistent path** the
  orchestrator collects. Decide per deployment; default to returning the
  summary inline and writing evidence to a connection-configured base path.
- **Secrets.** The connection's `api_key` input must be the SDK's
  **`credential_secret_key`** (masked) type; wrap it in `Secret` immediately;
  never log the connection. The existing redaction (`register_secret` runs in
  `VRTransport.__init__`) then covers logging automatically.

---

## Task group A — Scaffold the plugin

- [ ] **A1 `[agent]` Install the SDK and scaffold a workspace.**
      `pip install insight-plugin`; confirm `insight-plugin --help`. Create
      the plugin under `plugin/` in this repo (or a sibling repo per team
      convention).

- [ ] **A2 `[agent]` Author `plugin.spec.yaml`.** Define the plugin metadata,
      the **connection** (the three connection inputs), and one **action per
      row** in the table above. Concrete starting point (adjust field names to
      the installed SDK's schema — confirm against its docs):
      ```yaml
      plugin_spec_version: v2
      name: velociraptor
      title: Rapid7 Hosted Velociraptor
      description: Endpoint collection, hunts, and containment via the hosted Velociraptor REST API
      version: 0.1.0
      vendor: <your-team>
      connection:
        region:
          type: string
          required: true
        org_id:
          type: string
          required: true
        api_key:
          type: credential_secret_key
          required: true
      actions:
        triage:
          title: Triage collection
          input:
            host: { type: string, required: true }
            targets: { type: string, required: false }
            wait: { type: boolean, required: false }
            timeout: { type: integer, required: false }
          output:
            done: { type: boolean }
            client_id: { type: string }
            flow_id: { type: string }
            out: { type: string }
            artifacts: { type: object }
            manifest_files: { type: integer }
        # ... one block per action in the integration table
      ```
      Mirror each action's `input`/`output` to the CLI options and the ops
      function's return dict (see [../soar-playbooks.md](../soar-playbooks.md)
      for the exact return shapes).
      *Gate:* `plugin.spec.yaml` written; the action/connection inputs match
      the CLI surface 1:1.

- [ ] **A3 `[agent]` Generate the scaffold.** Run the SDK's generate step
      (e.g. `insight-plugin create plugin.spec.yaml` — confirm the exact
      subcommand for your SDK version). This produces the Python package with
      a `connection/`, `actions/<name>/`, and the `insightconnect_plugin_runtime`
      base classes.
      *Gate:* the scaffold generates and `insight-plugin validate` (or the
      equivalent) passes on the empty scaffold.

---

## Task group B — Wire `vrcli` into the plugin

- [ ] **B1 `[agent]` Add `vrcli` as a plugin dependency.** Add
      `vrcli==0.1.0` (the pinned tag) to the plugin's `requirements.txt` so the
      generated Docker image installs the published library. Do **not** copy or
      reimplement any logic.
      *Gate:* the plugin image builds with `vrcli` importable.

- [ ] **B2 `[agent]` Implement the connection.** In the generated
      `connection`'s `connect()`, build a `VRTransport` from the connection
      inputs using the snippet in "Key integration facts", store it on
      `self.transport`, and call `transport.request("GET", "/clients",
      params={"limit": 1})` as a connection test (mirrors `vr status`).
      *Gate:* connection test succeeds with valid inputs, raises a
      `PluginException` (not a bare traceback) with bad inputs.

- [ ] **B3 `[agent]` Implement each action's `run(params)`.** Each delegates to
      its `ops`/`api` function with `self.connection.transport` and returns the
      function's dict as the action output. Example (triage):
      ```python
      from vrcli.ops.triage import run_triage, start_triage, check_triage
      from vrcli.errors import VRError

      def run(self, params={}):
          t = self.connection.transport
          try:
              if params.get("token"):
                  return check_triage(t, params["token"])
              if params.get("wait", True):
                  return run_triage(t, params["host"], targets=params.get("targets", "KapeTriage"),
                                    timeout=params.get("timeout", 600), poll_interval=10)
              return start_triage(t, params["host"], targets=params.get("targets", "KapeTriage"))
          except VRError as exc:
              raise PluginException(cause=exc.message, data=exc.to_dict())
      ```
      Repeat for `live_response`, `ioc_hunt`, `contain`, `release`, `enrich`,
      and `status`. Keep the `--dry-run` and quarantine-guardrail behavior:
      pass a `dry_run` input through to `VRTransport(cfg, dry_run=...)`; the
      guardrail in `ops.containment.contain` fires unchanged.
      *Gate:* every action's `run()` returns the same dict its CLI counterpart
      emits for the same inputs.

---

## Task group C — Tests & parity

- [ ] **C1 `[agent]` Unit-test each action** with the SDK's test harness +
      `respx` (reuse the patterns in this repo's `tests/`), asserting the
      action output equals the corresponding `ops`/`api` return for mocked API
      responses. Include a **redaction test**: inject a sentinel key via the
      connection and assert it never appears in the plugin's logs/output.
      *Gate:* action tests pass; sentinel key absent from all output.

- [ ] **C2 `[agent]` Parity check vs the CLI.** For one action (triage), run
      the CLI and the plugin against the same mocked responses and diff the
      output documents.
      *Gate:* outputs match modulo intentional shape differences (documented).

---

## Task group D — Validate, package, publish

- [ ] **D1 `[agent]` Validate & run locally.** `insight-plugin validate` and
      `insight-plugin run` (confirm exact subcommands) with a sample input
      JSON for the `status` and `triage` actions against a mocked or lab
      endpoint.
      *Gate:* validate passes; `run` returns the expected JSON.

- [ ] **D2 `[agent]` Build the image / export the bundle.** Use the SDK's
      package step (e.g. `insight-plugin export`) to produce the plugin
      tarball/Docker image.
      *Gate:* an importable plugin artifact exists.

- [ ] **D3 `[tenant] [human]` Register in InsightConnect.** Import the plugin,
      create a connection with the SOAR service key (credential store), and run
      the `triage` action against the **lab host**.
      *Gate (= phase acceptance):* the action produces the same evidence +
      JSON as `vr ops triage` against the same host; the key is not present in
      any plugin log.

---

## Task group E — Decide CLI vs plugin coexistence

- [ ] **E1 `[human]` Decide the deployment mix.** The CLI stays valuable for
      WSL analysts; the plugin may replace the CLI-on-orchestrator path or run
      alongside it. Record the decision; if the plugin replaces the
      orchestrator CLI, migrate the Phase-4 workflows to plugin steps.
      *Gate:* documented decision; PLAN.md §8 Phase 5 note updated.

> **gRPC / open-source API:** out of scope unless the deployment mix changes
> (PLAN.md §8 Phase 5). Do not add it speculatively.

## Phase-5 completion checklist

- [ ] `plugin.spec.yaml` mirrors the CLI surface (A2).
- [ ] Connection builds a `VRTransport` from connection inputs; `vrcli` reused,
      not reimplemented (B1, B2).
- [ ] Every action delegates to its `ops`/`api` function and maps `VRError`
      to `PluginException` (B3).
- [ ] Action + redaction tests pass; CLI parity checked (C1, C2).
- [ ] Plugin validates, runs, and packages (D1, D2).
- [ ] Triage action acceptance run in InsightConnect matches the CLI (D3).
- [ ] Coexistence decision recorded (E1).
