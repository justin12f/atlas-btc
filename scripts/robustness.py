"""Verificacion de robustez de la estrategia final (despues del holdout; no cambia la config)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.metrics import perf_stats  # noqa: E402
from engine.portfolio import simulate  # noqa: E402
from engine.strategy import build_targets, load_config, portfolio_config  # noqa: E402

C = load_config()
P = C["periods"]
d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").loc[:P["holdout_end"]]
pred = pd.read_parquet(ROOT / "results/ml_pred_final.parquet")
START, END = P["dev_start"], P["holdout_end"]


def run(pcfg, lag=0, start=START):
    tg = build_targets(d, pred, C, pcfg)["target"]
    if lag:
        tg = tg.shift(lag)
    return simulate(d, tg, pcfg, start, END)["ret"]


def row(name, r):
    s = perf_stats(r)
    return {"prueba": name, "cagr": s["cagr"], "sharpe": s["sharpe"], "max_dd": s["max_dd"], "avg_month": s["avg_month"]}


rows = []
base = run(portfolio_config(C))
rows.append(row("Base (config bloqueada)", base))
rows.append(row("Ejecucion 1 dia tarde", run(portfolio_config(C), lag=1)))
rows.append(row("Ejecucion 3 dias tarde", run(portfolio_config(C), lag=3)))
rows.append(row("Costos x2.5 (0.25% + 0.08%)", run(portfolio_config(C, fee=0.0025, slippage=0.0008))))
rows.append(row("Vol objetivo 30%", run(portfolio_config(C, target_vol=0.30))))
rows.append(row("Vol objetivo 50%", run(portfolio_config(C, target_vol=0.50))))
rows.append(row("Sin capa de ciclo", run(portfolio_config(C, cycle_rule=False))))
rows.append(row("Ciclo con ventana pre-2018 [450,780]", run(portfolio_config(C, cycle_bear_window=(450, 780)))))
rows.append(row("Banda 5%", run(portfolio_config(C, band=0.05))))
rows.append(row("Banda 20%", run(portfolio_config(C, band=0.20))))
for s in ["2019-01-01", "2020-01-01", "2021-01-01", "2022-01-01"]:
    rows.append(row(f"Inicio {s[:4]}", run(portfolio_config(C), start=s)))
rob = pd.DataFrame(rows).set_index("prueba")

# --- Sharpe deflactado (Bailey & Lopez de Prado): penaliza por el numero de variantes probadas
n_trials = 414          # 30 variantes de la investigacion v2 + 384 permutaciones v3
r = base.loc[:P["dev_end"]]
sr = r.mean() / r.std()                      # Sharpe diario
T = len(r)
skew, kurt = r.skew(), r.kurt() + 3
trial_srs_var = (0.25 / np.sqrt(365)) ** 2   # dispersion supuesta de Sharpes anuales entre variantes (0.25)
emc = 0.5772156649
sr0 = np.sqrt(trial_srs_var) * ((1 - emc) * norm.ppf(1 - 1 / n_trials) + emc * norm.ppf(1 - 1 / (n_trials * np.e)))
dsr = norm.cdf((sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
psr_vs_btc = None
btc = d["close"].pct_change().loc[r.index]
diff = r - btc * (r.std() / btc.std())       # retorno extra vs BTC a igual volatilidad
t_stat = diff.mean() / diff.std() * np.sqrt(len(diff))

# --- distribucion de rendimientos mensuales y de 12 meses
m = (1 + base).groupby(base.index.to_period("M")).prod() - 1
mb = (1 + d["close"].pct_change().loc[START:END]).groupby(d.loc[START:END].index.to_period("M")).prod() - 1
roll12 = (1 + base).rolling(365).apply(np.prod, raw=True) - 1
roll12_btc = (1 + d["close"].pct_change().loc[START:END]).rolling(365).apply(np.prod, raw=True) - 1
dist = {
    "mensual_percentiles_atlas": {f"p{q}": float(m.quantile(q / 100)) for q in [5, 25, 50, 75, 95]},
    "mensual_percentiles_btc": {f"p{q}": float(mb.quantile(q / 100)) for q in [5, 25, 50, 75, 95]},
    "mensual_geometrico_atlas": float((1 + m).prod() ** (1 / len(m)) - 1),
    "mensual_geometrico_btc": float((1 + mb).prod() ** (1 / len(mb)) - 1),
    "pct_ventanas_12m_con_perdida_atlas": float((roll12.dropna() < 0).mean()),
    "pct_ventanas_12m_con_perdida_btc": float((roll12_btc.dropna() < 0).mean()),
    "peor_12m_atlas": float(roll12.min()), "peor_12m_btc": float(roll12_btc.min()),
    "mejor_12m_atlas": float(roll12.max()), "mejor_12m_btc": float(roll12_btc.max()),
    "pct_meses_en_usdc_mayormente": float((m.abs() < 0.002).mean()),
}
out = {"robustez": rob.reset_index().to_dict(orient="records"),
       "deflated_sharpe_prob": float(dsr), "n_trials_assumed": n_trials, "sharpe_anual_dev": float(sr * np.sqrt(365)),
       "t_stat_exceso_vs_btc_igual_vol": float(t_stat), "distribucion": dist}
(ROOT / "results/atlas_v3").mkdir(parents=True, exist_ok=True)
json.dump(out, open(ROOT / "results/atlas_v3/robustness.json", "w"), indent=2)
pd.set_option("display.width", 200)
f = rob.copy()
for c in ["cagr", "max_dd", "avg_month"]:
    f[c] = (f[c] * 100).round(1)
print(f.round(2).to_string())
print(json.dumps({k: v for k, v in out.items() if k != "robustez"}, indent=1))
