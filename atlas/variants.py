"""
Variantes en sombra calculadas a partir del ledger real del papel (no operan, no escriben nada).

Atlas + USDC 4%: mismas operaciones que el papel; el USDC que queda sin invertir al cierre de cada día gana
(1 + apy)^(1/365) − 1 al día siguiente, y ese interés se acumula aparte en USDC (no se invierte en BTC).
Es la misma tasa diaria que usa engine.portfolio.simulate con `usdc_apy`. Diferencia con el backtest: allí el
interés sí cambia el tamaño de las operaciones siguientes; aquí no, lo que lo hace un poco más conservador.
"""
from __future__ import annotations

import pandas as pd


def usdc_yield_equity(ledger: pd.DataFrame, apy: float = 0.04) -> pd.Series:
    """ledger: columnas date, usdc, equity (una fila por día, ordenadas). Devuelve la equity con interés."""
    daily = (1 + apy) ** (1 / 365) - 1
    pot, prev_cash, out = 0.0, None, []
    for row in ledger.itertuples(index=False):
        if prev_cash is not None:
            pot += prev_cash * daily
        out.append(row.equity + pot)
        prev_cash = row.usdc + pot
    return pd.Series(out, index=ledger["date"].to_numpy(), name=f"equity_usdc{apy:.0%}")
