from datetime import datetime, timezone

import pytest

from atlas.execution.brokers import FakeBroker, LiveBroker, PaperBroker, TestnetBroker
from atlas.interfaces import OrderPlan
from tests.helpers import UBTC, FakeClock, FakeMarket, flat_book, minute_candles

T0 = datetime(2026, 10, 29, 0, 6, 0, tzinfo=timezone.utc)
BUY = OrderPlan("2026-10-28", "buy", 0.05, 80_000.0, 0.75, 0.25, 4_000.0, "x")


def test_fake_broker_backtest_cost():
    r = FakeBroker(0.0013).execute(BUY, UBTC, "0x1")
    f = r.fills[0]
    assert r.status == "filled" and f.px == 80_000 and f.fee == pytest.approx(0.05 * 0.0013) and f.fee_token == "UBTC"


def test_skipped_plan():
    p = OrderPlan("d", None, 0, 1, 0, 0, 0, "banda")
    assert FakeBroker(0.0013).execute(p, UBTC, "0x1").status == "skipped"


def test_paper_maker_fill_when_crossed():
    book = flat_book(80_000.0)                         # bid 79,999.5 -> límite redondeado 79,999
    c1 = minute_candles(T0, 10, 80_000, 80_000, 80_001) + minute_candles(
        datetime(2026, 10, 29, 0, 16, tzinfo=timezone.utc), 5, 80_000, 79_990, 80_001)
    m = FakeMarket(book, c1)
    r = PaperBroker(m, FakeClock(T0)).execute(BUY, UBTC, "0xabc", ref_close=80_000)
    assert r.status == "filled" and len(r.fills) == 1
    f = r.fills[0]
    assert f.liquidity == "maker" and f.px == 79_999 and f.fee == pytest.approx(0.05 * 0.0004)
    assert len(r.meta["attempts"]) == 3            # no cruzó en los primeros dos intervalos de 5 min
    assert r.meta["capacity_bps"]["10000"] is not None


def test_paper_falls_back_to_taker():
    book = flat_book(80_000.0)
    m = FakeMarket(book, minute_candles(T0, 60, 80_000, 80_000, 80_001))   # nunca cruza
    clock = FakeClock(T0)
    r = PaperBroker(m, clock).execute(BUY, UBTC, "0xabc", ref_close=80_000)
    assert r.status == "filled" and r.fills[0].liquidity == "taker" and r.fills[0].px == 80_000.5
    assert (clock.now() - T0).total_seconds() == 30 * 60
    assert r.meta["cost"] == pytest.approx(0.5 / 80_000 + 0.0007)


def test_paper_taker_policy_immediate():
    m = FakeMarket(flat_book(80_000.0))
    clock = FakeClock(T0)
    r = PaperBroker(m, clock, policy="taker").execute(BUY, UBTC, "0xabc")
    assert r.fills[0].liquidity == "taker" and clock.now() == T0


def test_live_broker_blocked():
    with pytest.raises(RuntimeError, match="bloqueado"):
        LiveBroker()


class FakeExchange:
    base_url = "https://api.hyperliquid-testnet.xyz"

    def __init__(self):
        self.orders, self.cancels, self.dead_man = [], [], []

    def schedule_cancel(self, t):
        self.dead_man.append(t)

    def order(self, name, is_buy, sz, px, order_type, cloid=None):
        self.orders.append((name, is_buy, sz, px, order_type, cloid.to_raw()))
        return {"status": "ok"}

    def cancel_by_cloid(self, name, cloid):
        self.cancels.append(cloid.to_raw())


class FakeTestnetInfo:
    """Llena la segunda orden Alo."""
    def __init__(self, ex):
        self.ex = ex

    def l2_snapshot(self, coin):
        return {"levels": [[{"px": "79999.0", "sz": "1", "n": 1}], [{"px": "80001.0", "sz": "1", "n": 1}]]}

    def query_order_by_cloid(self, user, cloid):
        raw = cloid.to_raw()
        placed = [o for o in self.ex.orders if o[5] == raw]
        if not placed:
            return {"status": "unknownOid"}
        filled = self.ex.orders.index(placed[0]) == 1
        return {"status": "order", "order": {"status": "filled" if filled else "open"}}

    def user_fills_by_time(self, user, start):
        if len(self.ex.orders) < 2:
            return []
        o = self.ex.orders[1]
        return [{"coin": "@142", "cloid": o[5], "side": "B", "px": str(o[3]), "sz": str(o[2]), "fee": "0.000002",
                 "feeToken": "UBTC", "tid": 7, "time": 1793232400000, "crossed": False}]


def test_testnet_broker_flow():
    ex = FakeExchange()
    b = TestnetBroker(ex, FakeTestnetInfo(ex), "0xmaster", FakeClock(T0), max_order_usd=200)
    r = b.execute(BUY, UBTC, "0xabc")
    assert r.status == "filled" and r.fills[0].fee_token == "UBTC"
    assert ex.orders[0][2] * 80_000 <= 200                       # tope de testnet
    assert ex.orders[0][4] == {"limit": {"tif": "Alo"}} and len(ex.cancels) == 1
    assert ex.dead_man[0] is not None and ex.dead_man[-1] is None


def test_testnet_broker_refuses_mainnet():
    ex = FakeExchange()
    ex.base_url = "https://api.hyperliquid.xyz"
    with pytest.raises(RuntimeError, match="testnet"):
        TestnetBroker(ex, None, "0xmaster")
