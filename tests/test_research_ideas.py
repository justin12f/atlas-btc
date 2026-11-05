"""La copia del simulador usada en scripts/research_ideas_2026_11.py reproduce engine.portfolio.simulate."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from engine.portfolio import simulate
from engine.strategy import load_config, portfolio_config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ri", ROOT / "scripts/research_ideas_2026_11.py")
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)


def _data(n=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.04, n))), idx)
    low = close * (1 - rng.uniform(0, 0.15, n))
    tgt = pd.Series(rng.uniform(0, 1, n), idx)
    return pd.DataFrame({"close": close, "low": low}), tgt


def test_sin_regla_igual_a_simulate():
    d, tgt = _data()
    pc = portfolio_config(load_config())
    a = simulate(d, tgt, pc, "2020-01-01", "2021-01-31")["ret"]
    b = ri.simulate_intraday_exit(d, tgt, pc, "2020-01-01", "2021-01-31", None)["ret"]
    assert np.allclose(a.to_numpy(), b.to_numpy(), atol=1e-15, rtol=0)


def test_salida_limita_perdida_del_dia():
    d, tgt = _data()
    pc = portfolio_config(load_config())
    s = ri.simulate_intraday_exit(d, tgt, pc, "2020-01-01", "2021-01-31", 0.10)
    assert s["stop"].any()
    assert (s.loc[s["stop"], "ret"] >= -0.10 - 0.02).all()


def test_mitad_fija_g2():
    spec2 = importlib.util.spec_from_file_location("rg", ROOT / "scripts/research_growth_2026_11.py")
    rg = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(rg)
    t = pd.Series([0.0, 0.4, 1.0])
    assert rg.half_fixed(t).tolist() == [0.5, 0.7, 1.0]
