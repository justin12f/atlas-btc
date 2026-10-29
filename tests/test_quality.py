import numpy as np
import pandas as pd

from atlas.data.quality import check_day, data_age_min, ref_divergence
from atlas.data.stitch import to_frame
from tests.conftest import load_fx

S5 = to_frame(load_fx("candles_spot_5m_full"))
P5 = to_frame(load_fx("candles_perp_5m_full"))
DAY = pd.Timestamp("2026-10-27")


def test_good_day_has_no_flags():
    assert check_day(S5, DAY, P5) == []


def test_missing_bars_and_gap():
    d = S5.drop(S5.loc["2026-10-27 10:00":"2026-10-27 10:30"].index)
    f = check_day(d, DAY, P5)
    assert any(x.startswith("n_5m=") for x in f) and any(x.startswith("gap=") for x in f)


def test_unconfirmed_jump():
    d = S5.copy()
    ix = d.loc["2026-10-27 12:00"].name
    d.loc[ix, "c"] = d.loc[ix, "c"] * 1.2
    assert any(x.startswith("jump_unconfirmed") for x in check_day(d, DAY, P5))


def test_confirmed_jump_passes():
    d, p = S5.copy(), P5.copy()
    later = d.index >= pd.Timestamp("2026-10-27 12:00")
    d.loc[later, "c"] *= 1.2
    p.loc[p.index >= pd.Timestamp("2026-10-27 12:00"), "c"] *= 1.2
    assert check_day(d, DAY, p) == []


def test_helpers():
    assert np.isclose(ref_divergence(101, 100), 0.01)
    assert data_age_min(0, 600_000) == 10
