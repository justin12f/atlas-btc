import numpy as np
import pandas as pd

from atlas.parity import daily_close, parity_summary, parity_table


def frame(days: int, scale: float = 1.0, shift: float = 0.0, seed: int = 0) -> pd.DataFrame:
    idx = pd.date_range("2026-10-01", periods=days * 288 + 1, freq="5min")[1:] - pd.Timedelta("5min")
    r = np.random.default_rng(seed).normal(0, 0.001, len(idx)) * scale
    c = 80_000 * np.exp(np.cumsum(r)) * (1 + shift)
    return pd.DataFrame({"o": c, "h": c, "l": c, "c": c, "v": 1.0}, index=idx)


def test_daily_close_is_2355_bar():
    f = frame(2)
    dc = daily_close(f)
    assert len(dc) == 2 and dc.iloc[0] == f.loc["2026-10-01 23:55", "c"]


def test_identical_series_pass():
    f = frame(5)
    s = parity_summary(parity_table(f, {"binance": f.copy()}))
    r = s["refs"]["binance"]
    assert r["close_absdiff_median"] == 0 and abs(r["rv_ratio_median"] - 1) < 1e-12 and r["pass_close"] and r["pass_rv"]


def test_detects_offset_and_noisy_rv():
    hl = frame(5, scale=1.5)
    ref = frame(5, scale=1.0, shift=0.01)          # otra trayectoria: cierres muy distintos
    r = parity_summary(parity_table(hl, {"binance": ref}))["refs"]["binance"]
    assert not r["pass_close"] and not r["pass_rv"]


def test_seed_reference():
    f = frame(4)
    seed = pd.DataFrame({"close": daily_close(f), "rv": parity_table(f, {})["rv_hl"]})
    r = parity_summary(parity_table(f, {}, seed=seed))["refs"]["bitstamp"]
    assert r["pass_close"] and r["pass_rv"] and r["days_close"] == 4


def test_markdown_renders():
    from atlas.parity import parity_markdown
    f = frame(3)
    t = parity_table(f, {"binance": f.copy()})
    md = parity_markdown(t, parity_summary(t))
    assert "| binance | 3 |" in md and "2026-10-02" in md
