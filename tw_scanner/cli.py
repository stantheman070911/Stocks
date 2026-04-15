"""Command-line interface for the Taiwan scanner."""

import random
from typing import Annotated

import numpy as np
import typer

from tw_scanner import __version__
from tw_scanner.pipeline.screen import screen
from tw_scanner.utils.logging import configure_logging, log_stage

app = typer.Typer(
    help=f"Taiwan equity scanner bootstrap CLI (version {__version__}).",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the package version and exit.",
        ),
    ] = False,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Seed Python random and NumPy for deterministic runs."),
    ] = None,
) -> None:
    """Configure process-wide bootstrap options."""
    configure_logging()
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        log_stage("bootstrap.seed", rows_in=0, rows_out=0, duration_ms=0, warnings=[])


@app.command("screen")
def screen_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate CLI bootstrap without running the scanner."),
    ] = False,
) -> None:
    """Run the scanner pipeline."""
    result = screen(dry_run=dry_run)
    typer.echo(result)


def main() -> None:
    """Console script entrypoint."""
    app()


if __name__ == "__main__":
    main()
