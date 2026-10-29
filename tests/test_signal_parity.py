"""Paridad de señal: atlas.signal (datos truncados día a día) == engine.strategy.build_targets (serie completa)."""
import numpy as np
import pandas as pd
import pytest

from atlas.data.stitch import stitch, to_frame
from atlas.signal import SignalError, compute_signal, compute_targets
from engine.strategy import build_targets, load_config, portfolio_config
from tests.conftest import ROOT, load_fx

SEED = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet")
C = load_config()


def test_compute_targets_equals_backtest():
    a = compute_targets(SEED, C)["target"]
    b = build_targets(SEED, None, C, portfolio_config(C))["target"]
    pd.testing.assert_series_equal(a, b)


@pytest.mark.parametrize("day", ["2025-03-01", "2025-11-15", "2026-06-30", "2026-10-22"])
def test_truncated_equals_full(day):
    full = compute_targets(SEED, C)
    s = compute_signal(SEED, day, "h", "v", C)
    assert abs(s.target - full.loc[day, "target"]) < 1e-9
    assert abs(s.vol_fc - full.loc[day, "core_vol_fc"]) < 1e-9


def test_last_backtest_target_is_75pct():
    assert compute_signal(SEED, "2026-10-22", "h", "v", C).target == pytest.approx(0.75)


def test_signal_on_stitched_hl_days():
    d = stitch(SEED, to_frame(load_fx("candles_spot_1d_full")), to_frame(load_fx("candles_spot_5m_full")),
               "2026-10-22", now=pd.Timestamp("2026-10-29 05:20"))
    s = compute_signal(d, "2026-10-28", "h", "v", C)
    assert 0 <= s.target <= 1 and s.close == d.loc["2026-10-28", "close"]


def test_missing_rv_raises():
    d = SEED.copy()
    d.loc["2026-10-22", "rv"] = np.nan
    with pytest.raises(SignalError, match="incompleta"):
        compute_signal(d, "2026-10-22", "h", "v", C)


def test_ml_enabled_refused():
    c2 = {**C, "ml": {**C["ml"], "enabled": True}}
    with pytest.raises(SignalError, match="ML"):
        compute_targets(SEED, c2)
