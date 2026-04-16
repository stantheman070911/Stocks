"""Configuration loader — resolves YAML files, applies env vars, and produces a validated AppConfig."""

import hashlib
from pathlib import Path

import yaml
from dotenv import load_dotenv

from tw_scanner.config.schema import AppConfig

# Package-bundled defaults: tw_scanner/config/{section}.yaml
_PACKAGE_CONFIG_DIR = Path(__file__).parent

# Top-level project config/ directory (user overrides)
_PROJECT_ROOT = _PACKAGE_CONFIG_DIR.parent.parent
_USER_CONFIG_DIR = _PROJECT_ROOT / "config"

_SUB_CONFIGS = ("universe", "data", "signals", "scoring", "backtest", "costs")


def _load_yaml(path: Path) -> dict:
    """Read a YAML file; return empty dict if the file is absent or empty."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        result = yaml.safe_load(fh)
    return result if isinstance(result, dict) else {}


def _merge(section: str, user_dir: Path) -> dict:
    """Merge package default with user override (user wins on key conflicts)."""
    base = _load_yaml(_PACKAGE_CONFIG_DIR / f"{section}.yaml")
    override = _load_yaml(user_dir / f"{section}.yaml")
    base.update(override)
    return base


def load_config(user_config_dir: Path | None = None) -> AppConfig:
    """Load and validate the full application configuration.

    Resolution order (later wins):
      1. tw_scanner/config/{section}.yaml  — package defaults
      2. config/{section}.yaml             — top-level project overrides (or user_config_dir)

    Environment variables:
      FINMIND_TOKEN is NOT stored in AppConfig; it is read from the environment
      directly by the data layer (tw_scanner.data.finmind_client).
      load_dotenv() is called here so a .env file is populated into os.environ
      before any other module reads it.

    Args:
        user_config_dir: Path to the directory holding user YAML overrides.
                         Defaults to <project_root>/config/.
    """
    load_dotenv()

    user_dir = user_config_dir if user_config_dir is not None else _USER_CONFIG_DIR

    composed: dict = {}
    for section in _SUB_CONFIGS:
        composed[section] = _merge(section, user_dir)

    return AppConfig.model_validate(composed)


def resolved_config_hash(cfg: AppConfig) -> str:
    """Return the SHA-256 hex digest of the fully resolved, validated configuration.

    The hash is included in every run manifest to ensure reproducibility.
    Serialisation uses model_dump_json (compact, deterministic field order).
    """
    canonical = cfg.model_dump_json()
    return hashlib.sha256(canonical.encode()).hexdigest()
