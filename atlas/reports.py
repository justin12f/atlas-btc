"""
Reportes (sección F6): diario y semanal en markdown + CSV, en `reports/` bajo ATLAS_DATA_DIR.

Semanal: equity por modo, operaciones, costo por orden vs 0.13% del backtest, días a tiempo (runs),
eventos de modo seguro y paridad de datos (cierre HL spot vs perp).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from atlas.data.store import SQLiteStore

BACKTEST_COST = 0.0013


def orders_frame(store: SQLiteStore, mode: str) -> pd.DataFrame:
    rows = store.orders(mode)
    if not rows:
        return pd.DataFrame(columns=["date", "side", "sz", "status", "cost", "hyp_taker_cost"])
    df = pd.DataFrame(rows)
    meta = df["meta_json"].apply(json.loads)
    df["cost"] = meta.apply(lambda m: m.get("cost"))
    df["hyp_taker_cost"] = meta.apply(lambda m: m.get("hyp_taker_cost"))
    df["vwap"] = meta.apply(lambda m: m.get("vwap"))
    return df[["date", "cloid", "side", "sz", "status", "vwap", "cost", "hyp_taker_cost"]]


def daily_report(store: SQLiteStore, mode: str, date: str) -> str:
    led = store.ledger_row(mode, date)
    sig = store.signal(date, mode)
    out = [f"# Atlas {mode} — {date}", ""]
    if sig:
        out += [f"- Objetivo: **{sig['target']:.4f}** (tendencia {sig['trend']:.3f}, ciclo ×{sig['cycle_mult']}, "
                f"vol pronosticada {sig['vol_fc']:.3f}, escala {sig['vol_scaler']:.3f})",
                f"- Cierre: {sig['close']:,.2f} · config {sig['config_hash'][:12]} · código {sig['code_version']}"]
    else:
        out.append("- Sin señal (modo seguro o datos faltantes)")
    if led:
        out += [f"- Equity: {led['equity']:,.2f} USDC · peso BTC {led['weight']:.4f} · caída {led['drawdown']:.2%}"
                f" · pausa {'sí' if led['paused'] else 'no'}",
                f"- Saldos: {led['usdc']:,.2f} USDC + {led['ubtc']:.5f} UBTC @ {led['mark_px']:,.2f}"]
    od = orders_frame(store, mode)
    od = od[od["date"] == date]
    for _, o in od.iterrows():
        cost = "—" if pd.isna(o["cost"]) else f"{o['cost']:.4%}"
        out.append(f"- Orden {o['side']} {o['sz']} → {o['status']}, VWAP {o['vwap']}, costo {cost} (backtest 0.13%)")
    return "\n".join(out) + "\n"


def weekly_report(store: SQLiteStore, end_date: str, modes: tuple[str, ...] = ("paper", "testnet")) -> tuple[str, pd.DataFrame]:
    end = pd.Timestamp(end_date)
    start = end - pd.Timedelta(days=6)
    lines = [f"# Atlas — semana {start.date()} → {end.date()}", ""]
    frames = []
    for mode in modes:
        led = store.ledger_df(mode)
        if led.empty:
            continue
        led["date"] = pd.to_datetime(led["date"])
        wk = led[(led["date"] >= start) & (led["date"] <= end)]
        if wk.empty:
            continue
        frames.append(wk.assign(mode=mode))
        od = orders_frame(store, mode)
        od = od[(pd.to_datetime(od["date"]) >= start) & (pd.to_datetime(od["date"]) <= end)] if len(od) else od
        costs = od["cost"].dropna().astype(float) if len(od) else pd.Series(dtype=float)
        lines += [f"## {mode}",
                  f"- Equity: {wk['equity'].iloc[0]:,.2f} → {wk['equity'].iloc[-1]:,.2f} "
                  f"({wk['equity'].iloc[-1] / wk['equity'].iloc[0] - 1:+.2%}); caída máx. {wk['drawdown'].min():.2%}",
                  f"- Días con ledger: {len(wk)}/7 · operaciones: {len(od)}",
                  f"- Costo por orden: mediana {costs.median():.4%} · p90 {costs.quantile(0.9):.4%} (backtest 0.13%)"
                  if len(costs) else "- Costo por orden: sin operaciones", ""]
    runs = pd.read_sql("SELECT * FROM runs WHERE started_at >= ?", store.con, params=(str(start.date()),))
    ok = runs[runs["status"].isin(["done", "already_done", "safe_mode", "killed"])]
    lines.append(f"- Corridas: {len(runs)} (terminadas {len(ok)}, con error {int((runs['status'] == 'error').sum())})")
    ev = pd.read_sql("SELECT kind, COUNT(*) n FROM events WHERE ts >= ? GROUP BY kind", store.con,
                     params=(str(start.date()),))
    if len(ev):
        lines.append("- Eventos: " + ", ".join(f"{k} {n}" for k, n in ev.itertuples(index=False)))
    data = pd.concat(frames) if frames else pd.DataFrame()
    return "\n".join(lines) + "\n", data


def write_report(base: Path, name: str, text: str, data: pd.DataFrame | None = None) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    p = base / f"{name}.md"
    p.write_text(text)
    if data is not None and len(data):
        data.to_csv(base / f"{name}.csv", index=False)
    return p
