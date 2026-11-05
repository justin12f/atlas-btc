"""Corrida diaria de punta a punta con datos grabados (sin red)."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from atlas.config import load_live_config
from atlas.daily import Context, last_complete_day, run_daily
from atlas.data.collectors import load_fixture_candles
from atlas.data.store import SQLiteStore
from atlas.execution.brokers import PaperBroker
from atlas.interfaces import Fill
from engine.strategy import load_config
from tests.conftest import ROOT, load_fx
from tests.helpers import UBTC, FakeClock, FakeMarket

NOW = datetime(2026, 10, 29, 5, 20, tzinfo=timezone.utc)
SEED = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet")
NOW_MS = int(NOW.timestamp() * 1000)


class Recorder:
    def __init__(self):
        self.sent, self.pings = [], []

    def send(self, text, level="info"):
        self.sent.append((level, text))

    def ping(self, ok=True, body=""):
        self.pings.append(ok)


@pytest.fixture
def ctx(tmp_path):
    st = SQLiteStore(tmp_path / "atlas.db")
    for coin, iv, f in [(UBTC.coin, "5m", "candles_spot_5m_full"), (UBTC.coin, "1d", "candles_spot_1d_full"),
                        ("BTC", "5m", "candles_perp_5m_full")]:
        load_fixture_candles(st, coin, iv, load_fx(f), now=NOW_MS)
    clock = FakeClock(NOW)
    return Context(cfg=load_live_config(), C=load_config(), store=st, info=FakeMarket(), seed=SEED,
                   alerter=Recorder(), clock=clock)


def broker(ctx):
    return PaperBroker(ctx.info, ctx.clock, policy="taker")


def test_last_complete_day():
    assert last_complete_day(NOW) == "2026-10-28"


def test_first_day_buys_target(ctx):
    s = run_daily(ctx, broker(ctx))
    assert s.status == "done" and s.date == "2026-10-28"
    assert s.target == pytest.approx(0.75) and s.trade["side"] == "buy"
    assert s.weight_after == pytest.approx(0.75, abs=0.01)
    led = ctx.store.ledger_row("paper", "2026-10-28")
    assert led and led["target"] == pytest.approx(0.75)
    assert len(ctx.store.fills("paper")) == 1 and ctx.alerter.pings == [True]


def test_idempotent_second_run(ctx):
    run_daily(ctx, broker(ctx))
    s2 = run_daily(ctx, broker(ctx))
    assert s2.status == "already_done" and len(ctx.store.fills("paper")) == 1


def test_recovers_filled_order_after_crash(ctx):
    from atlas.hl.markets import cloid_for
    c = cloid_for("2026-10-28", "paper", 0)
    ctx.store.upsert_order(c, "paper", "2026-10-28", "buy", 70_000.0, 0.1, "maker_first", "filled")
    ctx.store.insert_fill("paper", Fill(f"{c}-0", c, "buy", 70_000.0, 0.1, 0.0, "UBTC", "maker", NOW))
    s = run_daily(ctx, broker(ctx))
    assert s.trade.get("filled") == pytest.approx(0.1)
    assert len(ctx.store.fills("paper")) == 1                  # no se volvió a ejecutar
    assert ctx.store.ledger_row("paper", "2026-10-28")["ubtc"] == pytest.approx(0.1)


def test_stale_data_safe_mode_keeps_position(ctx):
    ctx.clock.t = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)   # última vela 05:15 -> 3.5 h
    s = run_daily(ctx, broker(ctx))
    assert s.status == "safe_mode" and s.trade is None and any("viejos" in b for b in s.blocks)
    assert ctx.store.ledger_row("paper", "2026-10-28") is None          # el día no se cierra
    assert ctx.store.fills("paper") == []


def test_retry_after_safe_mode_trades(ctx):
    """Simulacro F5: datos viejos -> modo seguro; con datos al día, el reintento sí opera (una sola vez)."""
    ctx.clock.t = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
    assert run_daily(ctx, broker(ctx)).status == "safe_mode"
    ctx.clock.t = NOW                                                   # equivale a recolectar de nuevo
    s = run_daily(ctx, broker(ctx))
    assert s.status == "done" and s.trade["side"] == "buy"
    assert run_daily(ctx, broker(ctx)).status == "already_done" and len(ctx.store.fills("paper")) == 1


def test_crash_mid_execution_no_duplicate(ctx):
    """Simulacro F5: el proceso muere después de guardar el fill y antes del ledger; el reintento no duplica."""
    class Boom(Exception):
        pass

    orig = ctx.store.write_ledger

    def die(*a, **k):
        raise Boom()
    ctx.store.write_ledger = die
    with pytest.raises(Boom):
        run_daily(ctx, broker(ctx))
    ctx.store.write_ledger = orig
    assert len(ctx.store.fills("paper")) == 1 and ctx.store.ledger_row("paper", "2026-10-28") is None
    s = run_daily(ctx, broker(ctx))
    assert s.status == "done" and s.trade.get("filled") and len(ctx.store.fills("paper")) == 1
    assert s.weight_after == pytest.approx(0.75, abs=0.01)


def test_kill_switch_sells_even_in_safe_mode(ctx):
    mid = ctx.info.book(UBTC.coin).mid
    ctx.store.write_ledger("paper", "2026-10-27", 0.0, 0.1, mid, 0.1 * mid / 0.5, False, 1.0)   # caída 50%
    ctx.clock.t = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
    s = run_daily(ctx, broker(ctx))
    assert s.status == "killed" and s.trade["side"] == "sell" and s.target == 0.0
    led = ctx.store.ledger_row("paper", "2026-10-28")
    assert led["paused"] and led["ubtc"] == pytest.approx(0)
    assert any(lv == "critical" for lv, _ in ctx.alerter.sent)


def test_market_change_blocks(ctx):
    ctx.store.kv_set(f"market_fp:{UBTC.pair}", "otra-huella")
    s = run_daily(ctx, broker(ctx))
    assert s.status == "safe_mode" and any("spotMeta" in b for b in s.blocks)
