"""
Evalúa config/preregistro_crecimiento_2026-11.yaml (Atlas Crecimiento) contra Atlas v3 y BTC comprado y mantenido.
Solo investigación: las variantes se arman en memoria; engine/ y config/strategy.yaml no cambian.

  python scripts/research_growth_2026_11.py > results/crecimiento_2026_11.json
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.portfolio import simulate  # noqa: E402
from engine.strategy import build_targets, load_config, portfolio_config  # noqa: E402

_spec = importlib.util.spec_from_file_location("ri", ROOT / "scripts/research_ideas_2026_11.py")
ri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ri)
cagr, mdd, boot = ri.cagr, ri.mdd, ri.boot_cagr_diff

PR = yaml.safe_load(open(ROOT / "config/preregistro_crecimiento_2026-11.yaml"))
DEV, HOLD = PR["periodos"]["desarrollo"], PR["periodos"]["holdout"]
STARTS, END = PR["periodos"]["inicios_robustez"], PR["periodos"]["holdout"][1]
KILL = PR["comun_a_las_tres"]["interruptor_caida"]


def half_fixed(target: pd.Series) -> pd.Series:
    """G2: mitad siempre en BTC + mitad siguiendo a Atlas v3."""
    return 0.5 + 0.5 * target


def run(d: pd.DataFrame, C: dict, transform=None, kill: float | None = None, apy: float = 0.0,
        cost_mult: float = 1.0) -> pd.Series:
    over = {} if kill is None else {"kill_dd": kill}
    pc = portfolio_config(C, usdc_apy=apy, **over)
    pc.fee, pc.slippage = pc.fee * cost_mult, pc.slippage * cost_mult
    tg = build_targets(d, None, C, pc)["target"]
    if transform is not None:
        tg = transform(tg)
    return simulate(d, tg, pc, DEV[0], END)["ret"]


def main() -> dict:
    C0 = load_config()
    d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").loc[:END]
    C1, C3 = copy.deepcopy(C0), copy.deepcopy(C0)
    C1["risk"]["target_vol"], C3["risk"]["target_vol"] = 99.0, 0.60
    spec = {"G1_sin_freno_vol": dict(C=C1), "G2_mitad_fija": dict(C=C0, transform=half_fixed),
            "G3_vol60": dict(C=C3)}
    base = run(d, C0)
    base25 = run(d, C0, cost_mult=2.5)
    btc = d["close"].pct_change().loc[base.index]
    out = {"comparadores": {}, "variantes": {}}
    for name, r in {"Atlas v3": base, "BTC comprado y mantenido": btc}.items():
        out["comparadores"][name] = {
            "cagr_dev": cagr(r.loc[DEV[0]:DEV[1]]), "mdd_dev": mdd(r.loc[DEV[0]:DEV[1]]),
            "ret_holdout": float((1 + r.loc[HOLD[0]:HOLD[1]]).prod() - 1),
            "cagr_completo": cagr(r), "mdd_completo": mdd(r), "cagr_por_inicio": {s: cagr(r.loc[s:]) for s in STARTS}}
    out["comparadores"]["Atlas v3"]["cagr_completo_usdc4"] = cagr(run(d, C0, apy=0.04))
    bh = out["comparadores"]["Atlas v3"]["ret_holdout"]
    for k, v in spec.items():
        r = run(d, kill=KILL, **v)
        dev, hold = r.loc[DEV[0]:DEV[1]], r.loc[HOLD[0]:HOLD[1]]
        lo, hi = boot(dev, base.loc[DEV[0]:DEV[1]])
        w_v3 = sum(cagr(r.loc[s:]) > cagr(base.loc[s:]) for s in STARTS)
        w_btc = sum(cagr(r.loc[s:]) > cagr(btc.loc[s:]) for s in STARTS)
        d25 = cagr(run(d, kill=KILL, cost_mult=2.5, **v).loc[DEV[0]:DEV[1]]) - cagr(base25.loc[DEV[0]:DEV[1]])
        row = {"cagr_dev": cagr(dev), "mdd_dev": mdd(dev), "ret_holdout": float((1 + hold).prod() - 1),
               "mdd_holdout": mdd(hold), "cagr_completo": cagr(r), "mdd_completo": mdd(r),
               "cagr_completo_usdc4": cagr(run(d, kill=KILL, apy=0.04, **v)),
               "cagr_completo_interruptor36": cagr(run(d, **v)),
               "exposicion_media": None,
               "cagr_por_inicio": {s: cagr(r.loc[s:]) for s in STARTS},
               "dif_cagr_dev_vs_v3": cagr(dev) - cagr(base.loc[DEV[0]:DEV[1]]), "ic95": [lo, hi],
               "gana_a_v3": f"{w_v3}/6", "gana_a_btc": f"{w_btc}/6", "dif_costos_x2.5": d25}
        crit = {"C1": lo > 0, "C2": row["mdd_dev"] >= -0.60, "C3": row["ret_holdout"] >= bh - 0.05,
                "C4": w_v3 >= 4, "C5": w_btc >= 4, "C6": d25 > 0}
        row["criterios"], row["pasa"] = crit, all(crit.values())
        pc = portfolio_config(v["C"], kill_dd=KILL)
        tg = build_targets(d, None, v["C"], pc)["target"]
        tg = v.get("transform", lambda x: x)(tg)
        row["exposicion_media"] = float(simulate(d, tg, pc, DEV[0], END)["weight"].mean())
        out["variantes"][k] = row
    return out


if __name__ == "__main__":
    json.dump(main(), sys.stdout, indent=1, default=lambda o: bool(o) if isinstance(o, np.bool_) else str(o))
    print()
