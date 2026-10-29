"""
Réplica (sección 13): recorre el pipeline día por día con datos truncados y lo compara con
engine.portfolio.simulate sobre la misma serie.

Mismo código que en vivo para señal, planificador y ledger; el broker es FakeBroker (llena al cierre con el
costo del backtest). El interruptor se reanuda solo tras `kill_switch_review_days`, como en el backtest.

Diferencias esperadas (no son errores): el ledger lleva unidades (UBTC/USDC) con tamaños redondeados a
szDecimals y la comisión en el activo recibido; simulate trabaja en pesos y descuenta el costo del retorno.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from atlas.execution.brokers import FakeBroker
from atlas.execution.ledger import apply_fill, initial_state, pause, resume, roll_peak
from atlas.execution.planner import plan_order
from atlas.hl.markets import cloid_for
from atlas.interfaces import SpotMarket
from atlas.signal import compute_signal, compute_targets
from engine.portfolio import simulate
from engine.strategy import portfolio_config

REPLAY_MARKET = SpotMarket(pair="UBTC/USDC", coin="@replay", index=-1, asset_id=-1, base="UBTC", quote="USDC",
                           sz_decimals=5, base_token_index=-1)


@dataclass
class ReplayResult:
    ledger: pd.DataFrame       # por día de decisión t: target, w_antes, operación, saldos, equity al cierre de t+1
    sim: pd.DataFrame          # engine.portfolio.simulate
    report: dict


def run_replay(d: pd.DataFrame, start: str, end: str, C: dict, initial_usdc: float = 10_000.0,
               truncated: bool = True, market: SpotMarket = REPLAY_MARKET) -> ReplayResult:
    pcfg = portfolio_config(C)
    full = compute_targets(d, C)
    dates = full["target"].loc[start:end].dropna().index
    broker = FakeBroker(pcfg.fee + pcfg.slippage)
    s = initial_state("fake", initial_usdc)
    paused_until = -1
    rows = []
    for i, t in enumerate(dates[:-1]):
        px = float(d.loc[t, "close"])
        target = compute_signal(d, t, "replay", "replay", C).target if truncated else float(full.loc[t, "target"])
        # --- interruptor (caída al cierre de t), con reanudación automática como en el backtest
        if i == paused_until:
            s = resume(s, px)
        eq = s.equity(px)
        if not s.paused and eq / max(s.peak, eq) - 1 < -pcfg.kill_dd and i > paused_until:
            s, paused_until = pause(s), i + pcfg.kill_review_days
        w_before = s.weight(px)
        plan = plan_order(str(t.date()), target, s, px, market, pcfg.band, min_order_usd=10.0, fee_reserve=0.0)
        traded = 0.0
        if plan.side:
            res = broker.execute(plan, market, cloid_for(str(t.date()), "fake", 0))
            for f in res.fills:
                s = apply_fill(s, f, market.base, market.quote)
            traded = s.weight(px) - w_before
        t1 = dates[i + 1]
        px1 = float(d.loc[t1, "close"])
        s = roll_peak(s, px1)
        rows.append({"date": t, "target": target, "w_before": w_before, "trade": traded, "side": plan.side,
                     "usdc": s.usdc, "ubtc": s.ubtc, "date_next": t1, "equity_next": s.equity(px1) / initial_usdc,
                     "paused": s.paused})
    led = pd.DataFrame(rows).set_index("date")
    sim = simulate(d, full["target"], pcfg, start, end)
    return ReplayResult(led, sim, compare(led, sim, full["target"].loc[led.index]))


def compare(led: pd.DataFrame, sim: pd.DataFrame, targets: pd.Series) -> dict:
    """Paridad réplica vs simulate. simulate indexa por t+1 la operación decidida al cierre de t."""
    sim_by_decision = sim.copy()
    sim_by_decision.index = led.index[: len(sim)]
    trade_days_led = set(led.index[led["side"].notna()])
    trade_days_sim = set(sim_by_decision.index[sim_by_decision["trade"].abs() > 0])
    eq_led = led["equity_next"].to_numpy()[: len(sim)]
    eq_sim = sim["equity"].to_numpy()
    rel = np.abs(eq_led / eq_sim - 1)
    return {
        "days": int(len(led)),
        "target_max_abs_diff": float((led["target"] - targets).abs().max()),
        "trades_replay": len(trade_days_led), "trades_simulate": len(trade_days_sim),
        "trade_days_equal": trade_days_led == trade_days_sim,
        "only_replay": sorted(str(x.date()) for x in trade_days_led - trade_days_sim),
        "only_simulate": sorted(str(x.date()) for x in trade_days_sim - trade_days_led),
        "equity_final_replay": float(eq_led[-1]), "equity_final_simulate": float(eq_sim[-1]),
        "equity_max_rel_diff": float(rel.max()), "equity_final_rel_diff": float(rel[-1]),
    }
