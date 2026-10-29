"""
Benchmarks: BTC buy&hold, QQQ, IWF, Magnificent 7 (equiponderado, rebalanceo mensual) y
Momentum S&P 500 top-10 (membresia historica, sin sesgo de supervivencia).
Todos con precios ajustados por splits y dividendos (retorno total).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import load_equity_prices, load_sp500_members

MAG7 = ["NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "TSLA"]


def _basket_daily(px: pd.DataFrame, holdings: dict[pd.Timestamp, list[str]], cost: float = 0.0) -> pd.Series:
    """Retornos diarios de canastas equiponderadas que se rebalancean en cada fecha de `holdings`.
    La canasta elegida al cierre de la fecha k se mantiene hasta la fecha k+1."""
    dates = sorted(holdings)
    out = []
    prev = set()
    for k, d0 in enumerate(dates):
        d1 = dates[k + 1] if k + 1 < len(dates) else px.index.max()
        names = [n for n in holdings[d0] if n in px.columns]
        if not names:
            continue
        seg = px.loc[d0:d1, names]
        if len(seg) < 2:
            continue
        seg = seg.ffill()
        rel = seg / seg.iloc[0]
        rel = rel.loc[:, rel.iloc[0].notna()]
        val = rel.mean(axis=1)                      # equiponderado al inicio, deriva despues
        r = val.pct_change().iloc[1:]
        turnover = len(set(names) ^ prev) / max(len(names), 1)
        if len(r):
            r.iloc[0] -= cost * turnover
        prev = set(names)
        out.append(r)
    s = pd.concat(out)
    return s[~s.index.duplicated()]


def equity_benchmarks(start: str = "2016-01-01") -> dict[str, pd.Series]:
    px = load_equity_prices(["QQQ", "IWF", "SPY", "VOO"] + MAG7, start=start)
    res = {
        "QQQ": px["QQQ"].pct_change(),
        "IWF": px["IWF"].pct_change(),
        "SPY": px["SPY"].pct_change(),
        "VOO": px["VOO"].pct_change(),
    }
    month_ends = px.resample("ME").apply(lambda x: x.index.max() if len(x) else pd.NaT).iloc[:, 0].dropna()
    hold = {pd.Timestamp(d): MAG7 for d in month_ends.values}
    res["Mag7"] = _basket_daily(px[MAG7], hold, cost=0.0005)
    return res


def momentum_top10(start: str = "2016-01-01", top: int = 10, lookback: int = 12, skip: int = 1,
                   cost: float = 0.001) -> pd.Series:
    px = load_equity_prices(None, start=str(pd.Timestamp(start) - pd.DateOffset(months=lookback + 2))[:10])
    mem = load_sp500_members()
    # cuarentena de tickers reciclados (misma logica que el dataset de origen)
    last_seen = mem.groupby("ticker")["date"].max()
    first_px = px.apply(lambda s: s.first_valid_index())
    recycled = {t for t in last_seen.index if t in first_px.index and pd.notna(first_px[t]) and first_px[t] > last_seen[t]}
    snaps = {d: set(g["ticker"]) - recycled for d, g in mem.groupby("date")}
    snap_dates = sorted(snaps)

    me_px = px.resample("ME").last()
    me_dates = [px.loc[:m].index.max() for m in me_px.index]  # ultimo dia habil real
    me_px.index = me_dates
    mom = me_px.shift(skip) / me_px.shift(lookback) - 1
    holdings = {}
    j = 0
    for d in me_px.index:
        if d < pd.Timestamp(start):
            continue
        while j + 1 < len(snap_dates) and snap_dates[j + 1] <= d:
            j += 1
        members = snaps[snap_dates[j]] if snap_dates[j] <= d else set()
        cand = mom.loc[d, [t for t in members if t in mom.columns]].dropna()
        holdings[d] = cand.sort_values(ascending=False).head(top).index.tolist()
    return _basket_daily(px, holdings, cost=cost), holdings
