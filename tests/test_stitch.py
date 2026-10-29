import numpy as np
import pandas as pd
import pytest

from atlas.data.stitch import bad_days, rv_by_day, stitch, to_frame
from tests.conftest import ROOT, load_fx

SEED = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet")
S1D = to_frame(load_fx("candles_spot_1d_full"))
S5 = to_frame(load_fx("candles_spot_5m_full"))
P5 = to_frame(load_fx("candles_perp_5m_full"))
NOW = pd.Timestamp("2026-10-29 05:20")


@pytest.fixture(scope="module")
def d():
    return stitch(SEED, S1D, S5, "2026-10-22", perp5=P5, now=NOW)


def test_seed_part_identical_to_backtest(d):
    cols = ["open", "high", "low", "close", "volume", "rv", "rv_down"]
    pd.testing.assert_frame_equal(d.loc[:"2026-10-22", cols], SEED.loc[:"2026-10-22", cols], check_freq=False)
    assert (d.loc[:"2026-10-22", "source"] == "bitstamp").all()


def test_hl_part(d):
    hl = d.loc["2026-10-23":]
    assert list(hl.index.strftime("%Y-%m-%d")) == [f"2026-10-{x}" for x in range(23, 29)]   # sin el día en curso
    assert (hl["source"] == "hl").all() and hl["rv"].notna().all() and (hl["n_5m"] == 288).all()
    assert (hl["quality_flags"] == "").all()
    c1d = S1D.loc["2026-10-28", "c"]
    assert hl.loc["2026-10-28", "close"] == c1d


def test_rv_hl_close_to_bitstamp_overlap():
    rv = rv_by_day(S5)["rv"].dropna()
    ov = rv.index.intersection(SEED.index)
    ratio = (rv.loc[ov] / SEED.loc[ov, "rv"]).median()
    assert 0.85 <= ratio <= 1.15


def test_missing_5m_flags_rv_missing():
    s5 = S5.drop(S5.loc["2026-10-25 03:00":"2026-10-25 03:20"].index)
    d = stitch(SEED, S1D, s5, "2026-10-22", now=NOW)
    assert "rv_missing" in d.loc["2026-10-25", "quality_flags"]
    assert "2026-10-25" in bad_days(d, since="2026-10-23").index.strftime("%Y-%m-%d")


def test_binance_fallback_for_rv():
    s5 = S5.drop(S5.loc["2026-10-25 03:00":"2026-10-25 03:20"].index)
    bn = pd.DataFrame(load_fx("binance_btcusdc_5m_window")).iloc[:, :6]
    bn.columns = ["t", "o", "h", "l", "c", "v"]
    d = stitch(SEED, S1D, s5, "2026-10-22", binance5=to_frame(bn.to_dict("records")), now=NOW)
    row = d.loc["2026-10-25"]
    assert row["source"] == "hl+binance_rv" and np.isfinite(row["rv"])
    assert "2026-10-25" not in bad_days(d, since="2026-10-23").index.strftime("%Y-%m-%d")
