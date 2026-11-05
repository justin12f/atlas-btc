"""
Datos del tablero "Atlas vs lo esperado" (solo lectura: no toca engine/, config ni el ledger).

  python scripts/dashboard_data.py ledger_paper.csv carpeta_salida/ [ledger_shadow_g1.csv]

ledger_paper.csv: export del ledger del papel (date, usdc, ubtc, mark_px, equity, target, close).
Escribe:
  paper_daily.csv     equity diaria de Atlas, Atlas + USDC 4% (derivada) y BTC, todo desde 10,000
  expected_cone.csv   por día h (0–365): percentiles 5/25/50/75/95 de la equity del backtest v3 tras h días,
                      sobre todas las ventanas que empiezan en cualquier día 2018-01-01 → 2026-10-22
  backtest_stats.csv  rendimiento anual, multiplicador y peor caída por estrategia y periodo
  yearly.csv          rendimiento por año calendario
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from atlas.shadow_g1 import G1_KILL_DD, g1_config  # noqa: E402
from atlas.variants import paper_daily  # noqa: E402
from engine.portfolio import simulate  # noqa: E402
from engine.strategy import build_targets, load_config, portfolio_config  # noqa: E402

START = 10_000.0
HORIZON = 365


def backtest_returns() -> tuple[pd.DataFrame, dict]:
    C = load_config()
    P = C["periods"]
    d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").loc[:P["holdout_end"]]
    out = {}
    for name, pc in {"Atlas v3": portfolio_config(C), "Atlas v3 + USDC 4%": portfolio_config(C, usdc_apy=0.04)}.items():
        tg = build_targets(d, None, C, pc)
        out[name] = simulate(d, tg["target"], pc, P["dev_start"], P["holdout_end"])["ret"]
    G = g1_config(C)
    for name, apy in {"G1 sin freno de vol": 0.0, "G1 + USDC 4%": 0.04}.items():
        pc = portfolio_config(G, usdc_apy=apy, kill_dd=G1_KILL_DD)
        out[name] = simulate(d, build_targets(d, None, G, pc)["target"], pc, P["dev_start"], P["holdout_end"])["ret"]
    r = pd.DataFrame(out)
    r["BTC comprado y mantenido"] = d["close"].pct_change().reindex(r.index)
    return r, P


def cone(r: pd.Series) -> pd.DataFrame:
    logr = np.log1p(r.to_numpy())
    cs = np.concatenate([[0.0], np.cumsum(logr)])
    rows = []
    for h in range(HORIZON + 1):
        g = np.exp(cs[h:] - cs[: len(cs) - h]) if h else np.ones(len(cs))
        q = np.quantile(g, [0.05, 0.25, 0.5, 0.75, 0.95])
        rows.append({"day": h, **{f"p{p}": START * v for p, v in zip((5, 25, 50, 75, 95), q)}, "windows": len(g)})
    return pd.DataFrame(rows)


def stats(r: pd.DataFrame, P: dict) -> pd.DataFrame:
    periods = {"Diseño 2018-01 → 2025-10": (P["dev_start"], P["dev_end"]),
               "Prueba fuera de muestra 2025-10 → 2026-10": (P["holdout_start"], P["holdout_end"]),
               "Completo 2018-01 → 2026-10": (P["dev_start"], P["holdout_end"])}
    rows = []
    for per, (a, b) in periods.items():
        for col in r.columns:
            x = r[col].loc[a:b].dropna()
            eq = (1 + x).cumprod()
            rows.append({"periodo": per, "estrategia": col, "anual": eq.iloc[-1] ** (365 / len(x)) - 1,
                         "multiplicador": eq.iloc[-1], "peor_caida": (eq / eq.cummax() - 1).min(),
                         "anos": len(x) / 365})
    return pd.DataFrame(rows)


def yearly(r: pd.DataFrame) -> pd.DataFrame:
    y = (1 + r).groupby(r.index.year).prod() - 1
    y.index.name = "ano"
    return y.reset_index()


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    r, P = backtest_returns()
    g1 = pd.read_csv(sys.argv[3]) if len(sys.argv) > 3 else None
    paper_daily(pd.read_csv(src), g1).to_csv(dst / "paper_daily.csv", index=False)
    cone(r["Atlas v3"]).to_csv(dst / "expected_cone.csv", index=False)
    stats(r, P).to_csv(dst / "backtest_stats.csv", index=False)
    yearly(r).to_csv(dst / "yearly.csv", index=False)
    print(*(p.name for p in dst.glob("*.csv")))
