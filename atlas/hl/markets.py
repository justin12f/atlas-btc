"""
Resolución de pares spot y redondeo de órdenes según las reglas de Hyperliquid.

Regla 5 de CLAUDE.md: "@N" y szDecimals SIEMPRE salen de spotMeta en tiempo de ejecución.
Verificado 2026-10-29: UBTC/USDC = @142 (szDecimals 5) en mainnet; en testnet no hay UBTC.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_EVEN, Decimal

from atlas.interfaces import SpotMarket

SPOT_ASSET_OFFSET = 10_000
SPOT_MAX_DECIMALS = 8
PERP_MAX_DECIMALS = 6
MAX_SIG_FIGS = 5


class MarketNotFound(LookupError):
    pass


def resolve_spot(spot_meta: dict, pair: str) -> SpotMarket:
    """'UBTC/USDC' -> SpotMarket(coin='@142', ...). Falla si no existe o si hay más de un par igual."""
    base, quote = pair.split("/")
    tokens = {t["index"]: t for t in spot_meta["tokens"]}
    hits = []
    for u in spot_meta["universe"]:
        b, q = u["tokens"]
        if tokens[b]["name"] == base and tokens[q]["name"] == quote:
            hits.append((u, tokens[b]))
    if not hits:
        raise MarketNotFound(f"{pair} no está en spotMeta")
    if len(hits) > 1:
        raise MarketNotFound(f"{pair} es ambiguo en spotMeta: {[h[0]['name'] for h in hits]}")
    u, bt = hits[0]
    return SpotMarket(pair=pair, coin=u["name"], index=u["index"], asset_id=SPOT_ASSET_OFFSET + u["index"],
                      base=base, quote=quote, sz_decimals=int(bt["szDecimals"]), base_token_index=bt["index"])


def market_fingerprint(m: SpotMarket) -> str:
    """Huella del par: si cambia entre días (índice o szDecimals), el sistema entra en modo seguro."""
    key = json.dumps([m.pair, m.coin, m.index, m.sz_decimals, m.base_token_index])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _decimals_allowed(sz_decimals: int, spot: bool) -> int:
    return max((SPOT_MAX_DECIMALS if spot else PERP_MAX_DECIMALS) - sz_decimals, 0)


def is_valid_px(px: float, sz_decimals: int, spot: bool = True) -> bool:
    """≤ 5 cifras significativas y ≤ (8|6 − szDecimals) decimales; los enteros siempre son válidos."""
    if px <= 0:
        return False
    d = Decimal(repr(px)).normalize()
    if d == d.to_integral_value():
        return True
    exp = -d.as_tuple().exponent
    sig = len(d.as_tuple().digits)
    return exp <= _decimals_allowed(sz_decimals, spot) and sig <= MAX_SIG_FIGS


def _px_decimals(px: Decimal, sz_decimals: int, spot: bool) -> int:
    """Decimales permitidos para un precio de esta magnitud (5 cifras significativas y tope por szDecimals)."""
    if px >= 1:
        dec_sig = MAX_SIG_FIGS - len(str(int(px)))
    else:
        first = -int(math.floor(math.log10(float(px))))    # 0.000123 -> 4
        dec_sig = first + MAX_SIG_FIGS - 1
    return max(min(_decimals_allowed(sz_decimals, spot), dec_sig), 0)


def round_px(px: float, sz_decimals: int, spot: bool = True, side: str | None = None) -> float:
    """Redondea un precio a un valor aceptado por HL.

    side=None -> al más cercano; side='buy' -> hacia abajo; side='sell' -> hacia arriba
    (el lado pasivo, para que un post-only no cruce por el redondeo)."""
    if px <= 0:
        raise ValueError("precio no positivo")
    mode = {None: ROUND_HALF_EVEN, "buy": ROUND_FLOOR, "sell": ROUND_CEILING}[side]
    d = Decimal(repr(px))
    out = d.quantize(Decimal(1).scaleb(-_px_decimals(d, sz_decimals, spot)), rounding=mode)
    if out <= 0:
        raise ValueError(f"precio {px} se vuelve 0 al redondear")
    return float(out)


def round_sz(sz: float, sz_decimals: int) -> float:
    """Tamaño truncado (hacia cero) a szDecimals: nunca se pide más de lo disponible."""
    if sz < 0:
        raise ValueError("tamaño negativo")
    return float(Decimal(repr(sz)).quantize(Decimal(1).scaleb(-sz_decimals), rounding=ROUND_DOWN))


def tick_size(px: float, sz_decimals: int, spot: bool = True) -> float:
    """Paso de precio vigente alrededor de `px` (p. ej. 1.0 para BTC ~80,000 en spot)."""
    d = Decimal(repr(round_px(px, sz_decimals, spot)))
    return float(Decimal(1).scaleb(-_px_decimals(d, sz_decimals, spot)))


def cloid_for(date: str, mode: str, attempt: int, leg: int = 0) -> str:
    """cloid determinista (0x + 32 hex) por (fecha, modo, intento, tramo): reintentar no duplica órdenes."""
    h = hashlib.sha256(f"atlas|{date}|{mode}|{attempt}|{leg}".encode()).hexdigest()
    return "0x" + h[:32]
