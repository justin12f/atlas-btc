"""
Ledger por modo: saldos UBTC/USDC, comisión según feeToken, equity, máximo, caída y pausa.

Spot de HL: la comisión se cobra en el activo RECIBIDO (compra -> UBTC, venta -> USDC).
Invariantes: sin saldos negativos; valor conservado salvo comisiones.
"""
from __future__ import annotations

from dataclasses import replace

from atlas.interfaces import Fill, LedgerState

EPS = 1e-9


class LedgerError(RuntimeError):
    pass


def initial_state(mode: str, usdc: float) -> LedgerState:
    return LedgerState(mode=mode, date=None, usdc=float(usdc), ubtc=0.0, peak=float(usdc), paused=False)


def apply_fill(s: LedgerState, f: Fill, base: str = "UBTC", quote: str = "USDC") -> LedgerState:
    notional = f.px * f.sz
    if f.side == "buy":
        usdc, ubtc = s.usdc - notional, s.ubtc + f.sz
    elif f.side == "sell":
        usdc, ubtc = s.usdc + notional, s.ubtc - f.sz
    else:
        raise LedgerError(f"lado inválido {f.side!r}")
    if f.fee_token == base:
        ubtc -= f.fee
    elif f.fee_token == quote:
        usdc -= f.fee
    else:
        raise LedgerError(f"feeToken desconocido {f.fee_token!r}")
    if usdc < -EPS or ubtc < -EPS:
        raise LedgerError(f"saldo negativo tras fill {f.fill_id}: usdc={usdc:.8f} ubtc={ubtc:.10f}")
    return replace(s, usdc=max(usdc, 0.0), ubtc=max(ubtc, 0.0))


def fee_value_usdc(f: Fill, base: str = "UBTC") -> float:
    return f.fee * f.px if f.fee_token == base else f.fee


def mark(s: LedgerState, px: float) -> tuple[float, float, float]:
    """(equity, peso en BTC, caída desde el máximo) al precio `px`."""
    eq = s.equity(px)
    peak = max(s.peak, eq)
    return eq, s.weight(px), eq / peak - 1 if peak > 0 else 0.0


def roll_peak(s: LedgerState, px: float) -> LedgerState:
    return replace(s, peak=max(s.peak, s.equity(px)))


def pause(s: LedgerState) -> LedgerState:
    return replace(s, paused=True)


def resume(s: LedgerState, px: float) -> LedgerState:
    """Reanudar tras el interruptor: el máximo se reinicia al equity actual (igual que engine/live.py::resume)."""
    return replace(s, paused=False, peak=s.equity(px))
