import numpy as np
import pandas as pd

from atlas.data.store import SQLiteStore
from atlas.shadow_g1 import MODE, g1_config, record_g1, step
from engine.strategy import load_config


def test_g1_config_no_toca_original():
    C = load_config()
    g = g1_config(C)
    assert g["risk"]["target_vol"] == 99.0 and C["risk"]["target_vol"] == 0.40


def test_step_compra_y_banda():
    r = step(None, 0.6, 100.0, 0.10, 0.0013, 10_000)
    assert abs(r["ubtc"] * 100 / (r["usdc"] + r["ubtc"] * 100) - 0.6) < 0.002
    r2 = step(r, 0.65, 100.0, 0.10, 0.0013, 10_000)          # dentro de la banda: no opera
    assert r2["ubtc"] == r["ubtc"]


def test_step_interruptor():
    prev = {"usdc": 0.0, "ubtc": 100.0, "peak": 10_000, "paused": False}
    r = step(prev, 1.0, 39.0, 0.10, 0.0013, 10_000)            # equity 3,900: caída −61%
    assert r["paused"] and r["ubtc"] == 0.0 and r["target"] == 0.0


def _series(n=420):
    rng = np.random.default_rng(3)
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n)))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0,
                         "rv": 0.03 ** 2, "rv_down": 0.03 ** 2 / 2}, index=idx)


def test_record_idempotente(tmp_path):
    st = SQLiteStore(tmp_path / "a.db")
    d = _series()
    C = load_config()
    s, e = str(d.index[-10].date()), str(d.index[-1].date())
    assert record_g1(st, d, C, s, e, 10_000) == 10
    assert record_g1(st, d, C, s, e, 10_000) == 0
    assert len(st.ledger_df(MODE)) == 10
