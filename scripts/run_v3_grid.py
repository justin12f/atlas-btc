"""Corre las 384 permutaciones pre-registradas y guarda retornos diarios y metricas."""
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.metrics import perf_stats  # noqa: E402
from engine.v3 import VariantBuilder  # noqa: E402

OUT = ROOT / "results" / "v3"
OUT.mkdir(parents=True, exist_ok=True)
FACTORS = {"T": ["T0", "T1", "T2"], "V": ["ewma", "har"], "D": ["none", "gz"], "M": ["off", "on"],
           "C": ["on", "off"], "E": ["off", "on"], "S": ["off", "on"], "P": ["on", "off"]}
DEV = ("2018-01-01", "2025-10-22")
HOLD = ("2025-10-23", "2026-10-22")
HOLD1 = ("2025-10-23", "2026-05-23")

ml = pd.read_parquet(ROOT / "results/ml_pred_final.parquet")
vb = VariantBuilder(ROOT, ml)
variants = [dict(zip(FACTORS, combo)) for combo in itertools.product(*FACTORS.values())]
assert variants[0] == {"T": "T0", "V": "ewma", "D": "none", "M": "off", "C": "on", "E": "off", "S": "off", "P": "on"}

rets, rows = {}, []
t0 = time.time()
for i, v in enumerate(variants):
    vid = "_".join(f"{k}{v[k]}" for k in FACTORS)
    sim = vb.run(v)
    rets[vid] = sim["ret"]
    row = {"id": vid, **v}
    for nm, (a, b) in {"dev": DEV, "hold": HOLD, "hold1": HOLD1}.items():
        s = perf_stats(sim["ret"].loc[a:b])
        for k in ["cagr", "sharpe", "max_dd", "vol", "calmar", "avg_month", "total_return"]:
            row[f"{nm}_{k}"] = float(s[k])
    row["dev_turnover_yr"] = float(sim["turnover"].loc[DEV[0]:DEV[1]].sum() / ((len(sim.loc[DEV[0]:DEV[1]])) / 365))
    row["dev_avg_expo"] = float(sim["weight"].loc[DEV[0]:DEV[1]].mean())
    yr = sim["ret"].loc[DEV[0]:DEV[1]]
    for y, g in yr.groupby(yr.index.year):
        row[f"sr_{y}"] = float(g.mean() / g.std() * np.sqrt(365)) if g.std() > 0 else 0.0
        row[f"ret_{y}"] = float((1 + g).prod() - 1)
    rows.append(row)
    if i % 48 == 0:
        print(f"{i}/{len(variants)}  {time.time() - t0:.0f}s", flush=True)

R = pd.DataFrame(rets)
R.to_parquet(OUT / "daily_returns.parquet")
tab = pd.DataFrame(rows)
tab.to_csv(OUT / "grid_summary.csv", index=False)
print("listo", R.shape, f"{time.time() - t0:.0f}s")
print(tab.sort_values("dev_sharpe", ascending=False)[["id", "dev_sharpe", "dev_cagr", "dev_max_dd", "hold_sharpe", "hold_cagr"]].head(15).to_string())
print("base:", tab.iloc[0][["dev_sharpe", "dev_cagr", "dev_max_dd"]].to_dict())
