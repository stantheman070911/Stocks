"""Pydantic configuration models — validated at startup, fails fast on invalid config."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class UniverseConfig(BaseModel):
    """Stock-universe and liquidity-filter settings."""

    market: Literal["twse", "tpex"] = "twse"
    liquidity_turnover_20d_min_ntd: int = Field(
        50_000_000,
        gt=0,
        description="20-day median NT$ turnover floor for inclusion.",
    )
    liquidity_turnover_60d_diagnostic: bool = Field(
        True,
        description="Compute 60-day turnover for diagnostics without gating on it.",
    )
    price_floor_ntd: float = Field(
        10.0,
        gt=0,
        description="Exclude stocks with close price below this threshold (NT$).",
    )
    top_n: int = Field(200, gt=0, description="Maximum rows in candidates.csv output.")
    exclude_industry_categories: list[str] = Field(
        default_factory=lambda: ["金融保險"],
        description="TaiwanStockInfo.industry_category values to exclude from universe.",
    )


class DataConfig(BaseModel):
    """Data-source, caching, and throttle settings."""

    cache_dir: str = Field(".cache", description="Root directory for the parquet cache.")
    finmind_quota_requests_per_hour: int = Field(
        600,
        gt=0,
        description="FinMind API rate limit (free-tier default). Backer/Sponsor may increase.",
    )
    max_workers: int = Field(
        15,
        gt=0,
        le=64,
        description="Thread-pool size for parallel price downloads.",
    )
    lookup_days: int = Field(
        30,
        gt=0,
        description="Days of foreign institutional flow data to aggregate.",
    )
    price_lookback_days: int = Field(
        730,
        gt=0,
        description="Historical OHLCV window (calendar days) for price-based signals.",
    )
    twse_throttle_min_s: float = Field(
        0.7,
        ge=0,
        description="Minimum inter-request delay for TWSE domain (serialised).",
    )
    twse_throttle_max_s: float = Field(
        1.5,
        ge=0,
        description="Maximum inter-request delay for TWSE domain (serialised).",
    )
    other_throttle_min_s: float = Field(
        0.05,
        ge=0,
        description="Minimum per-thread jitter for non-TWSE requests.",
    )
    other_throttle_max_s: float = Field(
        0.15,
        ge=0,
        description="Maximum per-thread jitter for non-TWSE requests.",
    )
    risk_free_rate_fallback: float = Field(
        0.015,
        ge=0,
        description="Annual risk-free rate used when the central-bank feed is unavailable.",
    )
    benchmark_ticker_primary: str = Field(
        "TAIEX",
        description="FinMind TaiwanStockTotalReturnIndex data_id for primary benchmark.",
    )
    benchmark_ticker_secondary: str = Field(
        "0050.TW",
        description="Yahoo Finance ticker for secondary benchmark (Taiwan 50 ETF).",
    )

    @model_validator(mode="after")
    def _throttle_order(self) -> "DataConfig":
        if self.twse_throttle_min_s > self.twse_throttle_max_s:
            raise ValueError(
                "twse_throttle_min_s must be <= twse_throttle_max_s"
            )
        if self.other_throttle_min_s > self.other_throttle_max_s:
            raise ValueError(
                "other_throttle_min_s must be <= other_throttle_max_s"
            )
        return self


class SignalsConfig(BaseModel):
    """Signal lookback windows and indicator parameters."""

    ma_short_period: int = Field(20, gt=0)
    ma_mid_period: int = Field(60, gt=0)
    ma_long_period: int = Field(120, gt=0)
    atr_period: int = Field(14, gt=0)
    rsi_period: int = Field(14, gt=0)
    adx_period: int = Field(14, gt=0)
    bollinger_period: int = Field(20, gt=0)
    bollinger_std: float = Field(2.0, gt=0)
    mom_1m_days: int = Field(21, gt=0)
    mom_3m_days: int = Field(63, gt=0)
    mom_6m_days: int = Field(126, gt=0)
    mom_12_1_long_days: int = Field(252, gt=0, description="12-month total lookback.")
    mom_12_1_skip_days: int = Field(21, gt=0, description="Skip window to avoid short-term reversal.")
    foreign_flow_short_days: int = Field(5, gt=0)
    foreign_flow_long_days: int = Field(20, gt=0)
    foreign_consec_min: int = Field(
        5,
        gt=0,
        description="Minimum consecutive net-buy days for foreign flow qualification.",
    )
    realized_vol_days: int = Field(60, gt=0)
    drawdown_days: int = Field(120, gt=0)
    vol_surge_ma_days: int = Field(20, gt=0, description="Baseline MA for volume-ratio signal.")

    @model_validator(mode="after")
    def _ma_order(self) -> "SignalsConfig":
        if not (self.ma_short_period < self.ma_mid_period < self.ma_long_period):
            raise ValueError(
                "MA periods must be strictly increasing: ma_short < ma_mid < ma_long"
            )
        if self.mom_12_1_skip_days >= self.mom_12_1_long_days:
            raise ValueError(
                "mom_12_1_skip_days must be less than mom_12_1_long_days"
            )
        return self


class ScoringConfig(BaseModel):
    """Normalisation, missing-data policy, and weights-file pointer."""

    missing_mode: Literal["strict", "penalized", "legacy_reweighted"] = Field(
        "strict",
        description=(
            "strict: missing required signal → row excluded. "
            "penalized: missing → sector_median_z - 0.5. "
            "legacy_reweighted: old behaviour, research comparison only."
        ),
    )
    winsorize_lower: float = Field(
        0.01,
        ge=0,
        lt=0.5,
        description="Lower percentile for winsorisation of raw signal values.",
    )
    winsorize_upper: float = Field(
        0.99,
        gt=0.5,
        le=1,
        description="Upper percentile for winsorisation of raw signal values.",
    )
    zscore_clip: float = Field(
        3.0,
        gt=0,
        description="Absolute clip applied to z-scores after normalisation.",
    )
    weights_file: str | None = Field(
        None,
        description=(
            "Path to a frozen weights YAML produced by Phase 5 calibration. "
            "None = provisional research mode; must be set for production screening."
        ),
    )

    @model_validator(mode="after")
    def _winsorize_order(self) -> "ScoringConfig":
        if self.winsorize_lower >= self.winsorize_upper:
            raise ValueError(
                "winsorize_lower must be strictly less than winsorize_upper"
            )
        return self


class BacktestConfig(BaseModel):
    """Walk-forward backtest parameters and PIT embargo windows."""

    rebalance_cadence: Literal["daily", "weekly", "monthly"] = "weekly"
    horizons: list[int] = Field(
        default_factory=lambda: [5, 10, 20, 60],
        description="Forward-return horizons to evaluate (trading days).",
    )
    is_start: str = Field("2018-01-01", description="In-sample start date (YYYY-MM-DD).")
    is_end: str = Field("2022-12-31", description="In-sample end date (YYYY-MM-DD).")
    oos_start: str = Field("2023-01-01", description="Out-of-sample start date (YYYY-MM-DD).")
    purge_embargo_extra_days: int = Field(
        20,
        ge=0,
        description="Extra trading days added to max(signal lookback) for the purge embargo.",
    )
    fundamentals_embargo_monthly_days: int = Field(
        15,
        ge=0,
        description="Calendar days after monthly revenue period_end before data is usable.",
    )
    fundamentals_embargo_quarterly_days: int = Field(
        60,
        ge=0,
        description="Calendar days after quarterly statements period_end before data is usable.",
    )
    fundamentals_embargo_annual_days: int = Field(
        90,
        ge=0,
        description="Calendar days after fiscal year_end before annual statements are usable.",
    )

    @model_validator(mode="after")
    def _date_order(self) -> "BacktestConfig":
        if self.is_start >= self.is_end:
            raise ValueError("is_start must be before is_end")
        if self.is_end >= self.oos_start:
            raise ValueError("is_end must be before oos_start")
        if not all(h > 0 for h in self.horizons):
            raise ValueError("All backtest horizons must be positive integers")
        return self


class CostsConfig(BaseModel):
    """Transaction cost model for backtest and research."""

    broker_commission_bps: float = Field(
        14.25,
        ge=0,
        description="Broker commission in basis points, applied to both buy and sell legs.",
    )
    transaction_tax_bps: float = Field(
        30.0,
        ge=0,
        description="Securities transaction tax in basis points, applied to sell leg only.",
    )
    slippage_model: Literal["half_spread", "zero"] = Field(
        "half_spread",
        description=(
            "half_spread: estimate slippage as half the high-low range. "
            "zero: no slippage (optimistic; for sensitivity analysis only)."
        ),
    )


class AppConfig(BaseModel):
    """Root configuration model — validated composition of all sub-configs."""

    version: str = "1"
    universe: UniverseConfig = Field(default_factory=lambda: UniverseConfig())
    data: DataConfig = Field(default_factory=lambda: DataConfig())
    signals: SignalsConfig = Field(default_factory=lambda: SignalsConfig())
    scoring: ScoringConfig = Field(default_factory=lambda: ScoringConfig())
    backtest: BacktestConfig = Field(default_factory=lambda: BacktestConfig())
    costs: CostsConfig = Field(default_factory=lambda: CostsConfig())
