"""`vr` entry point: root group, global options, exit-code mapping."""

from __future__ import annotations

import sys

import click

from .. import __version__
from ..errors import EXIT_USAGE, VRError
from ._common import AppCtx, emit, emit_error, pass_app, setup_logging


@click.group(name="vr", context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="vr")
@click.option(
    "--output",
    type=click.Choice(["json", "table"]),
    default="json",
    show_default=True,
    help="Output format. stdout is one JSON document by default; table is for humans.",
)
@click.option("--verbose", is_flag=True, help="Debug logging on stderr (always redacted).")
@click.pass_context
def cli(ctx: click.Context, output: str, verbose: bool) -> None:
    """CLI for the Rapid7-hosted Velociraptor REST API.

    Configuration is environment-only: R7_VR_API_KEY (or R7_VR_API_KEY_FILE),
    R7_VR_REGION, R7_VR_ORG_ID. There is no --api-key flag by design.
    """
    app = ctx.ensure_object(AppCtx)
    app.output = output
    app.verbose = verbose
    setup_logging(verbose)
    ctx.call_on_close(app.close)


@cli.command()
@pass_app
def status(app: AppCtx) -> None:
    """Auth + connectivity smoke test (one cheap GET). SOAR health check."""
    transport = app.transport()
    transport.request("GET", "/clients", params={"limit": 1})
    emit(
        app,
        {
            "status": "ok",
            "region": app.config.region,
            "org_id": app.config.org_id,
            "base_url": app.config.base_url,
            "vrcli_version": __version__,
        },
    )


def _register_groups() -> None:
    from . import artifacts, clients, flows, hunts, labels, ops

    cli.add_command(clients.clients)
    cli.add_command(labels.labels)
    cli.add_command(hunts.hunts)
    cli.add_command(flows.flows)
    cli.add_command(artifacts.artifacts)
    cli.add_command(ops.ops)


_register_groups()


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point implementing the exit-code contract."""
    try:
        cli.main(args=argv, standalone_mode=False)
        return 0
    except click.exceptions.Exit as exc:  # --help / --version
        return int(exc.exit_code)
    except click.exceptions.Abort:
        click.echo("Aborted.", err=True)
        return EXIT_USAGE
    except click.exceptions.ClickException as exc:
        emit_error({"type": "UsageError", "message": exc.format_message(), "exit_code": EXIT_USAGE})
        exc.show(file=sys.stderr)
        return EXIT_USAGE
    except VRError as exc:
        emit_error(exc.to_dict())
        click.echo(f"error: {exc.message}", err=True)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
