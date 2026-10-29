"""
Combinacion de capas -> exposicion objetivo en BTC (0 = todo en USDC, 1 = todo en BTC).
Simulador de ejecucion diario con costos, banda de no-operacion y freno por drawdown.
Spot puro: sin apalancamiento ni cortos.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PortfolioConfig:
    w_trend: float = 0.5           # peso de la capa de tendencia
    w_ml: float = 0.5              # peso de la capa ML (si su compuerta esta encendida)
    ml_slope: float = 4.0          # p=0.625 -> 1.0 ; p=0.375 -> 0.0
    regime_penalty: float = 0.5    # exposicion *= 1 - penalty * P(regimen bajista)
    cycle_rule: bool = False       # capa de ciclo de 4 anios explicita (hipotesis, ver README)
    cycle_bear_window: tuple = (540, 900)   # dias desde el halving con sesgo bajista historico
    cycle_bear_mult: float = 0.5
    crash_lo: float = 0.30         # capa ML de riesgo: P(crash) <= lo -> sin recorte
    crash_hi: float = 0.60         #   P(crash) >= hi -> recorte maximo
    crash_max_cut: float = 0.5
    target_vol: float = 0.60       # volatilidad anual objetivo
    vol_halflife: int = 10
    dd_trigger: float = 0.20       # freno: si DD de la estrategia > 20% -> exposicion * dd_mult
    dd_release: float = 0.10       #   se libera cuando el DD vuelve a < 10%
    dd_mult: float = 0.5
    band: float = 0.10             # no operar si el cambio de exposicion es < 10% del capital
    fee: float = 0.0010            # comision Binance spot (taker, sin BNB)
    slippage: float = 0.0003
    usdc_apy: float = 0.0          # rendimiento del USDC ocioso (0 = conservador)
    kill_dd: float = 9.0           # interruptor: si la caida supera este nivel, todo a USDC
    kill_review_days: int = 30     #   y se reanuda despues de esta revision (en el backtest)


def vol_forecast(d: pd.DataFrame, halflife: int) -> pd.Series:
    rv = d["rv"].clip(lower=1e-10)
    ew = rv.ewm(halflife=halflife, min_periods=10).mean()
    return np.sqrt(0.5 * ew + 0.5 * rv.rolling(30, min_periods=10).mean()) * np.sqrt(365)


def cycle_multiplier(idx: pd.DatetimeIndex, cfg: PortfolioConfig) -> pd.Series:
    from .features import cycle_features, CYCLE_DAYS
    days = cycle_features(idx)["halving_phase"] * CYCLE_DAYS
    lo, hi = cfg.cycle_bear_window
    return pd.Series(np.where((days >= lo) & (days < hi), cfg.cycle_bear_mult, 1.0), index=idx)


def target_exposure(d: pd.DataFrame, trend: pd.Series, ml: pd.DataFrame | None,
                    cfg: PortfolioConfig, crash: pd.DataFrame | None = None,
                    regime: pd.DataFrame | None = None) -> pd.DataFrame:
    """Exposicion = [tendencia (+ ML direccion)] x regimen x ciclo x riesgo-ML x vol-target."""
    idx = trend.dropna().index
    T = trend.reindex(idx)
    S = pd.Series(np.nan, index=idx)
    score = T.copy()
    if ml is not None:
        ml = ml.reindex(idx)
        gate = ml["gate"].fillna(0.0)
        S = (0.5 + (ml["p_ml"].fillna(0.5) - 0.5) * cfg.ml_slope).clip(0, 1)
        score = pd.Series(np.where(gate > 0, cfg.w_trend * T + cfg.w_ml * S, T), index=idx)
    p_bear = 0.0
    src = regime if regime is not None else ml
    if src is not None and "p_bear" in src:
        p_bear = src["p_bear"].reindex(idx).fillna(0.0)
    regime_mult = 1 - cfg.regime_penalty * p_bear
    cyc = cycle_multiplier(idx, cfg) if cfg.cycle_rule else pd.Series(1.0, index=idx)
    crash_mult = pd.Series(1.0, index=idx)
    if crash is not None:
        c = crash.reindex(idx)
        pc = c["p_ml"].where(c["gate"] > 0)
        cut = ((pc - cfg.crash_lo) / (cfg.crash_hi - cfg.crash_lo)).clip(0, 1) * cfg.crash_max_cut
        crash_mult = (1 - cut).fillna(1.0)
    vf = vol_forecast(d, cfg.vol_halflife).reindex(idx)
    vol_scaler = (cfg.target_vol / vf).clip(upper=1.0)
    raw = (score * regime_mult * cyc * crash_mult * vol_scaler).clip(0, 1)
    return pd.DataFrame({"trend": T, "ml_score": S, "score": score, "regime_mult": regime_mult,
                         "cycle_mult": cyc, "crash_mult": crash_mult, "vol_fc": vf,
                         "vol_scaler": vol_scaler, "target_raw": raw})


def ml_promotion(d: pd.DataFrame, core_target: pd.Series, ml_target: pd.Series, cfg: PortfolioConfig,
                 start: str, window: int = 365, threshold: float = 0.02, ml_weight: float = 1.0) -> pd.DataFrame:
    """ML en MODO SOMBRA: se corren en papel la estrategia nucleo y la nucleo+ML.
    A fin de cada mes se mide la mejora de la version con ML en los ultimos `window` dias
    (retorno extra / volatilidad del nucleo). Si supera `threshold`, el ML se PROMUEVE el mes
    siguiente; si no, queda en la banca. Todo con informacion pasada (decision mensual con rezago)."""
    s_core = simulate(d, core_target, cfg, start)["ret"]
    s_ml = simulate(d, ml_target, cfg, start)["ret"]
    edge = (s_ml - s_core).rolling(window, min_periods=window // 2).mean() / \
        s_core.rolling(window, min_periods=window // 2).std()
    monthly = edge.resample("ME").last()
    promoted = (monthly > threshold).astype(float) * ml_weight
    w = promoted.reindex(pd.date_range(monthly.index.min(), core_target.index.max()), method="ffill")
    w = w.shift(1).reindex(core_target.index).fillna(0.0)   # efectiva desde el dia siguiente a fin de mes
    mix = core_target * (1 - w) + ml_target.reindex(core_target.index).fillna(core_target) * w
    return pd.DataFrame({"ml_weight": w, "target": mix, "edge": edge.reindex(core_target.index)})


def should_trade(e: float, w: float, band: float) -> bool:
    """Regla de banda: operar si el cambio de exposicion supera la banda, o salida total
    (objetivo < 2%), o entrada total (objetivo > 98%). La usan simulate y atlas (planificador)."""
    return abs(e - w) >= band or (e < 0.02 and w >= 0.02) or (e > 0.98 and w <= 0.98 and abs(e - w) > 0.02)


def simulate(d: pd.DataFrame, target: pd.Series, cfg: PortfolioConfig,
             start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """La exposicion decidida al cierre de t se mantiene durante el dia t+1."""
    close = d["close"]
    r = close.pct_change()
    tgt = target.loc[start:end].dropna()
    dates = tgt.index
    cash_r = (1 + cfg.usdc_apy) ** (1 / 365) - 1
    cost_rate = cfg.fee + cfg.slippage

    eq, peak, w, braked = 1.0, 1.0, 0.0, False
    paused_until, kill_events = -1, 0
    rows = []
    for i in range(len(dates) - 1):
        t, t1 = dates[i], dates[i + 1]
        # --- decision al cierre de t
        dd = eq / peak - 1
        if braked and dd > -cfg.dd_release:
            braked = False
        if not braked and dd < -cfg.dd_trigger:
            braked = True
        if i == paused_until:                      # fin de la revision: se reanuda con nuevo maximo
            peak = eq
            dd = 0.0
        if dd < -cfg.kill_dd and i > paused_until:
            paused_until = i + cfg.kill_review_days
            kill_events += 1
        e = tgt.iat[i] * (cfg.dd_mult if braked else 1.0)
        if i < paused_until:
            e = 0.0
        trade = 0.0
        if should_trade(e, w, cfg.band):
            trade = e - w
            w = e
        cost = abs(trade) * cost_rate
        # --- resultado del dia t+1
        rb = r.loc[t1]
        port_r = w * rb + (1 - w) * cash_r - cost
        eq *= 1 + port_r
        peak = max(peak, eq)
        rows.append((t1, w, trade, cost, port_r, eq, braked, i < paused_until))
        # deriva del peso por el movimiento del precio
        w = w * (1 + rb) / (1 + w * rb + (1 - w) * cash_r) if (1 + w * rb) != 0 else w
    out = pd.DataFrame(rows, columns=["date", "weight", "trade", "cost", "ret", "equity", "dd_brake", "paused"]).set_index("date")
    out.attrs["kill_events"] = kill_events
    return out
