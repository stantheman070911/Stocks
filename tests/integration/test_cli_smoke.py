"""CLI smoke tests."""

from typer.testing import CliRunner

from tw_scanner.cli import app


def test_cli_help() -> None:
    """CLI help renders successfully."""
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Taiwan equity scanner" in result.output


def test_cli_seed_and_dry_run() -> None:
    """Dry-run command succeeds with deterministic seed option."""
    result = CliRunner().invoke(app, ["--seed", "7", "screen", "--dry-run"])
    assert result.exit_code == 0
    assert "Phase 0 dry run complete" in result.output
