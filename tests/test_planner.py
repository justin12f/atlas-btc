import pytest

from atlas.execution.ledger import apply_fill, initial_state
from atlas.execution.planner import plan_order
from atlas.interfaces import Fill
from tests.helpers import UBTC


def holding(w, px=80_000.0, eq=10_000.0):
    s = initial_state("paper", eq)
    sz = eq * w / px
    return apply_fill(s, Fill("f", "c", "buy", px, sz, 0.0, "UBTC", "taker", "t")) if w > 0 else s


def test_inside_band_no_trade():
    p = plan_order("d", 0.55, holding(0.5), 80_000, UBTC, 0.10)
    assert p.side is None and "banda" in p.reason


def test_buy_sized_and_rounded():
    p = plan_order("d", 0.75, holding(0.0), 80_000, UBTC, 0.10)
    assert p.side == "buy" and p.sz == pytest.approx(0.09375) and round(p.sz, 5) == p.sz


def test_buy_reserves_fee():
    p = plan_order("d", 1.0, holding(0.0), 80_000, UBTC, 0.10, fee_reserve=0.001)
    assert p.sz * 80_000 <= 10_000 * 0.999


def test_full_exit_sells_everything():
    s = holding(0.30)
    p = plan_order("d", 0.01, s, 80_000, UBTC, 0.10)
    assert p.side == "sell" and p.sz == pytest.approx(round(s.ubtc - 5e-6, 5), abs=1e-5)
    assert p.sz <= s.ubtc


def test_full_entry_third_condition():
    p = plan_order("d", 0.99, holding(0.95), 80_000, UBTC, 0.10)
    assert p.side == "buy"


def test_min_order():
    p = plan_order("d", 0.012, holding(0.0, eq=500.0), 80_000, UBTC, 0.10)   # sin posición: no opera
    assert p.side is None
    p = plan_order("d", 0.5, holding(0.0, eq=15.0), 80_000, UBTC, 0.10)      # 7.5 USDC < 10
    assert p.side is None and "mínimo" in p.reason


def test_paused_targets_zero():
    s = holding(0.5)
    s.paused = True
    p = plan_order("d", 0.9, s, 80_000, UBTC, 0.10)
    assert p.side == "sell" and p.target == 0.0


def test_max_order_cap():
    p = plan_order("d", 1.0, holding(0.0), 80_000, UBTC, 0.10, max_order_usd=200)
    assert p.sz * 80_000 <= 200
