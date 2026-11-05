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
