# Installation

`vrcli` requires **Python ≥ 3.10** and has three runtime dependencies
(`httpx`, `click`, `PyYAML`). The package name is `vrcli`; the console
script it installs is `vr`.

## Analysts (WSL) — pipx, recommended

pipx gives each tool an isolated venv and an easy upgrade path
(PLAN.md §9.8).

On WSL Ubuntu, pipx needs `python3-venv`:

```bash
sudo apt update
sudo apt install pipx python3-venv
pipx ensurepath        # puts ~/.local/bin on PATH; restart the shell after
```

Then install from the internal git remote:

```bash
pipx install git+https://github.com/bodenpat/velociraptor_cli
vr --version
```

Upgrade later with:

```bash
pipx upgrade vrcli
```

After installing, configure your environment variables
([configuration.md](configuration.md), [SECURITY.md](../SECURITY.md)) and
run `vr status` — exit code `0` confirms auth, region, and org ID against
the live tenant.

## Fallback — `pip install --user`

If pipx is unavailable:

```bash
python3 -m pip install --user git+https://github.com/bodenpat/velociraptor_cli
```

Make sure `~/.local/bin` is on your `PATH`. Prefer pipx when you can: the
`--user` site-packages is shared with everything else you pip-install, so
dependency conflicts become your problem.

## InsightConnect orchestrator host — pin a tested tag

The SOAR orchestrator must **never** track the default branch. Pin the tag
the team has tested so workflows don't break on upgrade:

```bash
pipx install git+https://github.com/bodenpat/velociraptor_cli@v0.1.0
```

Upgrading the orchestrator is a deliberate act: test the new tag against a
lab host first, then reinstall with the new tag:

```bash
pipx install --force git+https://github.com/bodenpat/velociraptor_cli@v0.2.0
```

Releases are git tags (Keep a Changelog + SemVer — see
[CHANGELOG.md](../CHANGELOG.md)). The key reaches the orchestrator as an
env var injected from the InsightConnect credential store at step execution
time — see [SECURITY.md](../SECURITY.md); nothing key-related is installed
or written to disk.

## Development setup

```bash
git clone https://github.com/bodenpat/velociraptor_cli
cd velociraptor_cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pre-commit install --install-hooks
```

**`pre-commit install --install-hooks` is REQUIRED, not optional.** There
is no CI runner: the pre-commit hooks *are* the CI (PLAN.md §1). They run
`gitleaks`, `detect-secrets`, `ruff`, a hook that rejects any
non-placeholder UUID (the Insight API key format — in docs and tests every
hex group must be a single repeated character, e.g.
`11111111-2222-3333-4444-555555555555`), and a docs-freshness check that
regenerates `docs/cli-reference.md` from the click tree and fails the
commit if it changed. A clone without hooks has **no** secret protection.

Run the tests (pytest + respx; fully mocked, no network, no real key):

```bash
python3 -m pytest
```

Every new runtime dependency requires a written justification in the PR
(PLAN.md §3 minimal-dependency policy).
