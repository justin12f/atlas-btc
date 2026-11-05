"""
Sombra G1 "sin freno de volatilidad" (config/preregistro_crecimiento_2026-11.yaml). NO opera ni cambia Atlas v3.

G1 = Atlas v3 con risk.target_vol = 99 (el escalador de volatilidad queda en 1): objetivo = tendencia × ciclo.
El objetivo lo calcula engine (atlas.signal.compute_targets con una copia de la config en memoria, regla 2).
Contabilidad simulada en la tabla `ledger` con modo 'shadow_g1': se ejecuta al cierre diario con el costo del
backtest (fee + slippage), misma banda de 10 puntos e interruptor al 60% (pausa sin reanudación automática).
Estado: no pasó el criterio C1 (IC95%) del pre-registro; corre solo para observarla en vivo 12 meses.
"""
from __future__ import annotations

import copy

import pandas as pd

from atlas.data.store import SQLiteStore
from atlas.signal import compute_targets
from engine.portfolio import should_trade

MODE = "shadow_g1"
G1_TARGET_VOL = 99.0      # pre-registro: escalador de volatilidad = 1
G1_KILL_DD = 0.60         # pre-registro: interruptor común a las variantes de crecimiento


def g1_config(C: dict) -> dict:
    g = copy.deepcopy(C)
    g["risk"]["target_vol"] = G1_TARGET_VOL
    return g


def step(prev: dict | None, target: float, close: float, band: float, cost_rate: float, initial: float,
         kill_dd: float = G1_KILL_DD) -> dict:
    """Un día de la sombra: marca a `close`, revisa el interruptor y rebalancea a `target` si sale de la banda."""
    usdc, ubtc = (initial, 0.0) if prev is None else (prev["usdc"], prev["ubtc"])
    peak = initial if prev is None else prev["peak"]
    paused = False if prev is None else bool(prev["paused"])
    eq = usdc + ubtc * close
    peak = max(peak, eq)
    if eq / peak - 1 < -kill_dd:
        paused = True
    tgt = 0.0 if paused else float(target)
    w = ubtc * close / eq if eq > 0 else 0.0
    if should_trade(tgt, w, band):
        notional = (tgt - w) * eq
        cost = abs(notional) * cost_rate
        ubtc += notional / close
        usdc -= notional + cost
    return {"usdc": usdc, "ubtc": ubtc, "mark_px": close, "peak": max(peak, usdc + ubtc * close), "paused": paused,
            "target": tgt}


def record_g1(store: SQLiteStore, d: pd.DataFrame, C: dict, start: str, through: str, initial: float) -> int:
    """Escribe los días faltantes de la sombra entre `start` y `through` (idempotente: no reescribe días hechos).
    Cada objetivo usa solo datos hasta ese día. Devuelve cuántos días escribió."""
    g = g1_config(C)
    cost_rate = C["execution"]["fee"] + C["execution"]["slippage"]
    band = C["risk"]["band"]
    n = 0
    for day in pd.date_range(start, through, freq="D"):
        ds = str(day.date())
        if store.ledger_row(MODE, ds) or day not in d.index:
            continue
        tgt = compute_targets(d.loc[:day, ["open", "high", "low", "close", "volume", "rv", "rv_down"]], g)
        if day not in tgt.index or pd.isna(tgt.loc[day, "target"]):
            continue
        prev = store.last_ledger(MODE, before=ds)
        r = step(prev, tgt.loc[day, "target"], float(d.loc[day, "close"]), band, cost_rate, initial)
        store.write_ledger(MODE, ds, r["usdc"], r["ubtc"], r["mark_px"], r["peak"], r["paused"], r["target"])
        n += 1
    return n
