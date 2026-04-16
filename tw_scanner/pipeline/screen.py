"""Pipeline entrypoint — loads config and orchestrates screening stages."""

from tw_scanner.config import load_config, resolved_config_hash
from tw_scanner.utils.logging import log_stage


def screen(*, dry_run: bool = False) -> str:
    """Load config, validate it, and (in later phases) run the full screening pipeline.

    Args:
        dry_run: When True, validate bootstrap without running the scanner.

    Returns:
        A human-readable summary string echoed by the CLI.
    """
    cfg = load_config()
    config_hash = resolved_config_hash(cfg)

    log_stage(
        "pipeline.screen",
        rows_in=0,
        rows_out=0,
        duration_ms=0,
        warnings=[] if cfg.scoring.weights_file else ["weights_file is null — research mode only"],
    )

    if dry_run:
        return (
            f"Phase 1 dry run complete.\n"
            f"  config_hash : {config_hash}\n"
            f"  missing_mode: {cfg.scoring.missing_mode}\n"
            f"  universe    : {cfg.universe.market}, top_n={cfg.universe.top_n}, "
            f"price_floor=NT${cfg.universe.price_floor_ntd}"
        )

    # Phases 2–8 will fill this in.
    return (
        f"Screen scaffold installed. Config loaded OK (hash={config_hash[:12]}…).\n"
        "Production pipeline arrives in later phases."
    )
