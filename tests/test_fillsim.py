from datetime import datetime, timezone

import pandas as pd
import pytest

from atlas.data.stitch import to_frame
from atlas.execution.fillsim import candles_after, crossed, shortfall, vwap_for_notional, walk_book
from atlas.hl.client import parse_book
from tests.conftest import load_fx
from tests.helpers import flat_book, minute_candles

BOOK = parse_book(load_fx("l2book_spot_ubtc"))


def test_walk_book_small_fills_at_top():
    r = walk_book(BOOK, "buy", 0.001)
    assert r.vwap == BOOK.best_ask and not r.short


def test_walk_book_short_when_book_ends():
    total = sum(lv.sz for lv in BOOK.asks)
    r = walk_book(BOOK, "buy", total * 2)
    assert r.short and r.filled_sz == pytest.approx(total)


def test_walk_book_slippage_cap():
    b = flat_book(80_000.0, size=0.01)
    r = walk_book(b, "buy", 1.0, max_slippage=0.0001)        # tope 8 USDC por encima del mejor ask
    assert r.short and r.levels_used <= 9


def test_vwap_notional_and_capacity():
    assert vwap_for_notional(BOOK, "buy", 10_000) >= BOOK.best_ask
    assert vwap_for_notional(BOOK, "buy", 10_000_000) is None


def test_cross_requires_one_tick():
    t0 = datetime(2026, 10, 29, 0, 6, tzinfo=timezone.utc)
    touch = to_frame(minute_candles(t0, 5, 80_000, 80_000, 80_010))      # low == bid: tocar no basta
    assert crossed("buy", 80_000, touch, 1.0) is None
    through = to_frame(minute_candles(t0, 5, 80_000, 79_999, 80_010))
    assert crossed("buy", 80_000, through, 1.0) == through.index[0]
    assert crossed("sell", 80_010, through, 1.0) is None


def test_candles_after_excludes_current_minute():
    t0 = datetime(2026, 10, 29, 0, 6, tzinfo=timezone.utc)
    c = to_frame(minute_candles(t0, 10, 1, 1, 1))
    got = candles_after(c, pd.Timestamp("2026-10-29 00:06:30"), pd.Timestamp("2026-10-29 00:11:30"))
    assert got.index[0] == pd.Timestamp("2026-10-29 00:07") and got.index[-1] == pd.Timestamp("2026-10-29 00:11")


def test_shortfall_sign():
    assert shortfall("buy", 101, 100, 0.0004) == pytest.approx(0.0104)
    assert shortfall("sell", 101, 100, 0.0004) == pytest.approx(-0.0096)
