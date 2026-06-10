"""`vr artifacts` — 1:1 with the Artifact API."""

from __future__ import annotations

import click
import yaml

from ..api import artifacts as api
from ..errors import UsageError
from ._common import AppCtx, dry_run_option, emit, pass_app, require_yes, yes_option

_file_option = click.option(
    "--file",
    "file_",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Artifact definition YAML file.",
)


def _load_artifact_yaml(path: str) -> tuple[str, str]:
    """Read an artifact YAML file and validate it locally before upload.

    The parse is validation only — the RAW text is what gets sent, so the
    server sees exactly what the analyst wrote (comments and all). Returns
    (raw_text, artifact_name); UsageError if it does not parse or lacks the
    spec-required `name` + `sources` keys.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UsageError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(parsed, dict):
        raise UsageError(f"{path} must contain a YAML mapping (an artifact definition)")
    missing = [key for key in ("name", "sources") if key not in parsed]
    if missing:
        raise UsageError(f"{path} is missing required artifact key(s): {', '.join(missing)}")
    return text, str(parsed["name"])


@click.group()
def artifacts() -> None:
    """Inspect and manage artifact definitions."""


@artifacts.command(name="list")
@click.option(
    "--type",
    "type_",
    type=click.Choice(
        ["client", "client_event", "server", "server_event", "notebook"], case_sensitive=False
    ),
    default=None,
    help="Filter by artifact type.",
)
@click.option(
    "--os",
    "os_",
    type=click.Choice(["windows", "linux", "darwin"], case_sensitive=False),
    default=None,
    help="Filter by operating system.",
)
@click.option(
    "--builtin/--no-builtin",
    "builtin",
    default=None,
    help="Include or exclude built-in artifacts (default: server decides).",
)
@click.option(
    "--custom/--no-custom",
    "custom",
    default=None,
    help="Include or exclude custom artifacts (default: server decides).",
)
@pass_app
def list_(app: AppCtx, type_, os_, builtin, custom) -> None:
    """List artifacts."""
    emit(
        app,
        api.list_artifacts(
            app.transport(),
            type=type_,
            os=os_,
            include_builtin=builtin,
            include_custom=custom,
        ),
    )


@artifacts.command()
@click.argument("name")
@click.option(
    "--format",
    "format_",
    type=click.Choice(["json", "yaml"]),
    default="json",
    show_default=True,
    help="yaml prints the raw YAML definition (explicit non-JSON output, like --output table).",
)
@pass_app
def get(app: AppCtx, name: str, format_: str) -> None:
    """Get an artifact definition."""
    result = api.get_artifact(app.transport(), name, format=format_)
    if format_ == "yaml":
        click.echo(result)
    else:
        emit(app, result)


@artifacts.command()
@_file_option
@dry_run_option
@pass_app
def add(app: AppCtx, file_: str, dry_run: bool) -> None:
    """Add a custom artifact from a YAML file."""
    text, name = _load_artifact_yaml(file_)
    result = api.add_artifact(app.transport(dry_run=dry_run), text)
    emit(app, result if result is not None else {"added": name})


@artifacts.command()
@click.argument("name")
@_file_option
@dry_run_option
@pass_app
def update(app: AppCtx, name: str, file_: str, dry_run: bool) -> None:
    """Modify an existing artifact definition."""
    text, yaml_name = _load_artifact_yaml(file_)
    if yaml_name != name:
        raise UsageError(
            f"Artifact name mismatch: command targets {name!r} but {file_} defines {yaml_name!r}"
        )
    result = api.modify_artifact(app.transport(dry_run=dry_run), name, text)
    emit(app, result if result is not None else {"updated": name})


@artifacts.command()
@click.argument("name")
@yes_option
@dry_run_option
@pass_app
def delete(app: AppCtx, name: str, yes: bool, dry_run: bool) -> None:
    """Remove an artifact definition."""
    require_yes(yes, f"delete artifact {name}")
    result = api.delete_artifact(app.transport(dry_run=dry_run), name)
    emit(app, result if result is not None else {"deleted": name})
