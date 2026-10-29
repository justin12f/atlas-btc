import random

import pytest

from atlas.hl.markets import (MarketNotFound, cloid_for, is_valid_px, market_fingerprint, resolve_spot,
                              round_px, round_sz, tick_size)
from tests.conftest import load_fx


def test_resolve_ubtc_mainnet():
    m = resolve_spot(load_fx("spot_meta_mainnet"), "UBTC/USDC")
    assert m.coin.startswith("@") and m.coin == f"@{m.index}"
    assert m.asset_id == 10000 + m.index
    assert m.sz_decimals == 5           # verificado 2026-10-29 (@142)
    assert m.base == "UBTC" and m.quote == "USDC"


def test_testnet_has_no_ubtc_but_has_pobtc():
    sm = load_fx("spot_meta_testnet")
    with pytest.raises(MarketNotFound):
        resolve_spot(sm, "UBTC/USDC")
    m = resolve_spot(sm, "POBTC/USDC")
    assert m.sz_decimals == 5


def test_fingerprint_changes_with_szdecimals():
    sm = load_fx("spot_meta_mainnet")
    m = resolve_spot(sm, "UBTC/USDC")
    for t in sm["tokens"]:
        if t["name"] == "UBTC":
            t["szDecimals"] = 4
    assert market_fingerprint(resolve_spot(sm, "UBTC/USDC")) != market_fingerprint(m)


@pytest.mark.parametrize("px,szd,expected", [
    (80379.4, 5, 80379.0), (80379.6, 5, 80380.0), (123456.7, 5, 123457.0),   # enteros siempre válidos
    (1234.567, 5, 1234.6), (1234.567, 0, 1234.6), (0.0001234567, 0, 0.00012346),
    (0.0001234567, 2, 0.000123), (3.14159, 5, 3.142), (3.14159, 3, 3.1416),
])
def test_round_px_cases(px, szd, expected):
    assert round_px(px, szd) == pytest.approx(expected, abs=1e-12)
    assert is_valid_px(round_px(px, szd), szd)


def test_round_px_passive_side():
    assert round_px(80379.9, 5, side="buy") == 80379.0
    assert round_px(80379.1, 5, side="sell") == 80380.0


def test_round_px_matches_sdk_rule_below_1e5():
    rng = random.Random(0)
    for _ in range(2000):
        px, szd = 10 ** rng.uniform(-4, 4.99), rng.randint(0, 6)
        sdk = round(float(f"{px:.5g}"), 8 - szd)
        if sdk == 0:                     # no representable: debemos fallar en vez de mandar precio 0
            with pytest.raises(ValueError):
                round_px(px, szd)
            continue
        ours = round_px(px, szd)
        # el SDK redondea dos veces (5 cifras y luego decimales): puede diferir en 1 tick; ambos son válidos
        assert abs(ours - sdk) <= tick_size(ours, szd) * 1.000001
        assert is_valid_px(ours, szd)


def test_invalid_px():
    assert not is_valid_px(80379.5, 5)          # 6 cifras significativas
    assert not is_valid_px(0.123456, 3)         # 6 decimales > 8-3
    assert is_valid_px(80379.0, 5)


def test_round_sz_truncates():
    assert round_sz(0.123456789, 5) == 0.12345
    assert round_sz(0.000009, 5) == 0.0
    with pytest.raises(ValueError):
        round_sz(-1, 5)


def test_tick_size():
    assert tick_size(80379.0, 5) == 1.0
    assert tick_size(1234.5, 5) == 0.1


def test_cloid_deterministic_and_valid():
    a = cloid_for("2026-10-28", "paper", 0)
    assert a == cloid_for("2026-10-28", "paper", 0)
    assert a != cloid_for("2026-10-28", "paper", 1) != cloid_for("2026-10-28", "testnet", 0)
    assert a.startswith("0x") and len(a) == 34
    from hyperliquid.utils.types import Cloid
    Cloid(a)  # el SDK lo acepta
