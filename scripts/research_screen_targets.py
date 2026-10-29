"""Investigacion: que tan predecible es cada objetivo (walk-forward anual, LightGBM regularizado).
Solo periodo de desarrollo (2018-01 -> 2025-10). Sirve para decidir que debe predecir la capa ML."""
import sys, numpy as np, pandas as pd, lightgbm as lgb
from pathlib import Path
from sklearn.metrics import roc_auc_score, r2_score
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.features import build_feature_matrix
from engine.regime import RegimeHMM, regime_obs

d = pd.read_parquet('data/processed/btc_daily.parquet'); mac = pd.read_parquet('data/processed/macro_etfs.parquet')
X = build_feature_matrix(d, mac)
c, lo = d['close'], d['low']
lc = np.log(c)
sig = np.sqrt(d['rv'].rolling(30).mean())  # vol diaria
T = {}
T['dir7'] = (lc.shift(-7) - lc > 0)
T['dir28'] = (lc.shift(-28) - lc > 0)
f7 = lc.shift(-7) - lc
T['down7_1sig'] = f7 < -1.0 * sig * np.sqrt(7)
T['up7_1sig'] = f7 > 1.0 * sig * np.sqrt(7)
minlow10 = pd.concat([lo.shift(-k) for k in range(1, 11)], axis=1).min(axis=1)
T['crash10_10pct'] = (minlow10 / c - 1) < -0.10
# triple barrier 10d +-1.5 sigma
H = 10; up = np.exp(1.5 * sig * np.sqrt(H)); dn = np.exp(-1.5 * sig * np.sqrt(H))
hi_ = d['high']; tb = pd.Series(np.nan, index=d.index)
cv, hv, lv, uv, dv = c.values, hi_.values, lo.values, up.values, dn.values
for i in range(len(d) - H):
    for k in range(1, H + 1):
        if hv[i + k] >= cv[i] * uv[i]: tb.iat[i] = 1; break
        if lv[i + k] <= cv[i] * dv[i]: tb.iat[i] = 0; break
T['tb10'] = tb
fvol = np.log(np.sqrt(d['rv'].shift(-1).rolling(7).sum().shift(-6) / 7 * 365))
for k in T:
    T[k] = T[k].astype(float).where(lc.shift(-28).notna() if k == 'dir28' else lc.shift(-10).notna())
    if k == 'tb10': T[k] = tb
obs = regime_obs(d)
params = dict(n_estimators=200, learning_rate=0.02, num_leaves=6, max_depth=3, min_child_samples=150,
              subsample=0.7, subsample_freq=1, colsample_bytree=0.5, reg_lambda=5, verbose=-1, n_jobs=2)
years = range(2018, 2026)
res = {}
preds = {k: [] for k in list(T) + ['fvol']}
for yr in years:
    R = pd.Timestamp(f'{yr}-01-01'); E = pd.Timestamp(f'{yr+1}-01-01') if yr < 2025 else pd.Timestamp('2025-10-22')
    hmm = RegimeHMM().fit(obs.loc['2014-06-01':R - pd.Timedelta(days=1)])
    Xf = X.join(hmm.filter_proba(obs.loc['2014-06-01':]))
    for k, y in list(T.items()) + [('fvol', fvol)]:
        purge = 30
        tr = (Xf.index >= '2014-06-01') & (Xf.index < R - pd.Timedelta(days=purge)) & y.notna()
        te = (Xf.index >= R) & (Xf.index < E) & y.notna()
        if k == 'fvol':
            m = lgb.LGBMRegressor(**params).fit(Xf[tr], y[tr]); p = m.predict(Xf[te])
        else:
            m = lgb.LGBMClassifier(**params).fit(Xf[tr], y[tr].astype(int)); p = m.predict_proba(Xf[te])[:, 1]
        preds[k].append(pd.Series(p, index=Xf.index[te]))
for k in preds:
    p = pd.concat(preds[k]); y = (fvol if k == 'fvol' else T[k]).reindex(p.index)
    if k == 'fvol':
        base = np.log(np.sqrt(d['rv'].ewm(halflife=10).mean() * 365)).reindex(p.index)
        print(f"{k:16s} R2 LGBM={r2_score(y, p):.3f}  R2 EWMA-baseline={r2_score(y, base):.3f}  corr={np.corrcoef(y,p)[0,1]:.3f}")
    else:
        aucs = [roc_auc_score(y[str(yr)], p[str(yr)]) for yr in years if y[str(yr)].nunique() > 1]
        print(f"{k:16s} AUC={roc_auc_score(y, p):.3f}  base_rate={y.mean():.2f}  AUC por anio min/med/max={min(aucs):.2f}/{np.median(aucs):.2f}/{max(aucs):.2f}")
    p.to_frame('p').to_parquet(f'results/screen_{k}.parquet')
