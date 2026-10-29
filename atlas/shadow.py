"""
Sombra de stablecoins (sección 16): se registra cada día en la tabla `shadow`; NO cambia la exposición.
Usa engine.shadow (misma regla que el pre-registro v3). Falla de red -> evento, sin afectar la corrida diaria.
"""
from __future__ import annotations

import json

import pandas as pd

from atlas.data.store import SQLiteStore
from engine.shadow import fetch_supply_llama, stablecoin_signal


def shadow_row(supply: pd.Series, date: str, atlas_target: float | None) -> dict:
    sig = stablecoin_signal(supply).loc[:date].dropna(subset=["growth_30d"]).iloc[-1]
    mult = float(sig["shadow_mult"])
    return {"date": date, "atlas_target": atlas_target, "stablecoin_growth_30d": float(sig["growth_30d"]),
            "shadow_mult": mult, "shadow_target": None if atlas_target is None else atlas_target * mult}


def record_shadow(store: SQLiteStore, date: str, atlas_target: float | None, fetch=fetch_supply_llama) -> dict | None:
    try:
        row = shadow_row(fetch(), date, atlas_target)
    except Exception as e:                        # la sombra nunca tumba la corrida
        store.log_event("warn", "shadow", f"{type(e).__name__}: {e}")
        return None
    with store.tx():
        store.con.execute("INSERT OR REPLACE INTO shadow(date, payload_json) VALUES (?, ?)", (date, json.dumps(row)))
    return row
