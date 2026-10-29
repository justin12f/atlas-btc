import pytest

from atlas.execution.ledger import (LedgerError, apply_fill, fee_value_usdc, initial_state, mark, pause, resume,
                                    roll_peak)
from atlas.interfaces import Fill

T = "2026-10-29T00:10:00+00:00"


def fill(side, px, sz, fee, tok, fid="f"):
    return Fill(fid, "0x1", side, px, sz, fee, tok, "maker", T)


def test_buy_fee_in_base_and_conservation():
    s = initial_state("paper", 10_000)
    f = fill("buy", 80_000, 0.05, 0.05 * 0.0004, "UBTC")
    s2 = apply_fill(s, f)
    assert s2.usdc == pytest.approx(6_000) and s2.ubtc == pytest.approx(0.05 * (1 - 0.0004))
    assert s.equity(80_000) - s2.equity(80_000) == pytest.approx(fee_value_usdc(f))


def test_sell_fee_in_quote():
    s = apply_fill(initial_state("paper", 10_000), fill("buy", 80_000, 0.1, 0.0, "UBTC"))
    s2 = apply_fill(s, fill("sell", 81_000, 0.1, 81_000 * 0.1 * 0.0007, "USDC"))
    assert s2.ubtc == pytest.approx(0) and s2.usdc == pytest.approx(2_000 + 8_100 * (1 - 0.0007))


def test_no_negative_balances():
    with pytest.raises(LedgerError):
        apply_fill(initial_state("paper", 100), fill("buy", 80_000, 0.01, 0, "UBTC"))
    with pytest.raises(LedgerError):
        apply_fill(initial_state("paper", 100), fill("sell", 80_000, 0.01, 0, "USDC"))
    with pytest.raises(LedgerError):
        apply_fill(initial_state("paper", 1e6), fill("buy", 80_000, 0.01, 1, "HYPE"))


def test_drawdown_pause_resume():
    s = apply_fill(initial_state("paper", 10_000), fill("buy", 80_000, 0.125, 0, "UBTC"))
    s = roll_peak(s, 80_000)
    eq, w, dd = mark(s, 40_000)
    assert w == pytest.approx(1) and dd == pytest.approx(-0.5)
    s = pause(s)
    assert s.paused
    s = resume(s, 40_000)
    assert not s.paused and s.peak == pytest.approx(5_000)
