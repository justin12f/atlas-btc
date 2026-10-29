"""
Brokers (sección 9): FakeBroker (réplica), PaperBroker (Modo A, libro real de mainnet, llenados simulados),
TestnetBroker (Modo B, órdenes reales en testnet con API wallet) y LiveBroker (bloqueado, regla 3).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from atlas.data.stitch import to_frame
from atlas.execution.fillsim import (candles_after, crossed, maker_limit, shortfall, vwap_for_notional,
                                     walk_book)
from atlas.hl.markets import cloid_for, round_px, round_sz, tick_size
from atlas.interfaces import ExecutionResult, Fill, OrderPlan, SpotMarket

TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def make_fill(cloid: str, k: int, side: str, px: float, sz: float, fee_rate: float, liquidity: str,
              ts: datetime, market: SpotMarket) -> Fill:
    """Comisión en el activo recibido: compra -> base (UBTC), venta -> quote (USDC)."""
    if side == "buy":
        fee, token = sz * fee_rate, market.base
    else:
        fee, token = px * sz * fee_rate, market.quote
    return Fill(fill_id=f"{cloid}-{k}", cloid=cloid, side=side, px=px, sz=sz, fee=fee, fee_token=token,
                liquidity=liquidity, ts=ts)


class FakeBroker:
    """Réplica: llena todo al precio de referencia (cierre de 00:00) con el costo del backtest (fee + slippage)."""
    mode = "fake"

    def __init__(self, cost_rate: float, ts: datetime | None = None):
        self.cost_rate, self.ts = cost_rate, ts

    def execute(self, plan: OrderPlan, market: SpotMarket, cloid: str, ref_close: float | None = None) -> ExecutionResult:
        if plan.side is None:
            return ExecutionResult(cloid, "skipped")
        ts = self.ts or datetime.fromisoformat(plan.date).replace(tzinfo=timezone.utc) + timedelta(days=1)
        f = make_fill(cloid, 0, plan.side, plan.ref_px, plan.sz, self.cost_rate, "taker", ts, market)
        return ExecutionResult(cloid, "filled", [f], {"cost_backtest": self.cost_rate})


class PaperBroker:
    """Modo A: libro real de mainnet, llenados simulados. No envía nada a ningún exchange."""
    mode = "paper"

    def __init__(self, info, clock=None, maker_fee: float = 0.0004, taker_fee: float = 0.0007,
                 policy: str = "maker_first", step_min: int = 5, timeout_min: int = 30, max_slippage: float = 0.005,
                 backtest_cost: float = 0.0013, capacity_usd: tuple = (10_000, 100_000, 1_000_000)):
        self.info, self.clock = info, clock or SystemClock()
        self.maker_fee, self.taker_fee, self.policy = maker_fee, taker_fee, policy
        self.step_min, self.timeout_min, self.max_slippage = step_min, timeout_min, max_slippage
        self.backtest_cost, self.capacity_usd = backtest_cost, capacity_usd

    def execute(self, plan: OrderPlan, market: SpotMarket, cloid: str, ref_close: float | None = None) -> ExecutionResult:
        if plan.side is None:
            return ExecutionResult(cloid, "skipped")
        side, ref = plan.side, ref_close or plan.ref_px
        book0 = self.info.book(market.coin)
        hyp = walk_book(book0, side, plan.sz)
        meta = {"ref_close": ref, "book0_mid": book0.mid, "book0_spread_bps": (book0.best_ask - book0.best_bid) / book0.mid * 1e4,
                "hyp_taker_vwap": hyp.vwap, "hyp_taker_cost": shortfall(side, hyp.vwap, ref, self.taker_fee) if hyp.vwap else None,
                "cost_backtest": self.backtest_cost,
                "capacity_bps": {str(u): self._probe(book0, side, u) for u in self.capacity_usd}, "attempts": []}
        fills: list[Fill] = []
        remaining = plan.sz
        if self.policy == "maker_first":
            book = book0
            for k in range(max(self.timeout_min // self.step_min, 1)):
                if k > 0:
                    book = self.info.book(market.coin)
                limit = round_px(maker_limit(book, side), market.sz_decimals, side=side)
                placed = self.clock.now()
                self.clock.sleep(self.step_min * 60)
                until = self.clock.now()
                rows = [r for r in self.info.candles(market.coin, "1m", _ms(placed), _ms(until)) if int(r["T"]) < _ms(until)]
                c1 = candles_after(to_frame(rows), _naive(placed), _naive(until))
                hit = crossed(side, limit, c1, tick_size(limit, market.sz_decimals))
                meta["attempts"].append({"k": k, "limit": limit, "placed": placed.isoformat(), "hit": str(hit) if hit is not None else None})
                if hit is not None:
                    fills.append(make_fill(cloid, len(fills), side, limit, remaining, self.maker_fee, "maker",
                                           until, market))
                    remaining = 0.0
                    break
        if remaining > 0:
            book = self.info.book(market.coin) if self.policy == "maker_first" else book0
            res = walk_book(book, side, remaining, self.max_slippage)
            sz = round_sz(res.filled_sz, market.sz_decimals)
            if sz > 0 and res.vwap:
                fills.append(make_fill(cloid, len(fills), side, res.vwap, sz, self.taker_fee, "taker",
                                       self.clock.now(), market))
                remaining = round_sz(remaining - sz, market.sz_decimals)
            meta["taker_short"] = res.short
        filled = sum(f.sz for f in fills)
        if fills:
            vwap = sum(f.px * f.sz for f in fills) / filled
            fee_rate = sum(f.fee * (f.px if f.fee_token == market.base else 1) for f in fills) / (vwap * filled)
            meta["vwap"], meta["cost"] = vwap, shortfall(side, vwap, ref, fee_rate)
        status = "filled" if remaining <= 0 else ("partial" if fills else "unfilled")
        return ExecutionResult(cloid, status, fills, meta)

    @staticmethod
    def _probe(book, side: str, usd: float) -> float | None:
        v = vwap_for_notional(book, side, usd)
        return None if v is None else abs(v / book.mid - 1) * 1e4


def _naive(t: datetime) -> pd.Timestamp:
    """UTC sin zona (como el índice de las velas)."""
    ts = pd.Timestamp(t)
    return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts


class TestnetBroker:
    """Modo B: plomería con órdenes reales en TESTNET (API wallet, sin permiso de retiro).

    `exchange` es un hyperliquid.exchange.Exchange construido con la llave del agente y
    account_address = dirección maestra; `info` un hyperliquid.info.Info de testnet. Las consultas
    usan la dirección maestra. Nunca acepta un exchange que no apunte a testnet (regla 3)."""
    mode = "testnet"
    __test__ = False   # que pytest no la confunda con una clase de pruebas

    def __init__(self, exchange, info, account: str, clock=None, max_order_usd: float = 200.0, step_min: int = 5,
                 timeout_min: int = 30, max_slippage: float = 0.005, dead_man_min: int = 45):
        if getattr(exchange, "base_url", None) != TESTNET_API_URL:
            raise RuntimeError("TestnetBroker solo opera contra la testnet de Hyperliquid")
        self.ex, self.info, self.account, self.clock = exchange, info, account, clock or SystemClock()
        self.max_order_usd, self.step_min, self.timeout_min = max_order_usd, step_min, timeout_min
        self.max_slippage, self.dead_man_min = max_slippage, dead_man_min

    def _status(self, cloid: str) -> dict:
        from hyperliquid.utils.types import Cloid
        r = self.info.query_order_by_cloid(self.account, Cloid(cloid))
        return r if isinstance(r, dict) else {}

    def _fills_for(self, cloids: set[str], since_ms: int, market: SpotMarket) -> list[Fill]:
        out = []
        for f in self.info.user_fills_by_time(self.account, since_ms):
            if f.get("cloid") in cloids and f.get("coin") == market.coin:
                side = "buy" if f["side"] == "B" else "sell"
                out.append(Fill(fill_id=str(f["tid"]), cloid=f["cloid"], side=side, px=float(f["px"]), sz=float(f["sz"]),
                                fee=float(f["fee"]), fee_token=f.get("feeToken", market.quote),
                                liquidity="taker" if f.get("crossed") else "maker",
                                ts=datetime.fromtimestamp(f["time"] / 1000, tz=timezone.utc)))
        return out

    def execute(self, plan: OrderPlan, market: SpotMarket, cloid: str, ref_close: float | None = None) -> ExecutionResult:
        from hyperliquid.utils.types import Cloid
        if plan.side is None:
            return ExecutionResult(cloid, "skipped")
        is_buy = plan.side == "buy"
        sz = round_sz(min(plan.sz, self.max_order_usd / plan.ref_px), market.sz_decimals)
        start = self.clock.now()
        self.ex.schedule_cancel(_ms(start) + self.dead_man_min * 60_000)
        cloids: set[str] = set()
        remaining = sz
        for k in range(max(self.timeout_min // self.step_min, 1)):
            c = cloid_for(plan.date, self.mode, k)
            cloids.add(c)
            st = self._status(c)
            if st.get("status") == "order":           # ya existía (reintento tras caída): no se reenvía
                o = st["order"]
                if o.get("status") == "filled":
                    remaining = 0.0
                    break
            else:
                book = self.info.l2_snapshot(market.coin)
                best = float(book["levels"][0 if is_buy else 1][0]["px"])
                px = round_px(best, market.sz_decimals, side=plan.side)
                self.ex.order(market.coin, is_buy, remaining, px, {"limit": {"tif": "Alo"}}, cloid=Cloid(c))
            self.clock.sleep(self.step_min * 60)
            st = self._status(c)
            o = st.get("order", {}) if st.get("status") == "order" else {}
            if o.get("status") == "filled":
                remaining = 0.0
                break
            self.ex.cancel_by_cloid(market.coin, Cloid(c))
            filled = sum(f.sz for f in self._fills_for({c}, _ms(start), market))
            remaining = round_sz(max(remaining - filled, 0.0), market.sz_decimals)
            if remaining <= 0:
                break
        if remaining > 0:
            c = cloid_for(plan.date, self.mode, 99)
            cloids.add(c)
            book = self.info.l2_snapshot(market.coin)
            best = float(book["levels"][1 if is_buy else 0][0]["px"])
            cap = round_px(best * (1 + self.max_slippage if is_buy else 1 - self.max_slippage), market.sz_decimals)
            self.ex.order(market.coin, is_buy, remaining, cap, {"limit": {"tif": "Ioc"}}, cloid=Cloid(c))
        self.ex.schedule_cancel(None)
        fills = self._fills_for(cloids, _ms(start), market)
        got = round_sz(sum(f.sz for f in fills), market.sz_decimals)
        status = "filled" if got >= sz else ("partial" if fills else "unfilled")
        return ExecutionResult(cloid, status, fills, {"requested_sz": sz})


class LiveBroker:
    """Mainnet real: fuera de alcance de este plan. No hay forma de habilitarlo (regla 3)."""
    mode = "live"

    def __init__(self, *args, **kwargs):
        raise RuntimeError("LiveBroker está bloqueado: nada envía órdenes a mainnet en este plan")
