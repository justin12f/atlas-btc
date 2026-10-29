"""
Señal diaria: serie cosida -> engine.strategy.build_targets -> objetivo del día.

Regla 2: aquí no se reimplementa nada de la estrategia; solo se llama a engine/.
La serie se recorta en `date` antes de calcular (sin look-ahead) y debe empezar donde empieza la semilla
del backtest (las EWM y el recorte de 200 filas de engine.signals dependen del inicio).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from atlas.interfaces import Signal
from engine.strategy import build_targets, load_config, portfolio_config


class SignalError(RuntimeError):
    pass


def compute_targets(d: pd.DataFrame, C: dict | None = None) -> pd.DataFrame:
    """Todos los objetivos de la serie, exactamente como el backtest (ML apagado en v3)."""
    C = C or load_config()
    if C["ml"].get("enabled", True):
        raise SignalError("strategy.yaml tiene ML encendido: atlas no opera con ML (v3 lo apaga)")
    return build_targets(d, None, C, portfolio_config(C))


def compute_signal(d: pd.DataFrame, date: str | pd.Timestamp, config_hash: str, code_version: str,
                   C: dict | None = None) -> Signal:
    """Objetivo para el cierre de `date` usando solo datos hasta `date` inclusive."""
    day = pd.Timestamp(date).normalize()
    if day not in d.index:
        raise SignalError(f"no hay barra diaria para {day.date()}")
    cut = d.loc[:day]
    last = cut.iloc[-1]
    if not np.isfinite(last["close"]) or not np.isfinite(last["rv"]):
        raise SignalError(f"barra incompleta en {day.date()} (close o rv faltante)")
    tg = compute_targets(cut[["open", "high", "low", "close", "volume", "rv", "rv_down"]], C)
    if day not in tg.index or not np.isfinite(tg.loc[day, "target"]):
        raise SignalError(f"engine no produjo objetivo para {day.date()}")
    r = tg.loc[day]
    return Signal(date=str(day.date()), trend=float(r["core_trend"]), cycle_mult=float(r["core_cycle_mult"]),
                  vol_fc=float(r["core_vol_fc"]), vol_scaler=float(r["core_vol_scaler"]), target=float(r["target"]),
                  close=float(last["close"]), config_hash=config_hash, code_version=code_version)
