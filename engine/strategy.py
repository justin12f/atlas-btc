"""Ensambla las capas segun config/strategy.yaml."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from .ml import MLConfig
from .portfolio import PortfolioConfig, ml_promotion, target_exposure
from .signals import trend_score

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path | None = None) -> dict:
    return yaml.safe_load(open(path or ROOT / "config" / "strategy.yaml"))


def portfolio_config(c: dict, usdc_apy: float | None = None, **over) -> PortfolioConfig:
    kw = dict(
        target_vol=c["risk"]["target_vol"], vol_halflife=c["risk"]["vol_halflife"], band=c["risk"]["band"],
        dd_trigger=0.20 if c["risk"]["dd_brake"] else 9.0, regime_penalty=0.0,
        cycle_rule=c["cycle"]["enabled"], cycle_bear_window=tuple(c["cycle"]["bear_window_days"]),
        cycle_bear_mult=c["cycle"]["multiplier"], w_trend=1 - c["ml"]["ml_blend"], w_ml=c["ml"]["ml_blend"],
        fee=c["execution"]["fee"], slippage=c["execution"]["slippage"],
        usdc_apy=c["execution"]["usdc_apy_base"] if usdc_apy is None else usdc_apy,
        kill_dd=c["risk"].get("kill_switch_dd", 9.0), kill_review_days=c["risk"].get("kill_switch_review_days", 30),
    )
    kw.update(over)
    return PortfolioConfig(**kw)


def ml_config(c: dict) -> MLConfig:
    m = c["ml"]
    return MLConfig(horizon=m["horizon"], retrain_days=m["retrain_days"], automl=m["automl"],
                    n_trials=m["optuna_trials"], gate_auc=m["gate_auc"],
                    train_start=c["periods"]["train_start"], oos_start=c["periods"]["dev_start"])


def build_targets(d: pd.DataFrame, ml_pred: pd.DataFrame | None, c: dict, pcfg: PortfolioConfig) -> pd.DataFrame:
    T = trend_score(d)
    core = target_exposure(d, T, None, pcfg)
    if not c["ml"].get("enabled", True) or ml_pred is None:      # Atlas v3: sin capa ML
        out = core.add_prefix("core_")
        out["ml_weight"] = 0.0
        out["target"] = core["target_raw"]
        return out
    with_ml = target_exposure(d, T, ml_pred, pcfg)
    promo = ml_promotion(d, core["target_raw"], with_ml["target_raw"], pcfg, c["periods"]["dev_start"],
                         window=c["ml"]["promotion_window_days"], threshold=c["ml"]["promotion_threshold"],
                         ml_weight=c["ml"]["promotion_weight"])
    out = core.add_prefix("core_")
    out["ml_score"] = with_ml["ml_score"]
    out["target_with_ml"] = with_ml["target_raw"]
    out["ml_weight"] = promo["ml_weight"]
    out["target"] = promo["target"]
    return out
