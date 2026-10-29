from __future__ import annotations

import numpy as np
import pandas as pd


def perf_stats(daily_ret: pd.Series, periods: int = 365, rf: float = 0.0) -> dict:
    r = daily_ret.dropna()
    if len(r) < 5:
        return {}
    eq = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25 or len(r) / periods
    total = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = r.std() * np.sqrt(periods)
    downside = r[r < 0].std() * np.sqrt(periods)
    dd = eq / eq.cummax() - 1
    m = monthly_returns(r)
    return {
        "total_return": total,
        "cagr": cagr,
        "vol": vol,
        "sharpe": (r.mean() * periods - rf) / vol if vol > 0 else np.nan,
        "sortino": (r.mean() * periods - rf) / downside if downside > 0 else np.nan,
        "max_dd": dd.min(),
        "calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
        "avg_month": m.mean(),
        "median_month": m.median(),
        "pct_pos_months": (m > 0).mean(),
        "best_month": m.max(),
        "worst_month": m.min(),
        "n_months": len(m),
    }


def monthly_returns(daily_ret: pd.Series) -> pd.Series:
    r = daily_ret.dropna()
    return (1 + r).groupby(r.index.to_period("M")).prod() - 1


def drawdown(daily_ret: pd.Series) -> pd.Series:
    eq = (1 + daily_ret.fillna(0)).cumprod()
    return eq / eq.cummax() - 1
