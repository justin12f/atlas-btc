"""
Modo papel (paper trading) para Binance BTC/USDC. NO envia ordenes reales.

Flujo diario (correr poco despues de las 00:00 UTC = 18:00 hora del centro de Mexico):
  1. Descarga velas de 5 minutos de BTCUSDC de la API publica de Binance (sin llaves).
  2. Construye barras diarias + volatilidad realizada (mismo calculo que el backtest).
  3. Calcula la exposicion objetivo del nucleo (tendencia x ciclo x vol-target).
  4. Rebalancea un portafolio de papel si el cambio supera la banda, cobrando comision.
  5. Guarda el estado y un registro en data/live/.

La capa ML sigue en modo sombra: su promocion se re-evalua cada mes corriendo
scripts/run_backtest.py --refit con datos actualizados.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .portfolio import target_exposure
from .signals import trend_score
from .strategy import load_config, portfolio_config

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live"
API = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str = "BTCUSDC", interval: str = "5m", days: int = 400) -> pd.DataFrame:
    import requests
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    out = []
    while start < end:
        r = requests.get(API, params={"symbol": symbol, "interval": interval, "startTime": start, "limit": 1000},
                         timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        start = batch[-1][0] + 1
        time.sleep(0.15)  # respetar limites de la API
    df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close", "volume", "close_ts", "qv", "n",
                                    "tb", "tq", "ignore"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype(float)


def daily_from_5m(k5: pd.DataFrame) -> pd.DataFrame:
    day = k5.index.floor("D")
    g = k5.groupby(day)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum()})
    r5 = np.log(k5["close"]).diff()
    d["rv"] = (r5 ** 2).groupby(day).sum()
    d["rv_down"] = (r5.clip(upper=0) ** 2).groupby(day).sum()
    # descartar el dia en curso (incompleto)
    if k5.index.max() < d.index.max() + pd.Timedelta(hours=23, minutes=55):
        d = d.iloc[:-1]
    d.index.name = "date"
    return d


def today_target(d: pd.DataFrame) -> dict:
    C = load_config()
    cfg = portfolio_config(C)
    tx = target_exposure(d, trend_score(d), None, cfg)
    tx = tx.dropna(subset=["target_raw"])
    last = tx.iloc[-1]
    return {"date": str(tx.index[-1].date()), "target": float(last["target_raw"]),
            "trend": float(last["trend"]), "cycle_mult": float(last["cycle_mult"]),
            "vol_forecast": float(last["vol_fc"]), "vol_scaler": float(last["vol_scaler"])}


def resume(note: str) -> None:
    """Reanuda despues de una pausa del interruptor. Exige una nota escrita (proceso, no impulso)."""
    state_path = LIVE / "paper_state.json"
    st = json.load(open(state_path))
    if not st.get("paused"):
        print("No hay pausa activa.")
        return
    st["paused"] = False
    st["peak_equity"] = None          # el maximo se reinicia al reanudar
    st.setdefault("resume_notes", []).append({"date": pd.Timestamp.utcnow().isoformat(), "note": note})
    json.dump(st, open(state_path, "w"), indent=2)
    print("Reanudado:", note)


def paper_step(d: pd.DataFrame, initial_usdc: float = 10_000.0) -> dict:
    LIVE.mkdir(parents=True, exist_ok=True)
    C = load_config()
    band, fee = C["risk"]["band"], C["execution"]["fee"] + C["execution"]["slippage"]
    kill_dd = C["risk"].get("kill_switch_dd", 9.0)
    state_path = LIVE / "paper_state.json"
    st = json.load(open(state_path)) if state_path.exists() else {"usdc": initial_usdc, "btc": 0.0, "last_date": None}
    sig = today_target(d)
    if st["last_date"] == sig["date"]:
        return {**sig, "note": "ya procesado hoy", **st}
    price = float(d["close"].iloc[-1])
    equity = st["usdc"] + st["btc"] * price
    w = st["btc"] * price / equity if equity > 0 else 0.0
    # --- interruptor de caida: si la caida desde el maximo supera kill_dd -> todo a USDC y pausa
    peak = max(st.get("peak_equity") or equity, equity)
    st["peak_equity"] = peak
    dd = equity / peak - 1
    if dd < -kill_dd and not st.get("paused"):
        st["paused"] = True
        st["paused_since"] = sig["date"]
    e = 0.0 if st.get("paused") else sig["target"]
    traded = 0.0
    if abs(e - w) >= band or (e < 0.02 and w >= 0.02):
        delta_usd = (e - w) * equity
        cost = abs(delta_usd) * fee
        st["btc"] += delta_usd / price
        st["usdc"] -= delta_usd + cost
        traded = delta_usd
    st["last_date"] = sig["date"]
    json.dump(st, open(state_path, "w"), indent=2)
    row = {**sig, "price": price, "equity": st["usdc"] + st["btc"] * price, "weight_before": w,
           "traded_usd": traded, "btc": st["btc"], "usdc": st["usdc"], "drawdown": dd,
           "paused": bool(st.get("paused", False))}
    log = LIVE / "paper_log.csv"
    pd.DataFrame([row]).to_csv(log, mode="a", header=not log.exists(), index=False)
    return row
