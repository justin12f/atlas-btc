
from atlas.data.collectors import (INTERVAL_MS, candle_window, closed_only, collect_all, collect_candles,
                                   collect_funding, impact_bps)
from atlas.data.store import SQLiteStore
from atlas.hl.client import parse_book
from tests.conftest import load_fx

C5 = load_fx("candles_spot_5m_full")
NOW = C5[-1]["T"] + 1          # la última vela grabada ya cerró


class FakeInfo:
    def __init__(self):
        self.calls = []

    def candles(self, coin, interval, start, end):
        self.calls.append((coin, interval, start, end))
        src = {"5m": C5, "1m": load_fx("candles_spot_1m_full"), "1d": load_fx("candles_spot_1d_full")}[interval]
        return [r for r in src if start <= r["t"] <= end]

    def book(self, coin):
        return parse_book(load_fx("l2book_spot_ubtc"))

    def funding_history(self, coin, start, end=None):
        rows = [r for r in load_fx("funding_btc_first") if r["time"] >= start]
        return rows[:500]


def test_window():
    assert candle_window(None, "5m", 10**12) == (0, 10**12)
    assert candle_window(1000, "5m", 10**12)[0] == 1000 - 2 * INTERVAL_MS["5m"]


def test_closed_only_drops_open_candle():
    rows = [{"t": 0, "T": 299_999}, {"t": 300_000, "T": 599_999}]
    assert closed_only(rows, 400_000) == rows[:1]


def test_collect_candles_incremental(tmp_path):
    s, info = SQLiteStore(tmp_path / "a.db"), FakeInfo()
    n1 = collect_candles(info, s, "@142", "5m", NOW)
    assert n1 == len(C5)
    collect_candles(info, s, "@142", "5m", NOW)
    assert info.calls[-1][2] == C5[-1]["t"] - 2 * INTERVAL_MS["5m"]   # pide solo la cola
    assert len(s.candles("@142", "5m")) == len(C5)


def test_open_candle_never_stored(tmp_path):
    s = SQLiteStore(tmp_path / "a.db")
    collect_candles(FakeInfo(), s, "@142", "5m", C5[-1]["t"] + 10)   # la última aún abierta
    assert s.last_candle_t("@142", "5m") == C5[-2]["t"]


def test_impact_bps():
    b = parse_book(load_fx("l2book_spot_ubtc"))
    i10, i100 = impact_bps(b, 10_000), impact_bps(b, 100_000)
    assert 0 < i10 <= i100 < 5
    assert impact_bps(b, 10_000_000) is None
    assert impact_bps(b, 10_000, "sell") > 0


def test_funding_paging(tmp_path):
    s = SQLiteStore(tmp_path / "a.db")
    n = collect_funding(FakeInfo(), s, "BTC", now=2_000_000_000_000, start_ms=0)
    assert n == 500 and s.last_funding_time("BTC") == load_fx("funding_btc_first")[-1]["time"]


def test_collect_all_isolates_errors(tmp_path):
    class Broken(FakeInfo):
        def book(self, coin):
            raise TimeoutError("sin libro")
    s = SQLiteStore(tmp_path / "a.db")
    rep = collect_all(Broken(), s, "@142", "BTC", NOW)
    assert rep.candles["@142/5m"] > 0 and len(rep.errors) == 2
    assert s.events("collect")
