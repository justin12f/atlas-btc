"""
Contratos entre módulos (y entre terminales T1–T4). Cambios aquí los aprueba el orquestador.

Convenciones:
  - Tiempos en UTC. `date` = día calendario UTC cuya vela diaria CIERRA a las 00:00 del día siguiente.
  - Precios y tamaños como float en el código; las órdenes se redondean con atlas.hl.markets.
  - `mode` ∈ {"paper", "testnet", "fake"}; cada modo tiene su propio ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

MODES = ("paper", "testnet", "fake")


@dataclass(frozen=True)
class SpotMarket:
    """Par spot resuelto con spotMeta en tiempo de ejecución (nunca escrito a mano)."""
    pair: str            # "UBTC/USDC"
    coin: str            # nombre interno para la API: "@142"
    index: int           # índice del par en spotMeta.universe
    asset_id: int        # 10000 + index (para órdenes)
    base: str
    quote: str
    sz_decimals: int     # del token base
    base_token_index: int


@dataclass(frozen=True)
class Level:
    px: float
    sz: float


@dataclass(frozen=True)
class Book:
    coin: str
    ts: datetime
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]

    @property
    def best_bid(self) -> float:
        return self.bids[0].px

    @property
    def best_ask(self) -> float:
        return self.asks[0].px

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2


@dataclass(frozen=True)
class Signal:
    date: str            # "YYYY-MM-DD"
    trend: float
    cycle_mult: float
    vol_fc: float
    vol_scaler: float
    target: float
    close: float
    config_hash: str
    code_version: str


@dataclass(frozen=True)
class OrderPlan:
    """Salida del planificador: qué operar hoy (o nada)."""
    date: str
    side: str | None     # "buy" | "sell" | None (no operar)
    sz: float            # en unidades del activo base, ya redondeado
    ref_px: float        # precio de referencia (marca)
    target: float
    weight_before: float
    notional: float      # USDC con signo (+ compra)
    reason: str


@dataclass(frozen=True)
class Fill:
    fill_id: str
    cloid: str
    side: str
    px: float
    sz: float
    fee: float
    fee_token: str       # en spot la comisión se cobra en el activo recibido
    liquidity: str       # "maker" | "taker"
    ts: datetime


@dataclass
class ExecutionResult:
    cloid: str
    status: str          # "filled" | "partial" | "unfilled" | "skipped"
    fills: list[Fill] = field(default_factory=list)
    meta: dict = field(default_factory=dict)   # costo hipotético taker, costo backtest, etc.


@dataclass
class LedgerState:
    mode: str
    date: str | None
    usdc: float
    ubtc: float
    peak: float
    paused: bool = False

    def equity(self, px: float) -> float:
        return self.usdc + self.ubtc * px

    def weight(self, px: float) -> float:
        eq = self.equity(px)
        return self.ubtc * px / eq if eq > 0 else 0.0


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


@runtime_checkable
class MarketData(Protocol):
    """Lectura de mercado (mainnet en modo papel)."""
    def spot_meta(self) -> dict: ...
    def book(self, coin: str) -> Book: ...
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]: ...
    def all_mids(self) -> dict[str, str]: ...


@runtime_checkable
class Broker(Protocol):
    mode: str

    def execute(self, plan: OrderPlan, market: SpotMarket, cloid: str,
                ref_close: float | None = None) -> ExecutionResult: ...


@runtime_checkable
class Store(Protocol):
    def upsert_candles(self, coin: str, interval: str, rows: list[dict], source: str) -> int: ...
    def candles(self, coin: str, interval: str, start_ms: int | None = None,
                end_ms: int | None = None) -> list[dict]: ...
    def log_event(self, level: str, kind: str, message: str, payload: dict | None = None) -> None: ...
