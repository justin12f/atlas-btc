"""
Capa de datos.

Fuentes usadas en el backtest (todas publicas):
  - BTC/USD 1-minuto de Bitstamp (2012 -> hoy)  github.com/ff137/bitstamp-btcusd-minute-data
    Proxy de BTC/USDC de Binance: la base BTC/USD vs BTC/USDC es de unos pocos pb.
  - On-chain diario de Coin Metrics (community)  github.com/coinmetrics/data
    OJO: el repo dejo de actualizarse el 2026-05-24 -> se usa solo en investigacion.
  - Acciones/ETFs ajustados por splits y dividendos, con membresia historica del S&P 500
    (sin sesgo de supervivencia)  github.com/Johnbrick123/sp500-data

En produccion (tu laptop), `BinanceConnector` reemplaza a Bitstamp con klines de BTCUSDC.
"""
from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"


# ----------------------------------------------------------------------------------------
# BTC minuto -> diario (+ features intradia que solo se pueden calcular con datos de 1m)
# ----------------------------------------------------------------------------------------
def _read_minutes() -> pd.DataFrame:
    hist = pd.read_csv(RAW / "btc_bitstamp_hist.csv.gz", compression="gzip")
    latest = pd.read_csv(RAW / "btc_bitstamp_latest.csv")
    df = pd.concat([hist, latest], ignore_index=True)
    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_localize(None)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype("float64")


def build_btc_daily(start: str = "2013-06-01") -> pd.DataFrame:
    """Barras diarias UTC + microestructura derivada de velas de 1 minuto.

    Detector de "ballenas" (proxy con OHLCV): minutos cuyo volumen supera el percentil 99
    de los 30 dias PREVIOS (umbral causal). Su volumen se firma con la posicion del cierre
    dentro del rango de la vela (CLV in [-1, 1]): >0 = presion compradora.
    """
    m = _read_minutes()
    m = m.loc[start:]
    day = m.index.floor("D")

    # --- OHLCV diario
    g = m.groupby(day)
    d = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
    })

    # --- volatilidad realizada con retornos de 5 minutos
    c5 = m["close"].resample("5min").last().dropna()
    r5 = np.log(c5).diff().dropna()
    d5 = r5.index.floor("D")
    d["rv"] = (r5 ** 2).groupby(d5).sum()                        # varianza realizada diaria
    d["rv_down"] = (r5.clip(upper=0) ** 2).groupby(d5).sum()      # semivarianza negativa
    d["rskew"] = (r5 ** 3).groupby(d5).sum() / (d["rv"] ** 1.5 + 1e-18)
    d["n_min_zero_vol"] = (m["volume"] == 0).groupby(day).sum()

    # --- flujo firmado (CLV) y "ballenas"
    rng = (m["high"] - m["low"]).replace(0, np.nan)
    clv = (((m["close"] - m["low"]) - (m["high"] - m["close"])) / rng).fillna(0.0)
    sv = m["volume"] * clv
    d["flow_all"] = sv.groupby(day).sum()

    p99 = m["volume"].groupby(day).quantile(0.99)
    thr = p99.rolling(30, min_periods=10).median().shift(1)          # solo pasado
    thr_min = thr.reindex(day).to_numpy()
    big = m["volume"].to_numpy() > np.nan_to_num(thr_min, nan=np.inf)
    d["whale_vol"] = pd.Series(np.where(big, m["volume"], 0.0), index=m.index).groupby(day).sum()
    d["whale_flow"] = pd.Series(np.where(big, sv, 0.0), index=m.index).groupby(day).sum()
    d["whale_n"] = pd.Series(big.astype(float), index=m.index).groupby(day).sum()

    d.index.name = "date"
    d = d[d["close"].notna()]
    # ultimo dia incompleto fuera
    last_min = m.index.max()
    if last_min.floor("D") == d.index.max() and last_min.hour < 23:
        d = d.iloc[:-1]
    return d


# ----------------------------------------------------------------------------------------
# On-chain (investigacion; datos hasta 2026-05-24)
# ----------------------------------------------------------------------------------------
def build_onchain() -> pd.DataFrame:
    btc = pd.read_csv(RAW / "cm_btc.csv", parse_dates=["time"]).set_index("time")
    cols = ["AdrActCnt", "CapMVRVCur", "FlowInExNtv", "FlowOutExNtv", "SplyExNtv",
            "HashRate", "TxTfrCnt", "SplyCur", "FeeTotNtv"]
    oc = btc[cols].astype(float)
    for coin in ["usdc", "usdt"]:
        s = pd.read_csv(RAW / f"cm_{coin}.csv", parse_dates=["time"]).set_index("time")
        oc[f"sply_{coin}"] = s["SplyCur"].astype(float)
    oc.index.name = "date"
    return oc


# ----------------------------------------------------------------------------------------
# Acciones / ETFs (benchmarks + features macro)
# ----------------------------------------------------------------------------------------
def load_equity_prices(tickers: list[str] | None = None, start: str = "2010-01-01") -> pd.DataFrame:
    import duckdb
    con = duckdb.connect()
    where = f"date >= DATE '{start}'"
    if tickers:
        tl = ",".join(f"'{t}'" for t in tickers)
        where += f" AND ticker IN ({tl})"
    px = con.sql(f"""SELECT date, ticker, adj_close FROM read_parquet('{RAW / "sp500_prices.parquet"}')
                    WHERE {where}""").df()
    px["date"] = pd.to_datetime(px["date"])
    return px.pivot(index="date", columns="ticker", values="adj_close").sort_index()


def load_sp500_members() -> pd.DataFrame:
    import duckdb
    con = duckdb.connect()
    mem = con.sql(f"SELECT date, ticker FROM read_parquet('{RAW / 'sp500_members.parquet'}')").df()
    mem["date"] = pd.to_datetime(mem["date"])
    return mem


# ----------------------------------------------------------------------------------------
def build_all() -> None:
    PROC.mkdir(parents=True, exist_ok=True)
    print("BTC diario + microestructura ...")
    d = build_btc_daily()
    d.to_parquet(PROC / "btc_daily.parquet")
    print(f"  {d.index.min().date()} -> {d.index.max().date()}  ({len(d)} dias)")
    print("On-chain ...")
    oc = build_onchain()
    oc.to_parquet(PROC / "onchain.parquet")
    print(f"  {oc.index.min().date()} -> {oc.index.max().date()}")
    print("Macro ETFs ...")
    mac = load_equity_prices(["SPY", "QQQ", "TLT", "GLD", "HYG", "IEF", "IWF", "XLK"], start="2012-01-01")
    mac.to_parquet(PROC / "macro_etfs.parquet")
    print(f"  {mac.index.min().date()} -> {mac.index.max().date()}")


if __name__ == "__main__":
    build_all()
