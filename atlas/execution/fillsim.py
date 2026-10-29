"""
Modelos de llenado del PaperBroker (sección 9.2). Funciones puras: libro y velas entran, llenados salen.

- Taker: recorre el l2Book (≤ 20 niveles) y calcula VWAP; si el libro no alcanza, llena lo posible.
- Maker-first: el límite (mejor bid en compra / mejor ask en venta) se llena solo si en las velas de 1m
  POSTERIORES a colocarla el precio CRUZA el límite al menos un tick (low ≤ bid − tick / high ≥ ask + tick).
  Tocarlo no basta (cola). Se reprecia cada `step` minutos; al vencer, el resto va como taker.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from atlas.interfaces import Book


@dataclass(frozen=True)
class TakerResult:
    filled_sz: float
    vwap: float | None
    short: bool              # el libro (o el tope de deslizamiento) no alcanzó
    levels_used: int


def walk_book(book: Book, side: str, sz: float, max_slippage: float | None = None) -> TakerResult:
    levels = book.asks if side == "buy" else book.bids
    if not levels:
        return TakerResult(0.0, None, True, 0)
    best = levels[0].px
    cap = None if max_slippage is None else best * (1 + max_slippage if side == "buy" else 1 - max_slippage)
    rem, cost, used = sz, 0.0, 0
    for lv in levels:
        if cap is not None and ((side == "buy" and lv.px > cap) or (side == "sell" and lv.px < cap)):
            break
        take = min(lv.sz, rem)
        cost += take * lv.px
        rem -= take
        used += 1
        if rem <= 1e-12:
            break
    filled = sz - max(rem, 0.0)
    return TakerResult(filled, cost / filled if filled > 0 else None, rem > 1e-12, used)


def vwap_for_notional(book: Book, side: str, usd: float) -> float | None:
    """VWAP para gastar `usd` (prueba de capacidad; no entra al ledger). None si no alcanza."""
    levels = book.asks if side == "buy" else book.bids
    rem, cost, qty = usd, 0.0, 0.0
    for lv in levels:
        take = min(lv.sz, rem / lv.px)
        cost += take * lv.px
        qty += take
        rem -= take * lv.px
        if rem <= 1e-9:
            return cost / qty
    return None


def crossed(side: str, limit: float, candles_1m: pd.DataFrame, tick: float) -> pd.Timestamp | None:
    """Primer minuto en que el mercado cruzó el límite por al menos un tick (None si no)."""
    if candles_1m.empty:
        return None
    if side == "buy":
        hit = candles_1m[candles_1m["l"].astype(float) <= limit - tick]
    else:
        hit = candles_1m[candles_1m["h"].astype(float) >= limit + tick]
    return None if hit.empty else hit.index[0]


def maker_limit(book: Book, side: str) -> float:
    return book.best_bid if side == "buy" else book.best_ask


def candles_after(candles_1m: pd.DataFrame, placed_at: pd.Timestamp, until: pd.Timestamp) -> pd.DataFrame:
    """Velas de 1m que empiezan en o después de colocar la orden y antes de `until`.
    Si se colocó a mitad de minuto, ese minuto no cuenta (pudo cruzar antes de que existiera la orden)."""
    start = placed_at.ceil("min")
    return candles_1m.loc[(candles_1m.index >= start) & (candles_1m.index < until)]


def shortfall(side: str, vwap: float, ref_close: float, fee_rate: float) -> float:
    """Implementation shortfall (sección 9.3): signo × (VWAP / cierre de 00:00 − 1) + comisión."""
    sign = 1 if side == "buy" else -1
    return sign * (vwap / ref_close - 1) + fee_rate
