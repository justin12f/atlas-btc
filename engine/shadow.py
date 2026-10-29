"""
Senales en SOMBRA: se calculan y registran cada dia, pero NO cambian la exposicion de Atlas.

Filtro de stablecoins (preregistro_v3, factor S): exposicion x 0.5 si la oferta de USDT + USDC
cae en 30 dias (rezago 2 dias). En el grid mejoro 88% de los pares pero no paso las pruebas de
suerte; se evalua con datos en vivo el 2027-10-25 con el mismo criterio.

Fuente en vivo: API publica de stablecoins de DefiLlama (sin llave). Fuente historica: Coin Metrics.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "live" / "shadow_stablecoins.csv"
LLAMA = "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={sid}"
IDS = {"USDT": 1, "USDC": 2}          # identificadores de DefiLlama


def fetch_supply_llama() -> pd.Series:
    """Oferta diaria total USDT + USDC (USD) desde DefiLlama."""
    import requests
    total = None
    for name, sid in IDS.items():
        js = requests.get(LLAMA.format(sid=sid), timeout=30).json()
        s = pd.Series({pd.to_datetime(int(r["date"]), unit="s"): float(r["totalCirculating"]["peggedUSD"])
                       for r in js}, name=name).sort_index()
        total = s if total is None else total.add(s, fill_value=0)
    return total


def supply_from_coinmetrics(oc: pd.DataFrame) -> pd.Series:
    return oc["sply_usdc"].fillna(0) + oc["sply_usdt"].fillna(0)


def stablecoin_signal(supply: pd.Series) -> pd.DataFrame:
    s = supply.replace(0, np.nan)
    g = np.log(s).diff(30).shift(2)
    mult = pd.Series(np.where(g < 0, 0.5, 1.0), index=s.index).where(g.notna(), 1.0)
    return pd.DataFrame({"supply": supply, "growth_30d": g, "shadow_mult": mult})


def log_today(supply: pd.Series, atlas_target: float, date: str) -> dict:
    sig = stablecoin_signal(supply).dropna(subset=["growth_30d"]).iloc[-1]
    row = {"date": date, "atlas_target": atlas_target, "stablecoin_growth_30d": float(sig["growth_30d"]),
           "shadow_mult": float(sig["shadow_mult"]), "shadow_target": atlas_target * float(sig["shadow_mult"])}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(LOG, mode="a", header=not LOG.exists(), index=False)
    return row
