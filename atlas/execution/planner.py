"""
Planificador (sección 9.1): de objetivo + saldos a una orden redondeada (o nada).

La regla de banda es engine.portfolio.should_trade, la misma que usa simulate (no la del legado live.py).
"""
from __future__ import annotations

from atlas.hl.markets import round_sz
from atlas.interfaces import LedgerState, OrderPlan, SpotMarket
from engine.portfolio import should_trade

FULL_EXIT = 0.02


def plan_order(date: str, target: float, s: LedgerState, mark_px: float, market: SpotMarket, band: float,
               min_order_usd: float = 10.0, fee_reserve: float = 0.001, max_order_usd: float | None = None) -> OrderPlan:
    """`fee_reserve`: fracción del USDC que no se usa en compras (colchón para comisión y redondeo)."""
    e = 0.0 if s.paused else float(target)
    eq = s.equity(mark_px)
    w = s.weight(mark_px)
    none = lambda why: OrderPlan(date, None, 0.0, mark_px, e, w, 0.0, why)  # noqa: E731
    if eq <= 0:
        return none("equity<=0")
    if not should_trade(e, w, band):
        return none(f"dentro de banda |{e:.4f}-{w:.4f}|<{band}")
    notional = (e - w) * eq
    if max_order_usd is not None:
        notional = max(min(notional, max_order_usd), -max_order_usd)
    if abs(notional) < min_order_usd:
        return none(f"nocional {notional:.2f} < mínimo {min_order_usd}")
    if notional > 0:
        notional = min(notional, s.usdc * (1 - fee_reserve))
        sz = round_sz(notional / mark_px, market.sz_decimals)
        side = "buy"
    else:
        sz = round_sz(min(-notional / mark_px, s.ubtc), market.sz_decimals)
        if e < FULL_EXIT:                     # salida total: todo el saldo vendible
            sz = round_sz(s.ubtc, market.sz_decimals)
        side = "sell"
    if sz * mark_px < min_order_usd:
        return none(f"tamaño redondeado {sz} < mínimo")
    return OrderPlan(date, side, sz, mark_px, e, w, sz * mark_px * (1 if side == "buy" else -1),
                     f"objetivo {e:.4f} vs peso {w:.4f}")
