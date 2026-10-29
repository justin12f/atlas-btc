"""
Costura de la historia (sección 7.3): Bitstamp (semilla del backtest) + Hyperliquid -> serie diaria continua.

- Hasta `bitstamp_last_day` (inclusive) se usa la semilla tal cual: así la señal en vivo sobre la historia es
  idéntica al backtest (las EWM y el recorte de 200 filas de engine.signals dependen del inicio de la serie).
- Después: OHLCV de la vela 1d de HL y rv/rv_down de las velas 5m de HL (día completo, 288 velas).
  Si faltan 5m de HL se usa Binance 5m guardado con source='binance'. Si tampoco, rv = NaN y bandera.
- Cada fila lleva `source` ("bitstamp" | "hl" | "hl+binance_rv") y `quality_flags`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from atlas.data.quality import BARS_PER_DAY, check_day

SEED_COLS = ["open", "high", "low", "close", "volume", "rv", "rv_down"]


def to_frame(rows: list[dict]) -> pd.DataFrame:
    """Filas de velas (de HL o de la base) -> DataFrame con índice = inicio UTC (naive) y columnas float."""
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v"], index=pd.DatetimeIndex([], name="ts"))
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms")
    df.index.name = "ts"
    for k in ("o", "h", "l", "c", "v"):
        df[k] = df[k].astype(float)
    return df.sort_index()


def rv_by_day(c5: pd.DataFrame) -> pd.DataFrame:
    """rv y rv_down por día, igual que engine/data.py: retornos log de cierres de 5m consecutivos
    (el primero del día usa el cierre de las 23:55 del día anterior). Solo días con 288 velas y
    con el cierre previo disponible."""
    if c5.empty:
        return pd.DataFrame(columns=["rv", "rv_down", "n_5m"])
    full = c5["c"].reindex(pd.date_range(c5.index.min(), c5.index.max(), freq="5min"))
    r = np.log(full).diff()
    day = r.index.floor("D")
    out = pd.DataFrame({"rv": (r ** 2).groupby(day).sum(min_count=1),
                        "rv_down": (r.clip(upper=0) ** 2).groupby(day).sum(min_count=1),
                        "n_5m": full.notna().groupby(day).sum(),
                        "n_ret": r.notna().groupby(day).sum()})
    good = (out["n_5m"] == BARS_PER_DAY) & (out["n_ret"] == BARS_PER_DAY)
    out.loc[~good, ["rv", "rv_down"]] = np.nan
    return out.drop(columns="n_ret")


def hl_daily(c1d: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Velas 1d de HL -> OHLCV diario; descarta la vela del día en curso."""
    if c1d.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    d = c1d.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    d = d[["open", "high", "low", "close", "volume"]].copy()
    d.index = d.index.normalize()
    if now is not None:
        d = d[d.index + pd.Timedelta(days=1) <= pd.Timestamp(now).tz_localize(None)]
    return d


def stitch(seed: pd.DataFrame, spot1d: pd.DataFrame, spot5: pd.DataFrame, seed_last_day: str,
           perp5: pd.DataFrame | None = None, binance5: pd.DataFrame | None = None,
           now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Serie diaria continua para engine/. Índice `date` (naive, 00:00 UTC del día)."""
    seed_last = pd.Timestamp(seed_last_day)
    base = seed.loc[:seed_last, SEED_COLS].copy()
    base["n_5m"] = np.nan
    base["source"] = "bitstamp"
    base["quality_flags"] = ""

    hl = hl_daily(spot1d, now).loc[seed_last + pd.Timedelta(days=1):]
    if hl.empty:
        return base
    rv_hl = rv_by_day(spot5)
    rv_bn = rv_by_day(binance5) if binance5 is not None and not binance5.empty else None
    rows = []
    for day, bar in hl.iterrows():
        flags: list[str] = []
        src = "hl"
        rv = rv_hl["rv"].get(day, np.nan)
        rvd = rv_hl["rv_down"].get(day, np.nan)
        n5 = rv_hl["n_5m"].get(day, 0)
        if np.isnan(rv) and rv_bn is not None and not np.isnan(rv_bn["rv"].get(day, np.nan)):
            rv, rvd, src = rv_bn["rv"][day], rv_bn["rv_down"][day], "hl+binance_rv"
            flags.append("rv_binance")
        if np.isnan(rv):
            flags.append("rv_missing")
        elif src == "hl":
            flags += check_day(spot5, day, perp5)
        rows.append({**bar.to_dict(), "rv": rv, "rv_down": rvd, "n_5m": n5, "source": src,
                     "quality_flags": ",".join(flags)})
    ext = pd.DataFrame(rows, index=hl.index)
    ext.index = ext.index.astype(base.index.dtype)     # misma resolución que la semilla
    out = pd.concat([base, ext])
    out.index.name = "date"
    expected = pd.date_range(out.index.min(), out.index.max(), freq="D")
    if len(expected) != len(out):
        missing = expected.difference(out.index)
        out = out.reindex(expected.astype(out.index.dtype))
        out.loc[missing, "quality_flags"] = "day_missing"
        out.loc[missing, "source"] = "none"
        out.index.name = "date"
    return out


def bad_days(d: pd.DataFrame, since: str | None = None) -> pd.Series:
    """Días con banderas que impiden operar (cualquier bandera salvo 'rv_binance', que solo avisa)."""
    q = d["quality_flags"].fillna("")
    if since:
        q = q.loc[since:]
    blocking = q.apply(lambda s: any(f and f != "rv_binance" for f in s.split(",")))
    return q[blocking]
