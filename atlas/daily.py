"""
Corrida diaria (sección 8): barras -> señal -> riesgo -> plan -> ejecución -> ledger -> alertas.

Idempotente: trabaja sobre "el último día completo"; si ese día ya tiene ledger no hace nada. Si una
corrida anterior quedó a medias, reutiliza los fills guardados (fill_id único) y solo re-ejecuta lo que
falta con un cloid nuevo (intento siguiente). El candado de proceso lo pone el CLI (flock).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

from atlas.config import code_version, strategy_hash
from atlas.data.stitch import stitch, to_frame
from atlas.data.store import SQLiteStore
from atlas.execution.ledger import apply_fill, initial_state, pause, roll_peak
from atlas.execution.planner import plan_order
from atlas.hl.markets import cloid_for, market_fingerprint, resolve_spot
from atlas.interfaces import Fill, LedgerState, SpotMarket
from atlas.risk import RiskInputs, evaluate
from atlas.signal import SignalError, compute_signal

BINANCE_REF = "BTCUSDC"   # respaldo de 5m (source='binance') para huecos de rv


@dataclass
class Context:
    cfg: dict                 # config/live_hyperliquid.yaml
    C: dict                   # config/strategy.yaml
    store: SQLiteStore
    info: object              # MarketData de mainnet
    seed: pd.DataFrame        # data/processed/btc_daily.parquet
    alerter: object | None = None
    clock: object | None = None


@dataclass
class DailySummary:
    mode: str
    date: str
    status: str               # done | already_done | safe_mode | killed | error
    target: float | None = None
    weight_before: float | None = None
    weight_after: float | None = None
    equity: float | None = None
    trade: dict | None = None
    blocks: list = field(default_factory=list)
    alerts: list = field(default_factory=list)


def utc_now(ctx: Context) -> datetime:
    return ctx.clock.now() if ctx.clock else datetime.now(timezone.utc)


def last_complete_day(now: datetime) -> str:
    return str((pd.Timestamp(now).tz_convert("UTC").normalize() - pd.Timedelta(days=1)).date())


def resolve_market(ctx: Context) -> tuple[SpotMarket, bool]:
    """Resuelve el par con spotMeta y compara su huella con la aceptada. Cambio -> modo seguro hasta `ack-market`."""
    sm = ctx.info.spot_meta()
    m = resolve_spot(sm, ctx.cfg["market"]["spot_pair"])
    fp = market_fingerprint(m)
    key = f"market_fp:{m.pair}"
    accepted = ctx.store.kv_get(key)
    ctx.store.kv_set(f"market_cache:{m.pair}", json.dumps(asdict(m)))
    if accepted is None:
        ctx.store.kv_set(key, fp)
        return m, False
    if accepted != fp:
        ctx.store.kv_set(f"{key}:pending", fp)
        ctx.store.log_event("error", "market_changed", f"spotMeta cambió para {m.pair}", asdict(m))
        return m, True
    return m, False


def build_series(ctx: Context, market: SpotMarket, now: datetime) -> pd.DataFrame:
    s = ctx.store
    spot5 = [r for r in s.candles(market.coin, "5m") if r["source"] == "hl"]
    binance = s.candles(BINANCE_REF, "5m")
    return stitch(ctx.seed, to_frame(s.candles(market.coin, "1d")), to_frame(spot5),
                  ctx.cfg["data"]["bitstamp_last_day"], perp5=to_frame(s.candles(ctx.cfg["market"]["perp_coin"], "5m")),
                  binance5=to_frame(binance) if binance else None, now=pd.Timestamp(now))


def load_state(store: SQLiteStore, mode: str, day: str, initial_usdc: float) -> LedgerState:
    r = store.last_ledger(mode, before=day)
    if r is None:
        return initial_state(mode, initial_usdc)
    return LedgerState(mode=mode, date=r["date"], usdc=r["usdc"], ubtc=r["ubtc"], peak=r["peak"], paused=bool(r["paused"]))


def _row_to_fill(r: dict) -> Fill:
    return Fill(r["fill_id"], r["cloid"], r["side"], r["px"], r["sz"], r["fee"], r["fee_token"], r["liquidity"], r["ts"])


def run_daily(ctx: Context, broker, mode: str = "paper") -> DailySummary:
    now = utc_now(ctx)
    day = last_complete_day(now)
    st = ctx.store
    if st.ledger_row(mode, day):
        return DailySummary(mode, day, "already_done")
    run_id = f"daily-{mode}-{day}-{uuid.uuid4().hex[:8]}"
    st.start_run(run_id, f"daily:{mode}")
    try:
        summary = _run(ctx, broker, mode, day, now)
        st.finish_run(run_id, summary.status)
    except Exception as e:
        st.finish_run(run_id, "error", f"{type(e).__name__}: {e}")
        st.log_event("error", "daily", f"{type(e).__name__}: {e}", {"mode": mode, "date": day})
        if ctx.alerter:
            ctx.alerter.send(f"ERROR en daily {mode} {day}: {type(e).__name__}: {e}", "error")
            ctx.alerter.ping(False, str(e))
        raise
    if ctx.alerter:
        _notify(ctx, summary)
    return summary


def _run(ctx: Context, broker, mode: str, day: str, now: datetime) -> DailySummary:
    st, cfg, C = ctx.store, ctx.cfg, ctx.C
    rcfg = cfg["risk"]
    blocks: list[str] = []
    market, changed = resolve_market(ctx)
    d = build_series(ctx, market, now)
    sig = None
    try:
        sig = compute_signal(d, day, strategy_hash(), code_version(), C)
        st.save_signal(sig, mode)
    except SignalError as e:
        blocks.append(f"señal: {e}")
    flags = d.loc[day, "quality_flags"] if pd.Timestamp(day) in d.index else "day_missing"

    # --- precios del momento (libro spot y perp de mainnet)
    book = ctx.info.book(market.coin)
    perp = ctx.info.book(cfg["market"]["perp_coin"])
    mid = book.mid
    last5 = st.last_candle_t(market.coin, "5m")
    age = None if last5 is None else (now.timestamp() * 1000 - (last5 + 300_000)) / 60_000
    close = sig.close if sig else mid

    state = load_state(st, mode, day, cfg["paper"]["initial_usdc"] if mode != "testnet" else cfg["testnet"]["initial_usdc"])
    last_level = float(st.kv_get(f"dd_alert_level:{mode}") or 0)
    risk = evaluate(RiskInputs(equity=state.equity(close), peak=state.peak, paused=state.paused,
                               kill_dd=C["risk"]["kill_switch_dd"], data_age_min=age,
                               max_data_age_min=rcfg["max_data_age_min"], quality_flags=flags,
                               divergences={"spot_vs_perp": abs(mid / perp.mid - 1)},
                               max_divergence=rcfg["max_ref_divergence"], market_changed=changed,
                               extra_blocks=blocks, drawdown_levels=tuple(cfg["alerts"]["drawdown_levels"]),
                               last_alerted_level=last_level))
    st.kv_set(f"dd_alert_level:{mode}", str(risk.new_alert_level))
    for b in risk.blocks:
        st.log_event("warn", "safe_mode", b, {"mode": mode, "date": day})
    status = "done"
    if risk.kill:
        state = pause(state)
        st.log_event("critical", "kill_switch", risk.alerts[0], {"mode": mode, "date": day, "dd": risk.drawdown})
        status = "killed"
    elif not risk.trade_allowed:
        status = "safe_mode"

    target = 0.0 if state.paused else (sig.target if sig else None)
    w_before = state.weight(mid)
    trade = None
    if status in ("done", "killed") and target is not None:
        plan = plan_order(day, target, state, mid, market, C["risk"]["band"], rcfg["min_order_usd"],
                          max_order_usd=cfg["testnet"]["max_order_usd"] if mode == "testnet" else None)
        if plan.side:
            state, trade = _execute(ctx, broker, mode, day, plan, market, state, close)
        else:
            trade = {"side": None, "reason": plan.reason}
    state = roll_peak(state, mid)
    st.write_ledger(mode, day, state.usdc, state.ubtc, mid, state.peak, state.paused, target)
    return DailySummary(mode, day, status, target, w_before, state.weight(mid), state.equity(mid), trade,
                        risk.blocks, risk.alerts)


def _execute(ctx: Context, broker, mode: str, day: str, plan, market: SpotMarket, state: LedgerState,
             close: float) -> tuple[LedgerState, dict]:
    st = ctx.store
    prior = st.orders(mode, day)
    done_fills = [_row_to_fill(f) for o in prior for f in st.fills(mode, o["cloid"])]
    if any(o["status"] == "filled" for o in prior):
        fills, res_meta, status = done_fills, {"recovered": True}, "filled"
    else:
        if done_fills:                         # parcial previo: aplica lo hecho y replanea el resto
            for f in done_fills:
                state = apply_fill(state, f, market.base, market.quote)
            plan = plan_order(day, plan.target, state, plan.ref_px, market, ctx.C["risk"]["band"],
                              ctx.cfg["risk"]["min_order_usd"])
            if not plan.side:
                return state, {"side": None, "reason": "completado por fills previos", "recovered": True}
        cloid = cloid_for(day, mode, len(prior))
        st.upsert_order(cloid, mode, day, plan.side, plan.ref_px, plan.sz, "maker_first", "working")
        res = broker.execute(plan, market, cloid, ref_close=close)
        for f in res.fills:
            st.insert_fill(mode, f)
        st.upsert_order(cloid, mode, day, plan.side, plan.ref_px, plan.sz, "maker_first", res.status, meta=res.meta)
        fills, res_meta, status = res.fills, res.meta, res.status
    for f in fills:
        state = apply_fill(state, f, market.base, market.quote)
    filled = sum(f.sz for f in fills)
    vwap = sum(f.px * f.sz for f in fills) / filled if filled else None
    return state, {"side": plan.side, "sz": plan.sz, "filled": filled, "vwap": vwap, "status": status,
                   "cost": res_meta.get("cost"), "hyp_taker_cost": res_meta.get("hyp_taker_cost")}


def _notify(ctx: Context, s: DailySummary) -> None:
    a = ctx.alerter
    lines = [f"{s.mode} {s.date}: {s.status}", f"objetivo {s.target}", f"peso {s.weight_before} -> {s.weight_after}",
             f"equity {s.equity:,.2f}" if s.equity else ""]
    if s.trade and s.trade.get("side"):
        t = s.trade
        lines.append(f"operación {t['side']} {t['filled']} @ {t['vwap']} costo {t.get('cost')}")
    lines += [f"BLOQUEO: {b}" for b in s.blocks] + s.alerts
    a.send("\n".join(x for x in lines if x), "critical" if s.status == "killed" else "info")
    a.ping(True, "\n".join(lines))          # la corrida terminó; los bloqueos van por Telegram
