"""
Chequeos de calidad de datos (secciones 7.2 y 10). Regla 6: ante datos dudosos NO se opera.

Un día de 5m es bueno si: 288 velas, sin huecos > 10 min y sin saltos > 8% en 5 min
que el perp no confirme (mismo signo y al menos la mitad del tamaño).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BARS_PER_DAY = 288


def day_frame(c5: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """Velas de 5m cuyo inicio cae en el día UTC `day` (índice = inicio de vela)."""
    day = pd.Timestamp(day).normalize()
    return c5.loc[(c5.index >= day) & (c5.index < day + pd.Timedelta(days=1))]


def check_day(spot5: pd.DataFrame, day: pd.Timestamp, perp5: pd.DataFrame | None = None,
              max_jump: float = 0.08) -> list[str]:
    """Banderas de calidad para el día. Lista vacía = día bueno."""
    flags: list[str] = []
    d = day_frame(spot5, day)
    if len(d) != BARS_PER_DAY:
        flags.append(f"n_5m={len(d)}")
    if len(d) >= 2:
        gap = d.index.to_series().diff().max()
        if gap > pd.Timedelta(minutes=10):
            flags.append(f"gap={int(gap.total_seconds() // 60)}m")
    if len(d):
        r = np.log(d["c"].astype(float)).diff()
        jumps = r[r.abs() > np.log1p(max_jump)]
        if len(jumps):
            if perp5 is None or perp5.empty:
                flags.append(f"jump_unconfirmed={len(jumps)}")
            else:
                pr = np.log(perp5["c"].astype(float)).diff().reindex(jumps.index)
                ok = (np.sign(pr) == np.sign(jumps)) & (pr.abs() >= jumps.abs() / 2)
                if not ok.all():
                    flags.append(f"jump_unconfirmed={int((~ok).sum())}")
    if len(d) and (d[["o", "h", "l", "c"]].astype(float) <= 0).any().any():
        flags.append("nonpositive_px")
    return flags


def ref_divergence(px: float, ref: float) -> float:
    return abs(px / ref - 1)


def data_age_min(last_candle_close_ms: int, now_ms: int) -> float:
    return (now_ms - last_candle_close_ms) / 60_000
