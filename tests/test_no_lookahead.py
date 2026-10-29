"""
Prueba anti look-ahead: si recortamos los datos en la fecha D (o alteramos el futuro),
todo lo calculado hasta D debe ser IDENTICO. Si algo cambia, ese modulo esta viendo el futuro.

  python -m pytest tests/ -q      (o)      python tests/test_no_lookahead.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.features import build_feature_matrix  # noqa: E402
from engine.ml import MLConfig, walk_forward  # noqa: E402
from engine.portfolio import PortfolioConfig, simulate, target_exposure  # noqa: E402
from engine.regime import RegimeHMM, regime_obs  # noqa: E402
from engine.signals import trend_score  # noqa: E402

D = pd.Timestamp("2022-06-30")
d_full = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet")
mac = pd.read_parquet(ROOT / "data/processed/macro_etfs.parquet")
d_cut = d_full.loc[:D]


def _same(a: pd.DataFrame | pd.Series, b: pd.DataFrame | pd.Series, name: str):
    a, b = a.loc[:D], b.loc[:D]
    pd.testing.assert_index_equal(a.index, b.index)
    av, bv = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ok = np.isclose(av, bv, rtol=1e-9, atol=1e-12, equal_nan=True)
    assert ok.all(), f"{name}: {np.size(ok) - ok.sum()} valores cambian al ver el futuro"


def test_features():
    _same(build_feature_matrix(d_full, mac), build_feature_matrix(d_cut, mac.loc[:D]), "features")


def test_trend_and_exposure():
    cfg = PortfolioConfig(cycle_rule=True, target_vol=0.4)
    tf, tc = trend_score(d_full), trend_score(d_cut)
    _same(tf, tc, "trend_score")
    _same(target_exposure(d_full, tf, None, cfg)["target_raw"], target_exposure(d_cut, tc, None, cfg)["target_raw"],
          "target_exposure")


def test_hmm_filter():
    obs_f, obs_c = regime_obs(d_full), regime_obs(d_cut)
    h = RegimeHMM().fit(obs_f.loc["2014-06-01":"2019-12-31"])
    _same(h.filter_proba(obs_f.loc["2014-06-01":]), h.filter_proba(obs_c.loc["2014-06-01":]), "hmm_filter")


def test_simulator_ignores_future_targets():
    cfg = PortfolioConfig()
    t = trend_score(d_full).fillna(0)
    t2 = t.copy()
    t2.loc[D + pd.Timedelta(days=1):] = 1 - t2.loc[D + pd.Timedelta(days=1):]   # futuro alterado
    a = simulate(d_full, t, cfg, "2018-01-01")["ret"]
    b = simulate(d_full, t2, cfg, "2018-01-01")["ret"]
    _same(a, b, "simulate")
    assert not np.allclose(a.loc[D + pd.Timedelta(days=2):], b.loc[D + pd.Timedelta(days=2):])


def test_ml_walk_forward():
    cfg = MLConfig(horizon=28, automl=False, oos_start="2021-01-01", retrain_days=182)
    Xf, Xc = build_feature_matrix(d_full, mac), build_feature_matrix(d_cut, mac.loc[:D])
    pf, _, _ = walk_forward(d_full, Xf, cfg, end="2022-12-31", verbose=False)
    pc, _, _ = walk_forward(d_cut, Xc, cfg, end=str(D.date()), verbose=False)
    _same(pf["p_raw"], pc["p_raw"], "ml_walk_forward")


if __name__ == "__main__":
    for fn in [test_features, test_trend_and_exposure, test_hmm_filter, test_simulator_ignores_future_targets,
               test_ml_walk_forward]:
        fn()
        print("OK", fn.__name__)
