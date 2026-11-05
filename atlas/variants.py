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


PAPER_START = 10_000.0


def paper_daily(ledger: pd.DataFrame, g1: pd.DataFrame | None = None) -> pd.DataFrame:
    """ledger: papel de Atlas v3. g1: ledger de la sombra G1 (mismas columnas), opcional."""
    led = ledger.sort_values("date").reset_index(drop=True)
    out = pd.DataFrame({"date": led["date"], "day": range(len(led))})
    out["atlas"] = led["equity"]
    out["atlas_usdc4"] = usdc_yield_equity(led[["date", "usdc", "equity"]], 0.04).to_numpy()
    out["btc"] = PAPER_START * led["close"] / led["close"].iloc[0]
    out["peso_btc"] = led["ubtc"] * led["mark_px"] / led["equity"]
    out["objetivo"] = led["target"]
    if g1 is not None:
        g = g1.sort_values("date").set_index("date")
        g4 = usdc_yield_equity(g.reset_index()[["date", "usdc", "equity"]], 0.04)
        out["g1"] = out["date"].map(g["equity"])
        out["g1_usdc4"] = out["date"].map(g4)
        out["g1_peso_btc"] = out["date"].map(g["ubtc"] * g["mark_px"] / g["equity"])
    return out


def ledgers_for_dashboard(store) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Ledger del papel (con el cierre de la señal del día) y de la sombra G1, como los espera paper_daily."""
    q = ("SELECT l.date, l.usdc, l.ubtc, l.mark_px, l.equity, l.target, s.close FROM ledger l "
         "LEFT JOIN signals s ON s.date = l.date AND s.mode = 'paper' WHERE l.mode = ? ORDER BY l.date")
    paper = pd.read_sql(q, store.con, params=("paper",))
    g1 = pd.read_sql(q, store.con, params=("shadow_g1",))
    paper["close"] = paper["close"].fillna(paper["mark_px"])
    return paper, (g1 if len(g1) else None)
