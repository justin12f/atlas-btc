"""
Investigacion por ablacion SOLO en el periodo de desarrollo (2018-01-01 -> 2025-10-22).
El holdout (ultimos 12 meses) no se toca aqui.

Responde: que capa aporta valor de verdad, fuera de muestra?
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.features import build_feature_matrix  # noqa: E402
from engine.metrics import perf_stats  # noqa: E402
from engine.ml import MLConfig, oos_quality, walk_forward  # noqa: E402
from engine.portfolio import PortfolioConfig, simulate, target_exposure  # noqa: E402
from engine.signals import trend_score  # noqa: E402

DEV_END = "2025-10-22"
RES = ROOT / "results"
d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet")
mac = pd.read_parquet(ROOT / "data/processed/macro_etfs.parquet")
X = build_feature_matrix(d, mac)
no_cycle = [c for c in X.columns if not c.startswith("halving")]
T = trend_score(d)

ml_runs = {
    "dir28_all": MLConfig(horizon=28, automl=False),
    "dir28_nocycle": MLConfig(horizon=28, automl=False, features=no_cycle),
    "crash14_all": MLConfig(horizon=14, target="crash", automl=False),
    "crash14_nocycle": MLConfig(horizon=14, target="crash", automl=False, features=no_cycle),
}
preds, quality = {}, {}
for name, cfg in ml_runs.items():
    p, logs, y = walk_forward(d, X, cfg, end=DEV_END, verbose=False)
    preds[name] = p
    quality[name] = oos_quality(p, y)
    p.to_parquet(RES / f"abl_pred_{name}.parquet")
    print(name, quality[name], flush=True)

regime = preds["dir28_all"][["p_bear", "p_side", "p_bull"]]
base = dict(target_vol=0.40, dd_trigger=9.0, regime_penalty=0.0)
variants = {
    "A. Tendencia + vol-target (nucleo)": (dict(), None, None),
    "B. A + regimen HMM": (dict(regime_penalty=0.5), None, None),
    "C. A + ciclo 4 anios (regla)": (dict(cycle_rule=True), None, None),
    "D. A + ML direccion 28d (con ciclo)": (dict(), "dir28_all", None),
    "E. A + ML direccion 28d (sin ciclo)": (dict(), "dir28_nocycle", None),
    "F. A + ML riesgo crash 14d (con ciclo)": (dict(), None, "crash14_all"),
    "G. A + ML riesgo crash 14d (sin ciclo)": (dict(), None, "crash14_nocycle"),
    "H. D + regimen HMM": (dict(regime_penalty=0.5), "dir28_all", None),
    "I. D + ML riesgo crash (con ciclo)": (dict(), "dir28_all", "crash14_all"),
    "J. A + freno por drawdown": (dict(dd_trigger=0.20), None, None),
}
rows = []
bh = d["close"].pct_change().loc["2018-01-02":DEV_END]
s = perf_stats(bh)
rows.append({"variante": "BTC buy & hold", **s})
for name, (over, mlk, crk) in variants.items():
    cfg = PortfolioConfig(**{**base, **over})
    tx = target_exposure(d, T, preds.get(mlk), cfg, crash=preds.get(crk), regime=regime)
    sim = simulate(d, tx["target_raw"], cfg, "2018-01-01", DEV_END)
    s = perf_stats(sim["ret"])
    s["turnover_x_anio"] = sim["trade"].abs().sum() / (len(sim) / 365)
    s["exposicion_media"] = sim["weight"].mean()
    rows.append({"variante": name, **s})
tab = pd.DataFrame(rows).set_index("variante")
tab.to_csv(RES / "ablation_dev.csv")
json.dump(quality, open(RES / "ablation_ml_quality.json", "w"), indent=2)
pd.set_option("display.width", 220)
fmt = tab[["cagr", "vol", "sharpe", "sortino", "max_dd", "calmar", "avg_month", "pct_pos_months",
           "turnover_x_anio", "exposicion_media"]].copy()
for c in ["cagr", "vol", "max_dd", "avg_month", "pct_pos_months", "exposicion_media"]:
    fmt[c] = (fmt[c] * 100).round(1)
print(fmt.round(2).to_string())
