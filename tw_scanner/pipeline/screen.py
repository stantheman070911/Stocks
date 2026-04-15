"""Pipeline entrypoint scaffold."""

from tw_scanner.utils.logging import log_stage


def screen(*, dry_run: bool = False) -> str:
    """Run or validate the screening pipeline scaffold."""
    log_stage("pipeline.screen", rows_in=0, rows_out=0, duration_ms=0, warnings=[])
    if dry_run:
        return "Phase 0 dry run complete."
    return "Phase 0 scaffold installed; production screen arrives in later phases."
