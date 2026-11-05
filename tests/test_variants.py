import pandas as pd
import pytest

from atlas.variants import usdc_yield_equity


def test_all_cash_compounds_at_apy():
    led = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=366).strftime("%Y-%m-%d"),
                        "usdc": 10_000.0, "equity": 10_000.0})
    eq = usdc_yield_equity(led, 0.04)
    assert eq.iloc[0] == 10_000 and eq.iloc[-1] == pytest.approx(10_400, rel=1e-9)


def test_fully_invested_earns_nothing():
    led = pd.DataFrame({"date": ["a", "b", "c"], "usdc": 0.0, "equity": [10_000.0, 10_500.0, 9_800.0]})
    assert list(usdc_yield_equity(led)) == [10_000.0, 10_500.0, 9_800.0]


def test_half_cash_half_rate():
    led = pd.DataFrame({"date": ["a", "b"], "usdc": [5_000.0, 5_000.0], "equity": [10_000.0, 10_000.0]})
    assert usdc_yield_equity(led).iloc[1] - 10_000 == pytest.approx(5_000 * (1.04 ** (1 / 365) - 1))


def test_dashboard_paper_con_g1():
    from atlas.variants import paper_daily
    led = pd.DataFrame({"date": ["2026-10-29", "2026-10-30"], "usdc": [5000.0, 5000.0], "ubtc": [0.05, 0.05],
                        "mark_px": [100_000.0, 102_000.0], "equity": [10_000.0, 10_100.0], "target": [0.5, 0.5],
                        "close": [100_000.0, 102_000.0]})
    g1 = led.drop(columns=["close", "target"]).assign(equity=[10_000.0, 10_200.0])
    out = paper_daily(led, g1)
    assert list(out["g1"]) == [10_000.0, 10_200.0]
    assert out["g1_usdc4"].iloc[1] > out["g1"].iloc[1]
    assert out["g1_peso_btc"].iloc[0] == pytest.approx(0.5)
