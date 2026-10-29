"""
Ingenieria de features. Regla de oro: todo valor en la fila t usa SOLO informacion
disponible al cierre del dia t (00:00 UTC del dia t+1). Las fuentes externas se rezagan:
  - ETFs de EE. UU.: 1 dia (cierran 20:00-21:00 UTC; rezago extra por seguridad)
  - On-chain Coin Metrics: 2 dias (se publica al dia siguiente ~13:30 UTC)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HALVINGS = pd.to_datetime(["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20", "2028-04-15"])
CYCLE_DAYS = 1458.0


def _z(s: pd.Series, w: int) -> pd.Series:
    m = s.rolling(w, min_periods=w // 2).mean()
    sd = s.rolling(w, min_periods=w // 2).std()
    return (s - m) / sd.replace(0, np.nan)


def price_features(d: pd.DataFrame) -> pd.DataFrame:
    c = d["close"]
    lc = np.log(c)
    f = pd.DataFrame(index=d.index)
    for h in [1, 3, 7, 14, 30, 60, 90, 180, 365]:
        f[f"ret_{h}"] = lc.diff(h)
    for n in [20, 50, 100, 200]:
        f[f"dist_sma{n}"] = lc - np.log(c.rolling(n).mean())
    for n in [20, 55, 100]:
        hi, lo = d["high"].rolling(n).max(), d["low"].rolling(n).min()
        f[f"donch_pos{n}"] = (c - lo) / (hi - lo).replace(0, np.nan)
    ath = c.cummax()
    f["dd_ath"] = c / ath - 1
    f["dd_365"] = c / c.rolling(365, min_periods=60).max() - 1
    f["days_since_high365"] = (
        c.rolling(365, min_periods=60).apply(lambda x: len(x) - 1 - np.argmax(x), raw=True) / 365
    )
    f["sma50_slope"] = np.log(c.rolling(50).mean()).diff(10)
    f["sma200_slope"] = np.log(c.rolling(200).mean()).diff(20)
    return f


def vol_features(d: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=d.index)
    rv = d["rv"].clip(lower=1e-10)
    f["vol_7"] = np.sqrt(rv.rolling(7).mean() * 365)
    f["vol_30"] = np.sqrt(rv.rolling(30).mean() * 365)
    f["vol_90"] = np.sqrt(rv.rolling(90).mean() * 365)
    f["vol_ratio_7_90"] = f["vol_7"] / f["vol_90"]
    f["vol_z_365"] = _z(np.log(f["vol_30"]), 365)
    f["downside_share_14"] = d["rv_down"].rolling(14).sum() / rv.rolling(14).sum()
    f["rskew_14"] = d["rskew"].rolling(14).mean()
    pk = (np.log(d["high"] / d["low"]) ** 2) / (4 * np.log(2))
    f["park_vs_rv_14"] = np.log(pk.rolling(14).mean() / rv.rolling(14).mean())
    return f


def flow_features(d: pd.DataFrame) -> pd.DataFrame:
    """Volumen, flujo firmado y detector de ballenas (proxy OHLCV de 1m)."""
    f = pd.DataFrame(index=d.index)
    lv = np.log(d["volume"].clip(lower=1e-6))
    f["volume_z_90"] = _z(lv, 90)
    f["volume_trend"] = lv.rolling(7).mean() - lv.rolling(60).mean()
    for w in [3, 7, 30]:
        f[f"flow_{w}"] = d["flow_all"].rolling(w).sum() / d["volume"].rolling(w).sum()
        f[f"whale_flow_{w}"] = d["whale_flow"].rolling(w).sum() / d["whale_vol"].rolling(w).sum().replace(0, np.nan)
    f["whale_share_7"] = d["whale_vol"].rolling(7).sum() / d["volume"].rolling(7).sum()
    f["whale_n_z_90"] = _z(np.log1p(d["whale_n"]), 90)
    # divergencia: ballenas vs el resto del mercado (>0 = las ballenas compran mas que la masa)
    f["whale_div_7"] = f["whale_flow_7"] - f["flow_7"]
    return f


def cycle_features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    f = pd.DataFrame(index=idx)
    pos = np.searchsorted(HALVINGS.values, idx.values, side="right") - 1
    last = HALVINGS.values[np.clip(pos, 0, None)]
    days = (idx.values - last).astype("timedelta64[D]").astype(float)
    phase = days / CYCLE_DAYS
    f["halving_phase"] = phase
    f["halving_sin"] = np.sin(2 * np.pi * phase)
    f["halving_cos"] = np.cos(2 * np.pi * phase)
    return f


def macro_features(idx: pd.DatetimeIndex, mac: pd.DataFrame, btc_close: pd.Series) -> pd.DataFrame:
    m = mac.copy()
    lm = np.log(m)
    f = pd.DataFrame(index=m.index)
    f["spy_ret_20"] = lm["SPY"].diff(20)
    f["spy_ret_60"] = lm["SPY"].diff(60)
    f["qqq_ret_20"] = lm["QQQ"].diff(20)
    f["spy_dist_sma200"] = lm["SPY"] - np.log(m["SPY"].rolling(200).mean())
    f["tlt_ret_20"] = lm["TLT"].diff(20)
    f["gld_ret_20"] = lm["GLD"].diff(20)
    f["credit_20"] = lm["HYG"].diff(20) - lm["IEF"].diff(20)
    f["spy_vol_20"] = lm["SPY"].diff().rolling(20).std() * np.sqrt(252)
    # rezago de 1 dia y relleno hacia adelante para fines de semana (BTC opera 24/7)
    f = f.shift(1).reindex(idx, method="ffill")
    # correlacion BTC-SPY (60 dias habiles aprox.)
    spy_d = np.log(m["SPY"]).diff().shift(1).reindex(idx)
    btc_d = np.log(btc_close).diff()
    both = pd.concat([btc_d, spy_d], axis=1).dropna()
    corr = both.iloc[:, 0].rolling(60).corr(both.iloc[:, 1])
    f["btc_spy_corr_60"] = corr.reindex(idx, method="ffill")
    return f


def onchain_features(idx: pd.DatetimeIndex, oc: pd.DataFrame) -> pd.DataFrame:
    o = oc.copy()
    f = pd.DataFrame(index=o.index)
    f["oc_mvrv"] = o["CapMVRVCur"]
    f["oc_mvrv_z"] = _z(o["CapMVRVCur"], 730)
    net = o["FlowInExNtv"] - o["FlowOutExNtv"]
    f["oc_exnet_7"] = net.rolling(7).sum() / o["SplyCur"] * 1e4
    f["oc_exnet_30"] = net.rolling(30).sum() / o["SplyCur"] * 1e4
    f["oc_sply_ex_chg_30"] = np.log(o["SplyExNtv"]).diff(30)
    f["oc_adr_growth_30"] = np.log(o["AdrActCnt"].rolling(7).mean()).diff(30)
    f["oc_hash_ribbon"] = np.log(o["HashRate"].rolling(30).mean() / o["HashRate"].rolling(60).mean())
    stable = o["sply_usdc"].fillna(0) + o["sply_usdt"].fillna(0)
    f["oc_stable_growth_30"] = np.log(stable.replace(0, np.nan)).diff(30)
    f["oc_fee_z"] = _z(np.log1p(o["FeeTotNtv"]), 365)
    return f.shift(2).reindex(idx)  # rezago de publicacion


def build_feature_matrix(d: pd.DataFrame, mac: pd.DataFrame | None = None,
                         oc: pd.DataFrame | None = None) -> pd.DataFrame:
    parts = [price_features(d), vol_features(d), flow_features(d), cycle_features(d.index)]
    if mac is not None:
        parts.append(macro_features(d.index, mac, d["close"]))
    if oc is not None:
        parts.append(onchain_features(d.index, oc))
    X = pd.concat(parts, axis=1)
    return X.replace([np.inf, -np.inf], np.nan)


def make_labels(close: pd.Series, horizon: int) -> pd.DataFrame:
    """Retorno futuro log de t a t+h (la posicion se abre al cierre de t / apertura de t+1)."""
    fwd = np.log(close.shift(-horizon) / close)
    return pd.DataFrame({"fwd_ret": fwd, "y": (fwd > 0).astype(float).where(fwd.notna())})
