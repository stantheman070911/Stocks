"""Run manifest helpers."""

from tw_scanner import __version__


def base_manifest() -> dict[str, str]:
    """Return the minimum manifest payload shared by pipeline commands."""
    return {"version": __version__}
