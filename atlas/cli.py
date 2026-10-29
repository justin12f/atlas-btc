"""
CLI de Atlas:  python -m atlas.cli <comando>

  collect [--final]                     recolección horaria (mainnet, sin llaves)
  backfill [--from-fixtures] [--funding-from-start] [--binance-days N]
  daily [--mode paper|testnet]          corrida diaria (idempotente, con candado)
  resume --mode M --note "motivo"       reanudar tras el interruptor (reinicia el máximo)
  ack-market --pair UBTC/USDC           aceptar un cambio de spotMeta tras revisarlo
  status                                estado de la base y de cada modo
  report [--weekly] [--date D]          reportes en reports/
  replay --from D --to D --broker fake  réplica contra engine.portfolio.simulate
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from atlas.config import ROOT, data_dir, db_path, load_live_config
from atlas.data.store import SQLiteStore


@contextmanager
def process_lock(name: str):
    """flock: impide dos procesos del mismo trabajo a la vez."""
    p = data_dir() / "data" / "live" / f"{name}.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"otro proceso '{name}' está corriendo; salgo sin hacer nada")
            sys.exit(0)
        fh.write(str(os.getpid()))
        fh.flush()
        yield


def _store(cfg: dict) -> SQLiteStore:
    return SQLiteStore(db_path(cfg))


def _info(cfg: dict):
    from atlas.hl.client import HLInfo
    h = cfg["hl"]
    return HLInfo(h["mainnet_url"], weight_per_min=h["weight_per_min"], timeout_s=h["timeout_s"],
                  max_retries=h["max_retries"])


def _market(cfg: dict, info, store: SQLiteStore):
    from atlas.hl.markets import resolve_spot
    m = resolve_spot(info.spot_meta(), cfg["market"]["spot_pair"])
    store.kv_set(f"market_cache:{m.pair}", json.dumps(m.__dict__))
    return m


# ---------------------------------------------------------------------------------------------- comandos
def cmd_collect(args, cfg) -> int:
    from atlas.data.collectors import collect_all
    with process_lock("collect"):
        st, info = _store(cfg), _info(cfg)
        m = _market(cfg, info, st)
        rep = collect_all(info, st, m.coin, cfg["market"]["perp_coin"])
        print(json.dumps({"candles": rep.candles, "books_spread_bps": rep.books, "funding": rep.funding,
                          "errors": rep.errors, "weight_used": info.budget.used()}, indent=1))
        if args.final:
            _quality_yesterday(cfg, st, m)
        return 1 if rep.errors else 0


def _quality_yesterday(cfg, st, m) -> None:
    from atlas.data.quality import check_day
    from atlas.data.stitch import to_frame
    day = pd.Timestamp.now("UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)
    flags = check_day(to_frame(st.candles(m.coin, "5m")), day, to_frame(st.candles(cfg["market"]["perp_coin"], "5m")))
    st.log_event("warn" if flags else "info", "quality", f"{day.date()}: {flags or 'ok'}")
    print(f"calidad {day.date()}: {flags or 'ok'}")


def cmd_backfill(args, cfg) -> int:
    from atlas.data.collectors import collect_candles, collect_funding, load_fixture_candles
    st = _store(cfg)
    if args.from_fixtures:
        fx = ROOT / "tests" / "fixtures" / "hl"
        m = json.loads(st.kv_get("market_cache:" + cfg["market"]["spot_pair"]) or "null")
        spot = m["coin"] if m else _market(cfg, _info(cfg), st).coin
        for coin, iv, f in [(spot, "5m", "candles_spot_5m_full"), (spot, "1m", "candles_spot_1m_full"),
                            (spot, "1d", "candles_spot_1d_full"), ("BTC", "5m", "candles_perp_5m_full"),
                            ("BTC", "1d", "candles_perp_1d_full")]:
            print(coin, iv, load_fixture_candles(st, coin, iv, json.load(open(fx / f"{f}.json"))))
        return 0
    info = _info(cfg)
    m = _market(cfg, info, st)
    for coin in (m.coin, cfg["market"]["perp_coin"]):
        for iv in ("5m", "1m", "1d"):
            print(coin, iv, collect_candles(info, st, coin, iv))
    if args.funding_from_start:
        print("funding", collect_funding(info, st, cfg["market"]["perp_coin"], start_ms=0))
    if args.binance_days:
        print("binance 5m", backfill_binance(st, args.binance_days))
    return 0


def backfill_binance(st: SQLiteStore, days: int) -> int:
    """Respaldo: Binance BTCUSDC 5m (API pública) con source='binance' para huecos de rv."""
    import requests
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    n = 0
    while start < end:
        r = requests.get("https://data-api.binance.vision/api/v3/klines",
                         params={"symbol": "BTCUSDC", "interval": "5m", "startTime": start, "limit": 1000}, timeout=30)
        r.raise_for_status()
        b = [k for k in r.json() if k[6] < end]
        if not b:
            break
        n += st.upsert_candles("BTCUSDC", "5m", [{"t": k[0], "o": k[1], "h": k[2], "l": k[3], "c": k[4], "v": k[5],
                                                  "n": k[8]} for k in b], "binance")
        start = b[-1][0] + 1
        time.sleep(0.3)
    return n


def _context(cfg, st):
    from atlas.alerts import Alerter
    from atlas.daily import Context
    from engine.strategy import load_config
    seed = pd.read_parquet(ROOT / cfg["data"]["history_seed"])
    return Context(cfg=cfg, C=load_config(), store=st, info=_info(cfg), seed=seed,
                   alerter=Alerter(st, cfg["alerts"]["telegram"], cfg["alerts"]["healthchecks"]))


def cmd_daily(args, cfg) -> int:
    from atlas.daily import run_daily
    from atlas.execution.brokers import PaperBroker
    if args.mode == "testnet" and not cfg["testnet"].get("enabled"):
        print("testnet desactivado en config/live_hyperliquid.yaml (testnet.enabled: false)")
        return 0
    with process_lock(f"daily-{args.mode}"):
        st = _store(cfg)
        ctx = _context(cfg, st)
        if args.mode == "paper":
            p, f = cfg["paper"], cfg["fees"]
            broker = PaperBroker(ctx.info, maker_fee=f["spot_maker"], taker_fee=f["spot_taker"], policy=p["fill_policy"],
                                 step_min=p["reprice_every_min"], timeout_min=p["maker_timeout_min"],
                                 max_slippage=p["max_slippage"], capacity_usd=tuple(p["capacity_probe_usd"]))
        else:
            broker = _testnet_broker(cfg)
        s = run_daily(ctx, broker, args.mode)
        print(json.dumps(s.__dict__, indent=1, default=str))
        from atlas.reports import daily_report, write_report
        write_report(data_dir() / "reports" / "daily", f"{s.date}-{args.mode}", daily_report(st, args.mode, s.date))
        return 0


def _testnet_broker(cfg):
    """Modo B. Requiere HL_TESTNET_ACCOUNT y HL_TESTNET_AGENT_KEY (llave del AGENTE, nunca la maestra)."""
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from atlas.config import secret
    from atlas.execution.brokers import TESTNET_API_URL, TestnetBroker
    acct, key = secret("HL_TESTNET_ACCOUNT"), secret("HL_TESTNET_AGENT_KEY")
    if not (acct and key):
        raise SystemExit("faltan HL_TESTNET_ACCOUNT / HL_TESTNET_AGENT_KEY")
    agent = Account.from_key(key)
    ex = Exchange(agent, TESTNET_API_URL, account_address=acct)
    return TestnetBroker(ex, Info(TESTNET_API_URL, skip_ws=True), acct, max_order_usd=cfg["testnet"]["max_order_usd"],
                         step_min=cfg["paper"]["reprice_every_min"], timeout_min=cfg["paper"]["maker_timeout_min"],
                         max_slippage=cfg["paper"]["max_slippage"])


def cmd_resume(args, cfg) -> int:
    if not args.note or len(args.note.strip()) < 10:
        print("la nota es obligatoria (≥ 10 caracteres): describe la revisión hecha")
        return 2
    st = _store(cfg)
    r = st.last_ledger(args.mode)
    if not r or not r["paused"]:
        print("no hay pausa activa")
        return 0
    eq = r["usdc"] + r["ubtc"] * r["mark_px"]
    st.write_ledger(args.mode, r["date"], r["usdc"], r["ubtc"], r["mark_px"], eq, False, r["target"])
    st.log_event("warn", "resume", args.note, {"mode": args.mode, "date": r["date"], "new_peak": eq})
    print(f"reanudado {args.mode}; máximo reiniciado a {eq:,.2f}")
    return 0


def cmd_ack_market(args, cfg) -> int:
    st = _store(cfg)
    key = f"market_fp:{args.pair}"
    pending = st.kv_get(f"{key}:pending")
    if not pending:
        print("no hay cambio pendiente")
        return 0
    st.kv_set(key, pending)
    st.log_event("warn", "ack_market", f"aceptado cambio de spotMeta para {args.pair}", {"fp": pending})
    print("aceptado")
    return 0


def cmd_status(args, cfg) -> int:
    st = _store(cfg)
    out = {"db": str(db_path(cfg)), "schema_version": st.schema_version(), "candles": {}}
    for r in st.con.execute("SELECT coin, interval, COUNT(*), MIN(t), MAX(t) FROM candles GROUP BY coin, interval"):
        out["candles"][f"{r[0]}/{r[1]}"] = {"n": r[2], "from": str(pd.Timestamp(r[3], unit="ms")),
                                           "to": str(pd.Timestamp(r[4], unit="ms"))}
    for mode in ("paper", "testnet", "fake"):
        last = st.last_ledger(mode)
        if last:
            out[mode] = {k: last[k] for k in ("date", "equity", "weight", "drawdown", "paused", "target")}
    out["last_events"] = [{k: e[k] for k in ("ts", "level", "kind", "message")} for e in st.events(limit=5)]
    print(json.dumps(out, indent=1, default=str))
    return 0


def cmd_report(args, cfg) -> int:
    from atlas.reports import daily_report, weekly_report, write_report
    st = _store(cfg)
    date = args.date or str((pd.Timestamp.now("UTC").normalize() - pd.Timedelta(days=1)).date())
    base = data_dir() / "reports"
    if args.weekly:
        text, data = weekly_report(st, date)
        print(write_report(base / "weekly", date, text, data))
    else:
        print(write_report(base / "daily", f"{date}-paper", daily_report(st, "paper", date)))
    return 0


def cmd_replay(args, cfg) -> int:
    from atlas.replay import run_replay
    from engine.strategy import load_config
    if args.broker != "fake":
        print("la réplica solo usa --broker fake")
        return 2
    d = pd.read_parquet(ROOT / cfg["data"]["history_seed"])
    r = run_replay(d, args.date_from, args.date_to, load_config(), cfg["paper"]["initial_usdc"],
                   truncated=not args.fast)
    print(json.dumps(r.report, indent=1))
    out = data_dir() / "reports" / "replay"
    out.mkdir(parents=True, exist_ok=True)
    r.ledger.to_csv(out / f"replay_{args.date_from}_{args.date_to}.csv")
    ok = r.report["trade_days_equal"] and r.report["target_max_abs_diff"] < 1e-9 and r.report["equity_max_rel_diff"] < 1e-3
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atlas")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--final", action="store_true")
    p = sub.add_parser("backfill")
    p.add_argument("--from-fixtures", action="store_true")
    p.add_argument("--funding-from-start", action="store_true")
    p.add_argument("--binance-days", type=int, default=0)
    p = sub.add_parser("daily")
    p.add_argument("--mode", default="paper", choices=["paper", "testnet"])
    p = sub.add_parser("resume")
    p.add_argument("--mode", default="paper", choices=["paper", "testnet"])
    p.add_argument("--note", required=True)
    p = sub.add_parser("ack-market")
    p.add_argument("--pair", default="UBTC/USDC")
    sub.add_parser("status")
    p = sub.add_parser("report")
    p.add_argument("--weekly", action="store_true")
    p.add_argument("--date")
    p = sub.add_parser("replay")
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.add_argument("--broker", default="fake")
    p.add_argument("--fast", action="store_true", help="usa objetivos de la serie completa (probado igual)")
    args = ap.parse_args(argv)
    cfg = load_live_config(args.config)
    fn = {"collect": cmd_collect, "backfill": cmd_backfill, "daily": cmd_daily, "resume": cmd_resume,
          "ack-market": cmd_ack_market, "status": cmd_status, "report": cmd_report, "replay": cmd_replay}[args.cmd]
    return fn(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
