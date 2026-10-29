"""
Capa de tendencia (mediano plazo): ensamble de seguimiento de tendencia multi-horizonte.
Sin parametros ajustados a los datos: horizontes clasicos fijados de antemano.
Salida: trend_score en [0, 1] = fraccion de senales alcistas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def donchian_state(d: pd.DataFrame, entry: int = 55, exit_: int = 20) -> pd.Series:
    """Tortuga: entra al romper maximo de `entry` dias, sale al romper minimo de `exit_` dias."""
    hi = d["high"].rolling(entry).max().shift(1)
    lo = d["low"].rolling(exit_).min().shift(1)
    c = d["close"].to_numpy()
    state = np.zeros(len(d))
    on = 0.0
    for i in range(len(d)):
        if not np.isnan(hi.iat[i]) and c[i] > hi.iat[i]:
            on = 1.0
        elif not np.isnan(lo.iat[i]) and c[i] < lo.iat[i]:
            on = 0.0
        state[i] = on
    return pd.Series(state, index=d.index)


def trend_signals(d: pd.DataFrame) -> pd.DataFrame:
    c = d["close"]
    s = pd.DataFrame(index=d.index)
    s["sma50"] = (c > c.rolling(50).mean()).astype(float)
    s["sma100"] = (c > c.rolling(100).mean()).astype(float)
    s["sma200"] = (c > c.rolling(200).mean()).astype(float)
    s["mom30"] = (c > c.shift(30)).astype(float)
    s["mom90"] = (c > c.shift(90)).astype(float)
    s["mom180"] = (c > c.shift(180)).astype(float)
    s["donch55"] = donchian_state(d, 55, 20)
    s["ema_cross"] = (c.ewm(span=20).mean() > c.ewm(span=100).mean()).astype(float)
    s.iloc[:200] = np.nan
    return s


def trend_score(d: pd.DataFrame) -> pd.Series:
    return trend_signals(d).mean(axis=1)
