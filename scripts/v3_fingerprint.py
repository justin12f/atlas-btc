"""
Huella numérica de Atlas v3 (solo la estrategia, sin benchmarks de acciones).

`scripts/run_backtest.py` necesita data/raw/ (398 MB) por los benchmarks de acciones; este script
solo usa data/processed/btc_daily.parquet y sirve para probar que un refactor de engine/ no cambia
resultados (regla 1 de CLAUDE.md):

  python scripts/v3_fingerprint.py > antes.json   # antes del cambio
  python scripts/v3_fingerprint.py > despues.json # después
  diff antes.json despues.json                     # debe salir vacío
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.metrics import perf_stats  # noqa: E402
from engine.portfolio import simulate  # noqa: E402
from engine.strategy import build_targets, load_config, portfolio_config  # noqa: E402


def _h(a: pd.Series | pd.DataFrame) -> str:
    """sha256 de los valores exactos (bytes de float64)."""
    return hashlib.sha256(np.ascontiguousarray(a.to_numpy(dtype="float64")).tobytes()).hexdigest()


def fingerprint() -> dict:
    C = load_config()
    P = C["periods"]
    d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").loc[:P["holdout_end"]]
    pcfg = portfolio_config(C)
    tg = build_targets(d, None, C, pcfg)
    sim = simulate(d, tg["target"], pcfg, P["dev_start"], P["holdout_end"])
    out = {"targets_sha256": _h(tg["target"].dropna()),
           "sim_sha256": _h(sim[["weight", "trade", "cost", "ret", "equity"]]),
           "n_trades": int((sim["trade"].abs() > 0).sum()),
           "final_equity": float(sim["equity"].iloc[-1]),
           "current_target": float(tg["target"].dropna().iloc[-1])}
    for name, (a, b) in {"full": (P["dev_start"], P["holdout_end"]), "dev": (P["dev_start"], P["dev_end"]),
                         "holdout": (P["holdout_start"], P["holdout_end"])}.items():
        s = perf_stats(sim["ret"].loc[a:b], periods=365)
        out[name] = {k: round(float(s[k]), 10) for k in ("cagr", "sharpe", "max_dd")}
    return out


if __name__ == "__main__":
    json.dump(fingerprint(), sys.stdout, indent=1)
    print()
