"""Tests for load_config() package→user override merge precedence and resolved_config_hash()."""

import hashlib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tw_scanner.config import load_config, resolved_config_hash


def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


class TestLoadConfigMergePrecedence:
    """User-level overrides in top-level config/ must win over package defaults."""

    def test_user_override_top_n(self, tmp_path: Path) -> None:
        """top_n override in user config/universe.yaml takes precedence."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(user_config / "universe.yaml", {"top_n": 99})

        cfg = load_config(user_config_dir=user_config)

        assert cfg.universe.top_n == 99

    def test_user_override_missing_mode(self, tmp_path: Path) -> None:
        """missing_mode override in user config/scoring.yaml takes precedence."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(user_config / "scoring.yaml", {"missing_mode": "penalized"})

        cfg = load_config(user_config_dir=user_config)

        assert cfg.scoring.missing_mode == "penalized"

    def test_package_default_used_when_no_override(self, tmp_path: Path) -> None:
        """Empty user config directory → package defaults are used unchanged."""
        user_config = tmp_path / "config"
        user_config.mkdir()  # 空目錄，無覆蓋檔案

        cfg = load_config(user_config_dir=user_config)

        assert cfg.universe.top_n == 200  # 預設值
        assert cfg.scoring.missing_mode == "strict"

    def test_partial_override_preserves_other_defaults(self, tmp_path: Path) -> None:
        """Overriding one key in a sub-config does not clobber unmentioned keys."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        # 只覆蓋 price_floor_ntd，其他 universe 設定維持預設
        _write_yaml(user_config / "universe.yaml", {"price_floor_ntd": 5.0})

        cfg = load_config(user_config_dir=user_config)

        assert cfg.universe.price_floor_ntd == 5.0
        assert cfg.universe.top_n == 200  # 其他預設值未被影響
        assert cfg.universe.liquidity_turnover_20d_min_ntd == 50_000_000

    def test_invalid_override_raises_validation_error(self, tmp_path: Path) -> None:
        """Invalid user config (negative price floor) must raise a pydantic error."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(user_config / "universe.yaml", {"price_floor_ntd": -1})

        with pytest.raises(Exception, match="greater than 0"):
            load_config(user_config_dir=user_config)


class TestResolvedConfigHash:
    """resolved_config_hash() must be deterministic and sensitive to config changes."""

    def test_deterministic_across_calls(self, tmp_path: Path) -> None:
        """Same config → same hash on repeated calls."""
        user_config = tmp_path / "config"
        user_config.mkdir()

        cfg1 = load_config(user_config_dir=user_config)
        cfg2 = load_config(user_config_dir=user_config)

        assert resolved_config_hash(cfg1) == resolved_config_hash(cfg2)

    def test_hash_changes_on_config_change(self, tmp_path: Path) -> None:
        """Different config → different hash."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        modified_dir = tmp_path / "modified"
        modified_dir.mkdir()

        _write_yaml(modified_dir / "universe.yaml", {"top_n": 50})

        cfg_base = load_config(user_config_dir=base_dir)
        cfg_mod = load_config(user_config_dir=modified_dir)

        assert resolved_config_hash(cfg_base) != resolved_config_hash(cfg_mod)

    def test_hash_is_sha256_hex(self, tmp_path: Path) -> None:
        """Hash must be a 64-character hex string (SHA-256)."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        cfg = load_config(user_config_dir=user_config)
        h = resolved_config_hash(cfg)

        assert len(h) == 64
        int(h, 16)  # must be valid hex — raises ValueError if not

    def test_default_config_hash_is_stable(self, tmp_path: Path) -> None:
        """Default config hash must remain stable across test runs (no randomness)."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        cfg = load_config(user_config_dir=user_config)
        h = resolved_config_hash(cfg)

        # Hash is computed from the serialised config — re-compute independently
        expected = hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()
        assert h == expected


class TestConfigValidators:
    """Pydantic cross-field validators must reject logically invalid configurations."""

    def test_is_end_before_is_start_raises(self, tmp_path: Path) -> None:
        """BacktestConfig must reject is_end <= is_start."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(
            user_config / "backtest.yaml",
            {"is_start": "2023-01-01", "is_end": "2022-01-01", "oos_start": "2024-01-01"},
        )
        with pytest.raises(ValidationError):
            load_config(user_config_dir=user_config)

    def test_oos_before_is_end_raises(self, tmp_path: Path) -> None:
        """BacktestConfig must reject oos_start <= is_end."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(
            user_config / "backtest.yaml",
            {"is_start": "2018-01-01", "is_end": "2022-12-31", "oos_start": "2021-01-01"},
        )
        with pytest.raises(ValidationError):
            load_config(user_config_dir=user_config)

    def test_ma_period_ordering_violation_raises(self, tmp_path: Path) -> None:
        """SignalsConfig must reject out-of-order MA periods."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(
            user_config / "signals.yaml",
            {"ma_short_period": 120, "ma_mid_period": 60, "ma_long_period": 20},
        )
        with pytest.raises(ValidationError):
            load_config(user_config_dir=user_config)

    def test_winsorize_order_violation_raises(self, tmp_path: Path) -> None:
        """ScoringConfig must reject winsorize_lower >= winsorize_upper."""
        user_config = tmp_path / "config"
        user_config.mkdir()
        _write_yaml(
            user_config / "scoring.yaml",
            {"winsorize_lower": 0.8, "winsorize_upper": 0.2},
        )
        with pytest.raises(ValidationError):
            load_config(user_config_dir=user_config)
