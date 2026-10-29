"""
Backtest de la version de Atlas definida en config/strategy.yaml (hoy: v3) contra Atlas v2 y benchmarks.

  python scripts/run_backtest.py            # usa predicciones ML en cache (solo para comparar con v2)
  python scripts/run_backtest.py --refit    # re-entrena el walk-forward de ML de v2
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.benchmarks import equity_benchmarks, momentum_top10  # noqa: E402
from engine.features import build_feature_matrix  # noqa: E402
from engine.metrics import monthly_returns, perf_stats  # noqa: E402
from engine.ml import walk_forward  # noqa: E402
from engine.portfolio import simulate, target_exposure  # noqa: E402
from engine.signals import trend_score  # noqa: E402
from engine.strategy import build_targets, load_config, ml_config, portfolio_config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--refit", action="store_true")
args = ap.parse_args()

C = load_config()
C2 = load_config(ROOT / "config" / "strategy_v2.yaml")
P = C["periods"]
NAME = f"Atlas v{C.get('version', '3.0').split('.')[0]}"
RES = ROOT / "results" / "atlas_v3"
RES.mkdir(parents=True, exist_ok=True)
d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").loc[:P["holdout_end"]]

# ------------------------------------------------------------------ ML (solo para la comparacion con v2)
pred_path = ROOT / "results" / "ml_pred_final.parquet"
if args.refit or not pred_path.exists():
    mac = pd.read_parquet(ROOT / "data/processed/macro_etfs.parquet")
    pred, logs, y = walk_forward(d, build_feature_matrix(d, mac), ml_config(C2), end=P["holdout_end"])
    pred.to_parquet(pred_path)
pred = pd.read_parquet(pred_path)

# ------------------------------------------------------------------ estrategias
pcfg = portfolio_config(C)
tg = build_targets(d, pred, C, pcfg)                     # v3: ML apagado en la config
tg2 = build_targets(d, pred, C2, portfolio_config(C2))   # v2 tal como estaba
T = trend_score(d)
strategies = {
    NAME: (tg["target"], pcfg),
    "Atlas v2 (con ML)": (tg2["target"], portfolio_config(C2)),
    f"{NAME} + USDC 4%": (tg["target"], portfolio_config(C, usdc_apy=C["execution"]["usdc_apy_sensitivity"])),
    "Nucleo sin ciclo": (target_exposure(d, T, None, portfolio_config(C, cycle_rule=False))["target_raw"],
                         portfolio_config(C, cycle_rule=False)),
}
sims = {k: simulate(d, t, cfg, P["dev_start"], P["holdout_end"]) for k, (t, cfg) in strategies.items()}
daily = {k: s["ret"] for k, s in sims.items()}
daily["BTC buy & hold"] = d["close"].pct_change().loc[P["dev_start"]:P["holdout_end"]].iloc[1:]

eq = equity_benchmarks(start="2016-01-01")
mom, _ = momentum_top10(start="2016-06-30")
eq["Momentum S&P top10"] = mom
eq_names = {"VOO": "VOO (Vanguard S&P 500)", "QQQ": "QQQ (Nasdaq-100)", "Mag7": "Magnificent 7",
            "IWF": "IWF (Russell 1000 Growth)", "Momentum S&P top10": "Momentum S&P 500 top 10"}
for k, nm in eq_names.items():
    daily[nm] = eq[k].loc[P["dev_start"]:P["holdout_end"]].dropna()

# ------------------------------------------------------------------ metricas
periods = {"dev": (P["dev_start"], P["dev_end"]), "holdout": (P["holdout_start"], P["holdout_end"]),
           "full": (P["dev_start"], P["holdout_end"])}
stats = {p: {k: perf_stats(r.loc[a:b], periods=252 if k in eq_names.values() else 365) for k, r in daily.items()}
         for p, (a, b) in periods.items()}
monthly = pd.DataFrame({k: monthly_returns(r) for k, r in daily.items()})
monthly.index = monthly.index.astype(str)
monthly.to_csv(RES / "monthly_returns.csv")
yearly = pd.DataFrame({k: (1 + r).groupby(r.index.year).prod() - 1 for k, r in daily.items()})
yearly.to_csv(RES / "yearly_returns.csv")
curves = pd.DataFrame({k: (1 + r).cumprod().resample("W").last() for k, r in daily.items()}).ffill()
curves.to_csv(RES / "equity_curves_weekly.csv")

fin = sims[NAME]
roll12 = (1 + fin["ret"]).rolling(365).apply(np.prod, raw=True) - 1
m = monthly[NAME].dropna()
out = {
    "version": C.get("version"),
    "stats": stats,
    "kill_switch_events": int(fin.attrs.get("kill_events", 0)),
    "trades": {"n_trades": int((fin["trade"].abs() > 0).sum()),
               "turnover_per_year": float(fin["trade"].abs().sum() / (len(fin) / 365)),
               "avg_exposure": float(fin["weight"].mean())},
    "monthly": {"compound_full": float((1 + m).prod() ** (1 / len(m)) - 1),
                "p5": float(m.quantile(0.05)), "p50": float(m.median()), "p95": float(m.quantile(0.95)),
                "pct_positive": float((m > 0).mean())},
    "rolling_12m": {"pct_negative": float((roll12.dropna() < 0).mean()), "worst": float(roll12.min()),
                    "best": float(roll12.max())},
    "current_target": {"date": str(tg["target"].dropna().index[-1].date()), "target": float(tg["target"].dropna().iloc[-1])},
}
json.dump(out, open(RES / "summary.json", "w"), indent=2, default=lambda o: None if (isinstance(o, float) and np.isnan(o)) else str(o))

pd.set_option("display.width", 250)
for pname in periods:
    t = pd.DataFrame(stats[pname]).T[["cagr", "total_return", "vol", "sharpe", "max_dd", "calmar", "avg_month",
                                       "median_month", "pct_pos_months", "worst_month", "best_month"]]
    for c in t.columns:
        if c not in ("sharpe", "calmar"):
            t[c] = (t[c].astype(float) * 100).round(1)
    print(f"\n===== {pname.upper()} {periods[pname]} =====")
    print(t.astype(float).round(2).to_string())
print(json.dumps({k: v for k, v in out.items() if k != "stats"}, indent=1))
