"""
Cliente de lectura (/info) de Hyperliquid con presupuesto de peso y reintentos.

No usa `hyperliquid.info.Info` para lectura porque su constructor hace llamadas de red sin reintentos
y no lleva cuenta del peso; aquí el transporte es inyectable (pruebas sin red) y el peso se controla
contra el límite de 1,200/min por IP (regla 8). Para firmar órdenes en testnet sí se usa el SDK.
"""
from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

import requests

from atlas.interfaces import Book, Level

# Peso por tipo de petición (docs de HL, verificado 2026-10-29). El resto pesa 20.
LIGHT = {"l2Book", "allMids", "clearinghouseState", "spotClearinghouseState", "orderStatus"}
CANDLE_ITEMS_PER_WEIGHT = 60

Transport = Callable[[str, dict, float], tuple[int, object]]


class HLError(RuntimeError):
    pass


def requests_transport(url: str, payload: dict, timeout: float) -> tuple[int, object]:
    r = requests.post(url, json=payload, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return r.status_code, body


def request_weight(payload: dict) -> int:
    return 2 if payload.get("type") in LIGHT else 20


class WeightBudget:
    """Ventana deslizante de 60 s: bloquea (durmiendo) si la siguiente petición rebasaría el presupuesto."""

    def __init__(self, per_min: int, now: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.per_min, self._now, self._sleep = per_min, now, sleep
        self._spent: deque[tuple[float, int]] = deque()

    def used(self) -> int:
        t = self._now()
        while self._spent and t - self._spent[0][0] >= 60:
            self._spent.popleft()
        return sum(w for _, w in self._spent)

    def acquire(self, w: int) -> None:
        while self.used() + w > self.per_min and self._spent:
            self._sleep(max(60 - (self._now() - self._spent[0][0]), 0.05))
        self._spent.append((self._now(), w))

    def charge(self, w: int) -> None:
        """Peso adicional conocido solo después de la respuesta (velas)."""
        if w > 0:
            self._spent.append((self._now(), w))


class HLInfo:
    """Lectura de mercado. Implementa atlas.interfaces.MarketData."""

    def __init__(self, base_url: str, transport: Transport = requests_transport, weight_per_min: int = 1000,
                 timeout_s: float = 20, max_retries: int = 5, sleep: Callable[[float], None] = time.sleep,
                 budget: WeightBudget | None = None):
        self.base_url = base_url.rstrip("/")
        self.transport, self.timeout_s, self.max_retries, self._sleep = transport, timeout_s, max_retries, sleep
        self.budget = budget or WeightBudget(weight_per_min, sleep=sleep)

    # ------------------------------------------------------------------ transporte
    def post(self, payload: dict) -> object:
        self.budget.acquire(request_weight(payload))
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                status, body = self.transport(self.base_url + "/info", payload, self.timeout_s)
            except (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError) as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if status == 200:
                    return body
                last = f"HTTP {status}: {str(body)[:200]}"
                if status not in (429, 500, 502, 503, 504):
                    raise HLError(f"{payload.get('type')}: {last}")
            if attempt < self.max_retries:
                # backoff exponencial con jitter; 429 espera más
                base = 2.0 if "429" in (last or "") else 0.5
                self._sleep(min(base * 2 ** attempt, 60) * (1 + random.random() * 0.25))
        raise HLError(f"{payload.get('type')}: agotados {self.max_retries} reintentos ({last})")

    # ------------------------------------------------------------------ lecturas
    def meta(self) -> dict:
        return self.post({"type": "meta"})

    def spot_meta(self) -> dict:
        return self.post({"type": "spotMeta"})

    def all_mids(self) -> dict[str, str]:
        return self.post({"type": "allMids"})

    def user_fees(self, user: str) -> dict:
        return self.post({"type": "userFees", "user": user})

    def spot_user_state(self, user: str) -> dict:
        return self.post({"type": "spotClearinghouseState", "user": user})

    def book(self, coin: str) -> Book:
        return parse_book(self.post({"type": "l2Book", "coin": coin}))

    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
        rows = self.post({"type": "candleSnapshot",
                          "req": {"coin": coin, "interval": interval, "startTime": int(start_ms),
                                  "endTime": int(end_ms)}})
        if not isinstance(rows, list):
            raise HLError(f"candleSnapshot {coin} {interval}: respuesta inesperada {str(rows)[:120]}")
        self.budget.charge(len(rows) // CANDLE_ITEMS_PER_WEIGHT)
        return rows

    def funding_history(self, coin: str, start_ms: int, end_ms: int | None = None) -> list[dict]:
        p = {"type": "fundingHistory", "coin": coin, "startTime": int(start_ms)}
        if end_ms is not None:
            p["endTime"] = int(end_ms)
        return self.post(p)


def parse_book(raw: dict) -> Book:
    bids, asks = raw["levels"]
    lv = lambda side: tuple(Level(float(x["px"]), float(x["sz"])) for x in side)  # noqa: E731
    ts = datetime.fromtimestamp(raw["time"] / 1000, tz=timezone.utc)
    return Book(coin=raw["coin"], ts=ts, bids=lv(bids), asks=lv(asks))
