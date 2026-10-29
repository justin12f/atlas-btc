"""Aplica las pruebas pre-registradas a las 384 permutaciones (solo periodo de desarrollo)."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.luck_tests import (bootstrap_sharpes, deflated_sharpe, pbo_cscv, reality_check,  # noqa: E402
                               romano_wolf, sharpe_cols, stationary_bootstrap)

OUT = ROOT / "results" / "v3"
DEV = ("2018-01-01", "2025-10-22")
FACTORS = {"T": ["T0", "T1", "T2"], "V": ["ewma", "har"], "D": ["none", "gz"], "M": ["off", "on"],
           "C": ["on", "off"], "E": ["off", "on"], "S": ["off", "on"], "P": ["on", "off"]}
BASE = "TT0_Vewma_Dnone_Moff_Con_Eoff_Soff_Pon"

tab = pd.read_csv(OUT / "grid_summary.csv").set_index("id")
R_all = pd.read_parquet(OUT / "daily_returns.parquet")
R = R_all.loc[DEV[0]:DEV[1], tab.index]
X = R.to_numpy()
ids = list(R.columns)
base = ids.index(BASE)
t0 = time.time()
idx = stationary_bootstrap(len(X), B=2000, mean_block=20, seed=11)
sr = sharpe_cols(X)
srb = bootstrap_sharpes(X, idx)
print("bootstrap listo", f"{time.time() - t0:.0f}s", flush=True)

# max drawdown por remuestreo no se usa; se usa el observado
dd = tab["dev_max_dd"].to_numpy()

# ------------------------------------------------------------ 1. efectos principales
effects = []
for f, levels in FACTORS.items():
    ref = levels[0]
    for alt in levels[1:]:
        pairs = []
        for vid in ids:
            if tab.loc[vid, f] != ref:
                continue
            other = vid.replace(f"{f}{ref}", f"{f}{alt}")
            pairs.append((ids.index(vid), ids.index(other)))
        a = np.array([p[0] for p in pairs]); b = np.array([p[1] for p in pairs])
        dsr_obs = sr[b] - sr[a]
        eff = dsr_obs.mean()
        eff_boot = (srb[:, b] - srb[:, a]).mean(axis=1)
        lo, hi = np.quantile(eff_boot, [0.025, 0.975])
        # p unilateral: probabilidad de que el efecto sea <= 0 (bootstrap recentrado)
        p_one = float(((eff_boot - eff) >= eff).mean()) if eff > 0 else 1.0
        d_dd = (dd[b] - dd[a]).mean()
        d_cagr = (tab["dev_cagr"].to_numpy()[b] - tab["dev_cagr"].to_numpy()[a]).mean()
        d_hold = (tab["hold_sharpe"].to_numpy()[b] - tab["hold_sharpe"].to_numpy()[a]).mean()
        d_hold1 = (tab["hold1_sharpe"].to_numpy()[b] - tab["hold1_sharpe"].to_numpy()[a]).mean()
        passes = (lo > 0) and ((dsr_obs > 0).mean() >= 0.70) and (d_dd >= 0)
        effects.append({"factor": f, "from": ref, "to": alt, "n_pairs": len(pairs), "d_sharpe": eff,
                        "ci_lo": lo, "ci_hi": hi, "p_one_sided": p_one, "share_improved": (dsr_obs > 0).mean(),
                        "d_maxdd": d_dd, "d_cagr": d_cagr, "d_hold_sharpe": d_hold, "d_hold1_sharpe": d_hold1,
                        "passes_criteria": bool(passes)})
eff = pd.DataFrame(effects)
eff.to_csv(OUT / "main_effects.csv", index=False)
pd.set_option("display.width", 250)
print(eff.round(3).to_string())

# ------------------------------------------------------------ 2. mejor permutacion factible
feasible = tab[tab["dev_max_dd"] >= -0.30]
best_id = feasible["dev_sharpe"].idxmax()
best = ids.index(best_id)
print("\nmejor factible:", best_id, tab.loc[best_id, ["dev_sharpe", "dev_cagr", "dev_max_dd", "hold_sharpe", "hold_cagr", "hold_max_dd"]].to_dict())

# ------------------------------------------------------------ 3. Reality Check y Romano-Wolf
rc = reality_check(sr, srb, base)
rw = romano_wolf(sr, srb, base)
d_best = srb[:, best] - srb[:, base]
p_pair = float(((d_best - (sr[best] - sr[base])) >= (sr[best] - sr[base])).mean())
print("RC:", rc, "| mejor RC:", ids[rc["best_k"]])
print("Romano-Wolf significativas:", [ids[k] for k in rw["significant"]][:20], "n =", len(rw["significant"]))
print("prueba pareada (sin correccion) mejor vs base: p =", p_pair)

# ------------------------------------------------------------ 4. PBO
pbo = pbo_cscv(X, S=16)
print("PBO:", pbo)

# ------------------------------------------------------------ 5. Sharpe deflactado
dsr_best = deflated_sharpe(X[:, best], sr, n_trials=384 + 30)
dsr_base = deflated_sharpe(X[:, base], sr, n_trials=384 + 30)
print("DSR mejor:", dsr_best, "| DSR base:", dsr_base)

# ------------------------------------------------------------ 6. estabilidad por anio
years = [c for c in tab.columns if c.startswith("sr_")]
yr = pd.DataFrame({"base": tab.loc[BASE, years], "best": tab.loc[best_id, years]})
yr["best_wins"] = yr["best"] > yr["base"]
print(yr.round(2).to_string())

summary = {
    "base": BASE, "best_feasible": best_id,
    "sr_base": float(sr[base]), "sr_best": float(sr[best]),
    "reality_check": {**rc, "best_id": ids[rc["best_k"]]},
    "romano_wolf_significant": [ids[k] for k in rw["significant"]],
    "paired_p_best_vs_base_unadjusted": p_pair,
    "pbo": pbo, "dsr_best": dsr_best, "dsr_base": dsr_base,
    "years_best_beats_base": int(yr["best_wins"].sum()), "n_years": int(len(yr)),
    "n_feasible": int(len(feasible)),
}
json.dump(summary, open(OUT / "luck_summary.json", "w"), indent=2)
np.save(OUT / "boot_sharpes.npy", srb)
print(json.dumps({k: v for k, v in summary.items() if k != "romano_wolf_significant"}, indent=1, default=str))
