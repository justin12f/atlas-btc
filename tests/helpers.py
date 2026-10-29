"""Dobles de prueba compartidos: reloj falso, mercado falso a partir de fixtures."""
from datetime import datetime, timedelta, timezone

from atlas.hl.client import parse_book
from atlas.hl.markets import resolve_spot
from atlas.interfaces import Book, Level
from tests.conftest import load_fx

UBTC = resolve_spot(load_fx("spot_meta_mainnet"), "UBTC/USDC")


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


def flat_book(mid: float, coin: str = "@142", size: float = 5.0, ts=None) -> Book:
    ts = ts or datetime(2026, 10, 29, tzinfo=timezone.utc)
    return Book(coin, ts, tuple(Level(mid - 0.5 - i, size) for i in range(20)),
                tuple(Level(mid + 0.5 + i, size) for i in range(20)))


class FakeMarket:
    """MarketData con libro fijo y velas de 1m programables."""
    def __init__(self, book: Book | None = None, candles_1m: list | None = None):
        self._book = book or parse_book(load_fx("l2book_spot_ubtc"))
        self._c1 = candles_1m or []
        self.book_calls = 0

    def book(self, coin):
        self.book_calls += 1
        return self._book

    def candles(self, coin, interval, start, end):
        return [r for r in self._c1 if start <= r["t"] <= end]

    def spot_meta(self):
        return load_fx("spot_meta_mainnet")

    def all_mids(self):
        return load_fx("all_mids_mainnet")


def minute_candles(start: datetime, n: int, o: float, l_: float, h: float) -> list[dict]:
    t0 = int(start.timestamp() * 1000)
    return [{"t": t0 + i * 60_000, "T": t0 + (i + 1) * 60_000 - 1, "o": o, "h": h, "l": l_, "c": o, "v": 1, "n": 1}
            for i in range(n)]
