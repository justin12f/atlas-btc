"""
Evalúa las ideas de config/preregistro_ideas_2026-11.yaml contra Atlas v3. Solo investigación:
no toca engine/ ni config/strategy.yaml (las variantes se arman en memoria).

  python scripts/research_ideas_2026_11.py > results/ideas_2026_11.json
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.portfolio import should_trade, simulate  # noqa: E402
from engine.strategy import build_targets, load_config, portfolio_config  # noqa: E402

PR = __import__("yaml").safe_load(open(ROOT / "config/preregistro_ideas_2026-11.yaml"))
DEV, HOLD = PR["periodos"]["desarrollo"], PR["periodos"]["holdout"]
STARTS = PR["periodos"]["inicios_robustez"]
END = HOLD[1]


def simulate_intraday_exit(d: pd.DataFrame, target: pd.Series, cfg, start: str, end: str,
                           thr: float | None, extra_slip: float = 0.005) -> pd.DataFrame:
    """Copia de engine.portfolio.simulate (sin freno de caída, que v3 tiene apagado) + salida intradía I3.
    Con thr=None debe dar exactamente lo mismo que simulate (se verifica abajo)."""
    close, low = d["close"], d["low"]
    r = close.pct_change()
    tgt = target.loc[start:end].dropna()
    dates = tgt.index
    cash_r = (1 + cfg.usdc_apy) ** (1 / 365) - 1
    cost_rate = cfg.fee + cfg.slippage
    eq, peak, w = 1.0, 1.0, 0.0
    paused_until = -1
    rows = []
    for i in range(len(dates) - 1):
        t, t1 = dates[i], dates[i + 1]
        dd = eq / peak - 1
        if i == paused_until:
            peak = eq
            dd = 0.0
        if dd < -cfg.kill_dd and i > paused_until:
            paused_until = i + cfg.kill_review_days
        e = tgt.iat[i]
        if i < paused_until:
            e = 0.0
        trade = 0.0
        if should_trade(e, w, cfg.band):
            trade = e - w
            w = e
        cost = abs(trade) * cost_rate
        rb = r.loc[t1]
        stop = thr is not None and w > 0 and low.loc[t1] <= close.loc[t] * (1 - thr)
        if stop:
            rs = -thr                                         # vende a cierre_t × (1 − thr)
            port_r = w * rs + (1 - w) * cash_r - cost - w * (cost_rate + extra_slip)
            w_end = 0.0
        else:
            port_r = w * rb + (1 - w) * cash_r - cost
            w_end = w * (1 + rb) / (1 + w * rb + (1 - w) * cash_r) if (1 + w * rb) != 0 else w
        eq *= 1 + port_r
        peak = max(peak, eq)
        rows.append((t1, w, trade, cost, port_r, eq, bool(stop)))
        w = w_end
    return pd.DataFrame(rows, columns=["date", "weight", "trade", "cost", "ret", "equity", "stop"]).set_index("date")


def cagr(r: pd.Series) -> float:
    return float((1 + r).prod() ** (365 / len(r)) - 1)


def mdd(r: pd.Series) -> float:
    e = (1 + r).cumprod()
    return float((e / e.cummax() - 1).min())


def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std() * np.sqrt(365))


def boot_cagr_diff(a: pd.Series, b: pd.Series, B: int = 2000, block: int = 20, seed: int = 7) -> tuple[float, float]:
    """IC95% de CAGR(a) − CAGR(b) con bootstrap estacionario (Politis-Romano) sobre pares de días."""
    x, y = np.log1p(a.to_numpy()), np.log1p(b.to_numpy())
    n, p = len(x), 1 / block
    rng = np.random.default_rng(seed)
    out = np.empty(B)
    for k in range(B):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        jumps = rng.random(n) < p
        news = rng.integers(n, size=n)
        for j in range(1, n):
            idx[j] = news[j] if jumps[j] else (idx[j - 1] + 1) % n
        out[k] = np.exp(x[idx].mean() * 365) - np.exp(y[idx].mean() * 365)
    return float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975))


def run(C: dict, d: pd.DataFrame, cost_mult: float = 1.0, floor: float | None = None, thr: float | None = None,
        apy: float = 0.0) -> pd.Series:
    pc = portfolio_config(C, usdc_apy=apy)
    pc.fee, pc.slippage = pc.fee * cost_mult, pc.slippage * cost_mult
    tg = build_targets(d, None, C, pc)["target"]
    if floor is not None:
        tg = tg.clip(lower=floor)
    if thr is not None:
        return simulate_intraday_exit(d, tg, pc, DEV[0], END, thr)["ret"]
    return simulate(d, tg, pc, DEV[0], END)["ret"]


def main() -> dict:
    C0 = load_config()
    d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").loc[:END]
    # verificación: la copia del simulador sin regla intradía reproduce simulate exactamente
    pc = portfolio_config(C0)
    tg = build_targets(d, None, C0, pc)["target"]
    a, b = simulate(d, tg, pc, DEV[0], END)["ret"], simulate_intraday_exit(d, tg, pc, DEV[0], END, None)["ret"]
    assert np.allclose(a.to_numpy(), b.to_numpy(), atol=1e-15, rtol=0), "la copia del simulador no coincide"

    C50 = copy.deepcopy(C0)
    C50["risk"]["target_vol"] = 0.50
    variants = {
        "base": dict(C=C0),
        "I1_vol50": dict(C=C50),
        "I2_piso30": dict(C=C0, floor=0.30),
        "I3_salida_intradia": dict(C=C0, thr=PR["ideas"]["I3_salida_intradia"]["umbral"]),
    }
    R = {k: run(d=d, **v) for k, v in variants.items()}
    R25 = {k: run(d=d, cost_mult=2.5, **v) for k, v in variants.items()}
    R4 = {k: run(d=d, apy=0.04, **v) for k, v in variants.items()}
    base = R["base"]
    out = {"verificacion_simulador": "ok", "ideas": {}}
    for k, r in R.items():
        dev, hold = r.loc[DEV[0]:DEV[1]], r.loc[HOLD[0]:HOLD[1]]
        row = {"cagr_dev": cagr(dev), "mdd_dev": mdd(dev), "sharpe_dev": sharpe(dev),
               "ret_holdout": float((1 + hold).prod() - 1), "mdd_holdout": mdd(hold),
               "cagr_completo": cagr(r), "mdd_completo": mdd(r),
               "cagr_completo_usdc4": cagr(R4[k]), "cagr_dev_costos_x2.5": cagr(R25[k].loc[DEV[0]:DEV[1]]),
               "cagr_por_inicio": {s: cagr(r.loc[s:]) for s in STARTS}}
        if k == "I3_salida_intradia":
            row["dias_con_salida"] = None
        if k != "base":
            bd = base.loc[DEV[0]:DEV[1]]
            lo, hi = boot_cagr_diff(dev, bd)
            b_hold = float((1 + base.loc[HOLD[0]:HOLD[1]]).prod() - 1)
            wins = sum(cagr(r.loc[s:]) > cagr(base.loc[s:]) for s in STARTS)
            d25 = cagr(R25[k].loc[DEV[0]:DEV[1]]) - cagr(R25["base"].loc[DEV[0]:DEV[1]])
            crit = {"C1": lo > 0, "C2": row["mdd_dev"] >= -0.30, "C3": row["ret_holdout"] >= b_hold - 0.02,
                    "C4": wins >= 4, "C5": d25 > 0}
            row.update({"dif_cagr_dev": cagr(dev) - cagr(bd), "ic95_dif_cagr_dev": [lo, hi],
                        "dif_holdout": row["ret_holdout"] - b_hold, "inicios_ganados": f"{wins}/6",
                        "dif_cagr_dev_costos_x2.5": d25, "criterios": crit, "pasa": all(crit.values())})
        out["ideas"][k] = row
    pc = portfolio_config(C0)
    st = simulate_intraday_exit(d, tg, pc, DEV[0], END, PR["ideas"]["I3_salida_intradia"]["umbral"])
    out["ideas"]["I3_salida_intradia"]["dias_con_salida"] = int(st["stop"].sum())
    return out


if __name__ == "__main__":
    json.dump(main(), sys.stdout, indent=1, default=lambda o: bool(o) if isinstance(o, np.bool_) else str(o))
    print()
