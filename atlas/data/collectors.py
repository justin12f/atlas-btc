"""
Recolectores de mainnet (solo lectura, sin llaves). Sección 7.2 del plan.

Cada hora (:03): velas 5m/1m/1d de spot y perp, libro spot y perp, funding BTC. Peso total < 200.
Solo se guardan velas CERRADAS (T < ahora): una vela abierta nunca entra a la base.
Se pide desde la última vela guardada − 2 velas para cubrir correcciones tardías.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from atlas.data.store import SQLiteStore
from atlas.interfaces import Book

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000, "1d": 86_400_000}
FUNDING_PAGE = 500


@dataclass
class CollectReport:
    candles: dict = field(default_factory=dict)     # "coin/interval" -> filas guardadas
    books: dict = field(default_factory=dict)       # coin -> spread_bps
    funding: int = 0
    errors: list = field(default_factory=list)


def now_ms() -> int:
    return int(time.time() * 1000)


def candle_window(last_t: int | None, interval: str, now: int, overlap: int = 2) -> tuple[int, int]:
    """Ventana a pedir: desde la última vela guardada − `overlap` velas, o desde 0 la primera vez
    (HL devuelve todo lo que guarda: ~5,000 velas, verificado 5,016–5,217)."""
    if last_t is None:
        return 0, now
    return last_t - overlap * INTERVAL_MS[interval], now


def closed_only(rows: list[dict], now: int) -> list[dict]:
    return [r for r in rows if int(r["T"]) < now]


def collect_candles(info, store: SQLiteStore, coin: str, interval: str, now: int | None = None) -> int:
    now = now or now_ms()
    start, end = candle_window(store.last_candle_t(coin, interval), interval, now)
    rows = closed_only(info.candles(coin, interval, start, end), now)
    return store.upsert_candles(coin, interval, rows, "hl") if rows else 0


def impact_bps(book: Book, usd: float, side: str = "buy") -> float | None:
    """Costo de impacto (pb vs mid) de una orden a mercado de `usd` USDC recorriendo el libro. None si no alcanza."""
    levels = book.asks if side == "buy" else book.bids
    rem, cost, qty = usd, 0.0, 0.0
    for lv in levels:
        take = min(lv.sz, rem / lv.px)
        cost += take * lv.px
        qty += take
        rem -= take * lv.px
        if rem <= 1e-9:
            vwap = cost / qty
            return (vwap / book.mid - 1) * 1e4 if side == "buy" else (1 - vwap / book.mid) * 1e4
    return None


def collect_book(info, store: SQLiteStore, coin: str) -> float:
    b = info.book(coin)
    spread = (b.best_ask - b.best_bid) / b.mid * 1e4
    store.insert_book(coin, int(b.ts.timestamp() * 1000), [[lv.px, lv.sz] for lv in b.bids],
                      [[lv.px, lv.sz] for lv in b.asks], b.mid, spread, impact_bps(b, 10_000), impact_bps(b, 100_000))
    return spread


def collect_funding(info, store: SQLiteStore, coin: str, now: int | None = None, start_ms: int | None = None,
                    max_pages: int = 1000) -> int:
    """Pagina fundingHistory (≤ 500 por respuesta) desde lo último guardado hasta ahora."""
    now = now or now_ms()
    last = store.last_funding_time(coin)
    start = start_ms if start_ms is not None else (last + 1 if last else now - 7 * 86_400_000)
    total = 0
    for _ in range(max_pages):
        rows = info.funding_history(coin, start, now)
        if not rows:
            break
        total += store.upsert_funding(rows)
        nxt = max(int(r["time"]) for r in rows) + 1
        if len(rows) < FUNDING_PAGE or nxt <= start:
            break
        start = nxt
    return total


def collect_all(info, store: SQLiteStore, spot_coin: str, perp_coin: str, now: int | None = None,
                intervals: tuple[str, ...] = ("5m", "1m", "1d")) -> CollectReport:
    """Corrida horaria. Un fallo en un recolector no detiene a los demás; se registra y se reporta."""
    now = now or now_ms()
    rep = CollectReport()
    for coin in (spot_coin, perp_coin):
        for iv in intervals:
            try:
                rep.candles[f"{coin}/{iv}"] = collect_candles(info, store, coin, iv, now)
            except Exception as e:  # noqa: BLE001 — se registra y sigue
                rep.errors.append(f"candles {coin} {iv}: {e}")
        try:
            rep.books[coin] = collect_book(info, store, coin)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"book {coin}: {e}")
    try:
        rep.funding = collect_funding(info, store, perp_coin, now)
    except Exception as e:  # noqa: BLE001
        rep.errors.append(f"funding {perp_coin}: {e}")
    for err in rep.errors:
        store.log_event("error", "collect", err)
    return rep


def load_fixture_candles(store: SQLiteStore, coin: str, interval: str, rows: list[dict], now: int | None = None) -> int:
    """Siembra la base con velas grabadas (p. ej. las de la Fase 0) para no perder la ventana de HL."""
    rows = closed_only(rows, now or now_ms())
    return store.upsert_candles(coin, interval, rows, "hl")
