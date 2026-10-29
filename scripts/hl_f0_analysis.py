"""
Fase 0: análisis de la fuente de la señal (sección 7.4 del plan) y paridad de datos (7.3).

Lee solo fixtures grabados en tests/fixtures/hl/ (no llama a la API), así que es reproducible:
  python scripts/hl_f0_analysis.py

Imprime un JSON con las métricas que se registran en docs/hl_facts_verified.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests" / "fixtures" / "hl"


def hl_candles(name: str) -> pd.DataFrame:
    """Velas de HL (lista de dicts con t,o,h,l,c,v,n) -> DataFrame indexado por inicio UTC."""
    raw = json.load(open(FX / f"{name}.json"))
    df = pd.DataFrame(raw)
    df["ts"] = pd.to_datetime(df["t"], unit="ms")
    df = df.set_index("ts")[["o", "h", "l", "c", "v", "n"]].astype(float)
    return df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})


def binance_5m() -> pd.DataFrame:
    raw = json.load(open(FX / "binance_btcusdc_5m_window.json"))
    df = pd.DataFrame(raw).iloc[:, :6]
    df.columns = ["t", "open", "high", "low", "close", "volume"]
    df["ts"] = pd.to_datetime(df["t"], unit="ms")
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype(float)


def daily_rv(k5: pd.DataFrame) -> pd.Series:
    """rv diario = suma de retornos log de 5m al cuadrado (igual que engine/data.py).
    Solo días completos (288 velas) cuyo cierre previo también existe."""
    c = k5["close"]
    full_idx = pd.date_range(c.index.min().ceil("D"), c.index.max().floor("D") - pd.Timedelta(minutes=5), freq="5min")
    c = c.reindex(pd.date_range(c.index.min(), c.index.max(), freq="5min"))
    r = np.log(c).diff()
    day = r.index.floor("D")
    rv = (r ** 2).groupby(day).sum(min_count=1)
    ok = r.notna().groupby(day).sum() == 288
    rv = rv[ok]
    return rv.loc[full_idx.min().floor("D"):full_idx.max().floor("D")]


def main() -> dict:
    sp1d, pp1d = hl_candles("candles_spot_1d_full"), hl_candles("candles_perp_1d_full")
    sp5, pp5, bn5 = hl_candles("candles_spot_5m_full"), hl_candles("candles_perp_5m_full"), binance_5m()
    bs = pd.read_parquet(ROOT / "data" / "processed" / "btc_daily.parquet")

    # --- días completos solamente (el último 1d está abierto)
    sp1d, pp1d = sp1d.iloc[:-1], pp1d.iloc[:-1]
    both = sp1d.index.intersection(pp1d.index)
    spot_vs_perp = (sp1d.loc[both, "close"] / pp1d.loc[both, "close"] - 1).abs()
    ntl = sp1d["volume"] * sp1d["close"]
    last90 = ntl.iloc[-90:]

    # --- paridad de cierre HL vs Bitstamp (backtest)
    ov = sp1d.index.intersection(bs.index)
    ovp = pp1d.index.intersection(bs.index)
    spot_vs_bs = np.log(sp1d.loc[ov, "close"] / bs.loc[ov, "close"]).abs()
    perp_vs_bs = np.log(pp1d.loc[ovp, "close"] / bs.loc[ovp, "close"]).abs()
    perp_recent = perp_vs_bs.loc["2023-06-01":]

    # --- rv
    rv = pd.DataFrame({"spot": daily_rv(sp5), "perp": daily_rv(pp5), "binance": daily_rv(bn5)}).dropna()
    rv["bitstamp"] = bs["rv"].reindex(rv.index)
    rvb = rv.dropna(subset=["bitstamp"])

    # --- huecos de 5m
    def gaps(k5: pd.DataFrame) -> int:
        full = pd.date_range(k5.index.min(), k5.index.max(), freq="5min")
        return int(len(full) - len(k5.index.unique()))

    out = {
        "spot_1d": {"first": str(sp1d.index.min().date()), "last_complete": str(sp1d.index.max().date()), "n": len(sp1d)},
        "perp_1d": {"first": str(pp1d.index.min().date()), "n": len(pp1d)},
        "spot_vs_perp_close": {"median_abs": float(spot_vs_perp.median()), "p95_abs": float(spot_vs_perp.quantile(0.95)),
                               "last90_median_abs": float(spot_vs_perp.iloc[-90:].median()), "n": len(both)},
        "spot_daily_notional_usd": {"median_all": float(ntl.median()), "median_last90": float(last90.median()),
                                    "min_last90": float(last90.min())},
        "close_vs_bitstamp": {"spot_median_abs_log": float(spot_vs_bs.median()), "spot_n": len(ov),
                              "perp_median_abs_log_since_2023_06": float(perp_recent.median()),
                              "perp_n_since_2023_06": len(perp_recent),
                              "perp_median_abs_log_before_2023_06": float(perp_vs_bs.loc[:"2023-05-31"].median())},
        "rv_ratio_median": {
            "spot_over_binance": float((rv["spot"] / rv["binance"]).median()),
            "perp_over_binance": float((rv["perp"] / rv["binance"]).median()),
            "spot_over_perp": float((rv["spot"] / rv["perp"]).median()),
            "spot_over_bitstamp": float((rvb["spot"] / rvb["bitstamp"]).median()) if len(rvb) else None,
            "perp_over_bitstamp": float((rvb["perp"] / rvb["bitstamp"]).median()) if len(rvb) else None,
            "binance_over_bitstamp": float((rvb["binance"] / rvb["bitstamp"]).median()) if len(rvb) else None,
            "n_days": len(rv), "n_days_bitstamp": len(rvb),
            "days": [str(x.date()) for x in rv.index],
        },
        "gaps_5m": {"spot": gaps(sp5), "perp": gaps(pp5), "binance": gaps(bn5),
                    "spot_n": len(sp5), "perp_n": len(pp5)},
    }
    return out


if __name__ == "__main__":
    json.dump(main(), sys.stdout, indent=1)
    print()
