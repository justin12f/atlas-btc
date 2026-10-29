"""
Atlas v3 — implementacion de las ideas pre-registradas en config/preregistro_v3.yaml.

Cada idea es un factor con versiones; `build_variant` arma la exposicion objetivo por activo
y `simulate_multi` corre el portafolio (BTC y opcionalmente ETH) con costos, banda y
control de caida Grossman-Zhou. Todo usa solo informacion disponible al cierre de t.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.stats import norm

from .features import CYCLE_DAYS, cycle_features
from .portfolio import PortfolioConfig, simulate
from .signals import donchian_state

FEE = 0.0010 + 0.0003
BAND = 0.10
TARGET_VOL = 0.40
START, END = "2018-01-01", "2026-10-22"


# ----------------------------------------------------------------------------- senales de tendencia
def trend_T0(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    c = close
    s = pd.DataFrame(index=c.index)
    s["sma50"] = c > c.rolling(50).mean()
    s["sma100"] = c > c.rolling(100).mean()
    s["sma200"] = c > c.rolling(200).mean()
    s["mom30"] = c > c.shift(30)
    s["mom90"] = c > c.shift(90)
    s["mom180"] = c > c.shift(180)
    s["donch"] = donchian_state(pd.DataFrame({"close": c, "high": high, "low": low}), 55, 20).astype(bool)
    s["ema"] = c.ewm(span=20).mean() > c.ewm(span=100).mean()
    out = s.astype(float).mean(axis=1)
    out.iloc[:200] = np.nan
    return out


def trend_T1(close: pd.Series) -> pd.Series:
    c = close
    s = pd.DataFrame({
        "sma100": c > c.rolling(100).mean(),
        "sma200": c > c.rolling(200).mean(),
        "mom90": c > c.shift(90),
        "mom180": c > c.shift(180),
        "mom365": c > c.shift(365),
    }).astype(float)
    out = s.mean(axis=1)
    out.iloc[:365] = np.nan
    return out


def trend_T2(close: pd.Series) -> pd.Series:
    lc = np.log(close)
    r = lc.diff()
    sig = np.sqrt((r ** 2).ewm(span=60, min_periods=30).mean())
    comps = []
    for L in (30, 90, 180, 365):
        z = (lc - lc.shift(L)) / (sig * np.sqrt(L))
        comps.append(pd.Series(norm.cdf(z), index=close.index).where(z.notna()))
    out = pd.concat(comps, axis=1).mean(axis=1, skipna=False)
    return out


# ----------------------------------------------------------------------------- volatilidad
def vol_ewma(rv: pd.Series) -> pd.Series:
    rv = rv.clip(lower=1e-10)
    ew = rv.ewm(halflife=10, min_periods=10).mean()
    return np.sqrt(0.5 * ew + 0.5 * rv.rolling(30, min_periods=10).mean()) * np.sqrt(365)


def vol_har(rv: pd.Series, refit_start: str = "2015-01-01") -> pd.Series:
    """HAR en logs, reajuste cada 1 de enero con datos cuya etiqueta ya es conocida."""
    rv = rv.clip(lower=1e-10)
    X = pd.DataFrame({
        "d": np.log(rv),
        "w": np.log(rv.rolling(7).mean()),
        "m": np.log(rv.rolling(30).mean()),
    })
    y = np.log(rv[::-1].rolling(7).mean()[::-1].shift(-1))   # media de t+1..t+7
    out = pd.Series(np.nan, index=rv.index)
    years = range(pd.Timestamp(refit_start).year, rv.index.max().year + 1)
    for yr in years:
        R = pd.Timestamp(f"{yr}-01-01")
        tr = (X.index < R - pd.Timedelta(days=8)) & X.notna().all(axis=1) & y.notna()
        if tr.sum() < 365:
            continue
        A = np.column_stack([np.ones(tr.sum()), X[tr].to_numpy()])
        beta, *_ = np.linalg.lstsq(A, y[tr].to_numpy(), rcond=None)
        resid = y[tr].to_numpy() - A @ beta
        s2 = resid.var()
        te = (X.index >= R) & (X.index < pd.Timestamp(f"{yr + 1}-01-01")) & X.notna().all(axis=1)
        pred = np.column_stack([np.ones(te.sum()), X[te].to_numpy()]) @ beta
        out[te] = np.sqrt(np.exp(pred + 0.5 * s2) * 365)
    return out


# ----------------------------------------------------------------------------- multiplicadores
def cycle_mult(idx: pd.DatetimeIndex) -> pd.Series:
    days = cycle_features(idx)["halving_phase"] * CYCLE_DAYS
    return pd.Series(np.where((days >= 540) & (days < 900), 0.5, 1.0), index=idx)


def macro_mult(idx: pd.DatetimeIndex, spy: pd.Series) -> pd.Series:
    ok = (spy > spy.rolling(200).mean()).where(spy.rolling(200).mean().notna())
    ok = ok.shift(1).reindex(idx, method="ffill")
    return pd.Series(np.where(ok == 0, 0.5, 1.0), index=idx)


def stable_mult(idx: pd.DatetimeIndex, oc: pd.DataFrame) -> pd.Series:
    st = oc["sply_usdc"].fillna(0) + oc["sply_usdt"].fillna(0)
    g = np.log(st.replace(0, np.nan)).diff(30).shift(2).reindex(idx)
    return pd.Series(np.where(g < 0, 0.5, 1.0), index=idx)   # NaN -> neutral


# ----------------------------------------------------------------------------- datos
@dataclass
class Asset:
    name: str
    close: pd.Series
    high: pd.Series
    low: pd.Series
    rv: pd.Series          # varianza diaria realizada (BTC: 5 min; ETH: retorno diario al cuadrado)
    last_date: pd.Timestamp


def load_assets(root) -> dict[str, Asset]:
    d = pd.read_parquet(root / "data/processed/btc_daily.parquet").loc[:END]
    btc = Asset("BTC", d["close"], d["high"], d["low"], d["rv"], d.index.max())
    e = pd.read_csv(root / "data/raw/cm_eth.csv", usecols=["time", "PriceUSD"], parse_dates=["time"])
    e = e.set_index("time")["PriceUSD"].dropna()
    e = e.reindex(d.index)                       # mismo calendario diario
    last = e.last_valid_index()
    e = e.ffill()
    r = np.log(e).diff()
    eth = Asset("ETH", e, e, e, r ** 2, last)
    return {"BTC": btc, "ETH": eth}


# ----------------------------------------------------------------------------- simulador multi-activo
def simulate_multi(targets: dict[str, pd.Series], rets: dict[str, pd.Series], gz: bool,
                   start: str = START, end: str = END) -> pd.DataFrame:
    names = list(targets)
    idx = targets[names[0]].loc[start:end].index
    T = np.column_stack([targets[n].reindex(idx).fillna(0).to_numpy() for n in names])
    R = np.column_stack([rets[n].reindex(idx).fillna(0).to_numpy() for n in names])
    n_days, k = T.shape
    w = np.zeros(k)
    eq, peak = 1.0, 1.0
    out_r = np.zeros(n_days)
    out_w = np.zeros(n_days)
    out_turn = np.zeros(n_days)
    for i in range(n_days - 1):
        mult = 1.0
        if gz:
            mult = min(1.0, max(0.0, (eq / peak - 0.70) / 0.30))
        tgt = T[i] * mult
        tot = tgt.sum()
        if tot > 1.0:
            tgt = tgt / tot
        trade = np.zeros(k)
        for j in range(k):
            if abs(tgt[j] - w[j]) >= BAND or (tgt[j] < 0.02 <= w[j]) or (tgt[j] > 0.98 and w[j] <= 0.98 and abs(tgt[j] - w[j]) > 0.02):
                trade[j] = tgt[j] - w[j]
                w[j] = tgt[j]
        cost = np.abs(trade).sum() * FEE
        rr = R[i + 1]
        pr = float(w @ rr) - cost
        eq *= 1 + pr
        peak = max(peak, eq)
        out_r[i + 1] = pr
        out_w[i + 1] = w.sum()
        out_turn[i + 1] = np.abs(trade).sum()
        gross = 1 + pr + cost
        w = w * (1 + rr) / gross if gross != 0 else w
    df = pd.DataFrame({"ret": out_r, "weight": out_w, "turnover": out_turn}, index=idx).iloc[1:]
    return df


# ----------------------------------------------------------------------------- armado de una variante
class VariantBuilder:
    def __init__(self, root, ml_pred: pd.DataFrame):
        self.root = root
        self.assets = load_assets(root)
        mac = pd.read_parquet(root / "data/processed/macro_etfs.parquet")
        oc = pd.read_parquet(root / "data/processed/onchain.parquet")
        idx = self.assets["BTC"].close.index
        self.idx = idx
        self.mults = {"C": cycle_mult(idx), "M": macro_mult(idx, mac["SPY"]), "S": stable_mult(idx, oc)}
        self.rets = {k: a.close.pct_change() for k, a in self.assets.items()}
        # ETH sin datos despues de su ultima fecha: retorno 0 y exposicion 0 (USDC)
        eth_last = self.assets["ETH"].last_date
        self.rets["ETH"] = self.rets["ETH"].where(self.rets["ETH"].index <= eth_last, 0.0)
        self.ml = ml_pred.reindex(idx)
        self._trend, self._vol = {}, {}

    def trend(self, asset: str, T: str) -> pd.Series:
        key = (asset, T)
        if key not in self._trend:
            a = self.assets[asset]
            self._trend[key] = {"T0": lambda: trend_T0(a.close, a.high, a.low),
                                "T1": lambda: trend_T1(a.close),
                                "T2": lambda: trend_T2(a.close)}[T]()
        return self._trend[key]

    def vol(self, asset: str, V: str) -> pd.Series:
        key = (asset, V)
        if key not in self._vol:
            a = self.assets[asset]
            self._vol[key] = vol_ewma(a.rv) if V == "ewma" else vol_har(a.rv)
        return self._vol[key]

    def multiplier(self, C: str, M: str, S: str) -> pd.Series:
        m = pd.Series(1.0, index=self.idx)
        if C == "on":
            m = m * self.mults["C"]
        if M == "on":
            m = m * self.mults["M"]
        if S == "on":
            m = m * self.mults["S"]
        return m

    def asset_target(self, asset: str, T: str, V: str, C: str, M: str, S: str, use_ml: bool = False) -> pd.Series:
        score = self.trend(asset, T)
        if use_ml:
            gate = self.ml["gate"].fillna(0.0)
            s_ml = (0.5 + (self.ml["p_ml"].fillna(0.5) - 0.5) * 4.0).clip(0, 1)
            score = pd.Series(np.where(gate > 0, 0.5 * score + 0.5 * s_ml, score), index=self.idx).where(score.notna())
        vs = (TARGET_VOL / self.vol(asset, V)).clip(upper=1.0)
        tgt = (score * self.multiplier(C, M, S) * vs).clip(0, 1)
        if asset == "ETH":
            tgt = tgt.where(tgt.index < self.assets["ETH"].last_date, 0.0)
        return tgt

    @lru_cache(maxsize=None)
    def promotion_weight(self, T: str, V: str, C: str, M: str, S: str) -> pd.Series:
        """Promocion del ML (igual que Atlas v2): papel nucleo vs nucleo+ML, decision mensual."""
        core = self.asset_target("BTC", T, V, C, M, S, False)
        withml = self.asset_target("BTC", T, V, C, M, S, True)
        d = pd.DataFrame({"close": self.assets["BTC"].close})
        cfg = PortfolioConfig(band=BAND, fee=0.0010, slippage=0.0003, dd_trigger=9.0)
        s_core = simulate(d, core.fillna(0), cfg, START)["ret"]
        s_ml = simulate(d, withml.fillna(0), cfg, START)["ret"]
        edge = (s_ml - s_core).rolling(365, min_periods=182).mean() / s_core.rolling(365, min_periods=182).std()
        monthly = (edge.resample("ME").last() > 0.02).astype(float)
        w = monthly.reindex(pd.date_range(monthly.index.min(), self.idx.max()), method="ffill").shift(1)
        return w.reindex(self.idx).fillna(0.0)

    def build(self, v: dict) -> dict[str, pd.Series]:
        T, V, C, M, S = v["T"], v["V"], v["C"], v["M"], v["S"]
        btc = self.asset_target("BTC", T, V, C, M, S, False)
        if v["P"] == "on":
            w = self.promotion_weight(T, V, C, M, S)
            btc_ml = self.asset_target("BTC", T, V, C, M, S, True)
            btc = btc * (1 - w) + btc_ml.fillna(btc) * w
        if v["E"] == "on":
            eth = self.asset_target("ETH", T, V, C, M, S, False)
            return {"BTC": 0.5 * btc, "ETH": 0.5 * eth}
        return {"BTC": btc}

    def run(self, v: dict) -> pd.DataFrame:
        tg = self.build(v)
        return simulate_multi(tg, {k: self.rets[k] for k in tg}, gz=(v["D"] == "gz"))
