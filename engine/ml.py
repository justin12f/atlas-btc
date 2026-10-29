"""
Capa de ML: AutoML walk-forward con "campeon / retador" y compuerta de calidad.

Cada `retrain_days` dias (trimestral por defecto):
  1. Ajusta el HMM de regimen SOLO con el pasado y agrega P(regimen) como features.
  2. Arma el set de entrenamiento con filas cuya etiqueta ya se conoce (purga de h dias).
  3. Validacion cruzada purgada (walk-forward interno: ultimos 3 anios en bloques de 1 anio).
  4. Compiten: LightGBM regularizado fijo, LightGBM afinado con Optuna (espacio acotado),
     ExtraTrees y Regresion Logistica. Se elige por AUC promedio en CV (robusto a cambios de
     tasa base entre anios alcistas y bajistas).
  5. Compuerta: si el campeon no supera AUC > `gate_auc` en CV, la capa ML queda NEUTRAL
     (p = 0.5) ese trimestre -> "reemplazar el modelo cuando deje de rendir", de forma causal.

Leccion de la investigacion (ver README): con ~10 anios de datos diarios, un Optuna con
espacio amplio sobreajusta (AUC OOS 0.49). Por eso el espacio de busqueda esta acotado a
modelos pequenos y muy regularizados.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import QuantileTransformer

from .regime import RegimeHMM, regime_obs

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

LGB_FIXED = dict(n_estimators=200, learning_rate=0.02, num_leaves=6, max_depth=3, min_child_samples=150,
                 subsample=0.7, subsample_freq=1, colsample_bytree=0.5, reg_lambda=5.0)


@dataclass
class MLConfig:
    horizon: int = 28
    target: str = "dir"            # "dir": retorno a h dias > 0 ; "crash": minimo en h dias < -crash_pct
    crash_pct: float = 0.10
    train_start: str = "2014-06-01"
    oos_start: str = "2018-01-01"
    retrain_days: int = 91
    cv_folds: int = 3
    cv_block_days: int = 365
    n_trials: int = 8
    automl: bool = True            # False -> solo LightGBM regularizado fijo (rapido, para ablaciones)
    gate_auc: float = 0.52
    use_hmm: bool = True
    features: list | None = None   # subconjunto de columnas (None = todas)
    seed: int = 42


@dataclass
class RetrainLog:
    date: pd.Timestamp
    champion: str
    cv_auc: dict
    gate_on: bool
    n_train: int
    top_features: list = field(default_factory=list)


def make_target(d: pd.DataFrame, cfg: MLConfig) -> pd.Series:
    lc = np.log(d["close"])
    known = lc.shift(-cfg.horizon).notna()
    if cfg.target == "dir":
        y = (lc.shift(-cfg.horizon) - lc > 0).astype(float)
    elif cfg.target == "crash":
        lows = pd.concat([d["low"].shift(-k) for k in range(1, cfg.horizon + 1)], axis=1).min(axis=1)
        y = ((lows / d["close"] - 1) < -cfg.crash_pct).astype(float)
    else:
        raise ValueError(cfg.target)
    return y.where(known)


# ------------------------------------------------------------------ candidatos
def _lgb(params: dict, seed: int):
    return lgb.LGBMClassifier(objective="binary", verbose=-1, random_state=seed, n_jobs=2, **params)


def _logit():
    return make_pipeline(SimpleImputer(strategy="median"),
                         QuantileTransformer(n_quantiles=200, output_distribution="normal"),
                         LogisticRegression(C=0.01, max_iter=3000))


def _etrees(seed: int):
    return make_pipeline(SimpleImputer(strategy="median"),
                         ExtraTreesClassifier(n_estimators=300, min_samples_leaf=100, max_features=0.3,
                                              n_jobs=2, random_state=seed))


def _purged_folds(dates: pd.DatetimeIndex, cfg: MLConfig):
    end = dates.max()
    folds = []
    for k in range(cfg.cv_folds, 0, -1):
        v_start = end - pd.Timedelta(days=cfg.cv_block_days * k)
        v_end = v_start + pd.Timedelta(days=cfg.cv_block_days)
        tr = dates < (v_start - pd.Timedelta(days=cfg.horizon))
        va = (dates >= v_start) & (dates < v_end - pd.Timedelta(days=cfg.horizon))
        folds.append((tr, va))
    return [(tr, va) for tr, va in folds if tr.sum() > 500 and va.sum() > 100]


def _cv_auc(make_model, X, y, folds) -> float:
    aucs = []
    for tr, va in folds:
        if y[va].nunique() < 2:
            continue
        m = make_model().fit(X[tr], y[tr])
        aucs.append(roc_auc_score(y[va], m.predict_proba(X[va])[:, 1]))
    return float(np.mean(aucs)) if aucs else 0.5


# ------------------------------------------------------------------ walk-forward
def walk_forward(d: pd.DataFrame, X: pd.DataFrame, cfg: MLConfig, end: str | None = None,
                 verbose: bool = True):
    y_all = make_target(d, cfg)
    if cfg.features is not None:
        X = X[cfg.features]
    end_ts = pd.Timestamp(end) if end else X.index.max()
    retrain_dates = pd.date_range(cfg.oos_start, end_ts, freq=f"{cfg.retrain_days}D")
    obs = regime_obs(d)
    out_idx = X.loc[cfg.oos_start:end_ts].index
    out = pd.DataFrame(index=out_idx, columns=["p_ml", "p_raw", "gate", "p_bear", "p_side", "p_bull"], dtype=float)
    out["champion"] = ""
    logs: list[RetrainLog] = []

    for i, R in enumerate(retrain_dates):
        nxt = retrain_dates[i + 1] if i + 1 < len(retrain_dates) else end_ts + pd.Timedelta(days=1)
        Xf = X
        if cfg.use_hmm:
            hmm = RegimeHMM().fit(obs.loc[cfg.train_start:R - pd.Timedelta(days=1)])
            Xf = X.join(hmm.filter_proba(obs.loc[cfg.train_start:]))
        tr_mask = (Xf.index >= cfg.train_start) & (Xf.index < R - pd.Timedelta(days=cfg.horizon + 1)) & y_all.notna()
        Xtr, ytr = Xf.loc[tr_mask], y_all.loc[tr_mask].astype(int)
        folds = _purged_folds(Xtr.index, cfg)

        cands = {"lgbm_fixed": lambda: _lgb(LGB_FIXED, cfg.seed)}
        if cfg.automl:
            def objective(trial):
                params = dict(
                    n_estimators=trial.suggest_int("n_estimators", 100, 400, step=50),
                    learning_rate=trial.suggest_float("learning_rate", 0.01, 0.05, log=True),
                    num_leaves=trial.suggest_int("num_leaves", 3, 8),
                    max_depth=trial.suggest_int("max_depth", 2, 3),
                    min_child_samples=trial.suggest_int("min_child_samples", 100, 400, step=25),
                    subsample=trial.suggest_float("subsample", 0.5, 0.9),
                    subsample_freq=1,
                    colsample_bytree=trial.suggest_float("colsample_bytree", 0.3, 0.7),
                    reg_lambda=trial.suggest_float("reg_lambda", 1.0, 20.0, log=True),
                )
                return _cv_auc(lambda: _lgb(params, cfg.seed), Xtr, ytr, folds)
            study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=cfg.seed + i))
            study.optimize(objective, n_trials=cfg.n_trials, show_progress_bar=False)
            best = dict(study.best_params, subsample_freq=1)
            cands["lgbm_optuna"] = lambda: _lgb(best, cfg.seed)
            cands["extratrees"] = lambda: _etrees(cfg.seed)
            cands["logit"] = _logit
        scores = {k: (study.best_value if k == "lgbm_optuna" else _cv_auc(mk, Xtr, ytr, folds))
                  for k, mk in cands.items()}
        champion = max(scores, key=scores.get)
        gate_on = scores[champion] > cfg.gate_auc
        model = cands[champion]().fit(Xtr, ytr)

        pred_idx = out_idx[(out_idx >= R) & (out_idx < nxt)]
        if len(pred_idx):
            p = model.predict_proba(Xf.loc[pred_idx, Xtr.columns])[:, 1]
            out.loc[pred_idx, "p_raw"] = p
            out.loc[pred_idx, "p_ml"] = p if gate_on else np.nan
            out.loc[pred_idx, "gate"] = float(gate_on)
            out.loc[pred_idx, "champion"] = champion
            if cfg.use_hmm:
                out.loc[pred_idx, ["p_bear", "p_side", "p_bull"]] = Xf.loc[pred_idx, ["p_bear", "p_side", "p_bull"]].to_numpy()

        top = []
        if champion.startswith("lgbm"):
            imp = pd.Series(model.booster_.feature_importance("gain"), index=Xtr.columns)
            top = (imp / imp.sum()).sort_values(ascending=False).head(12).round(4).to_dict()
        logs.append(RetrainLog(R, champion, {k: round(v, 4) for k, v in scores.items()}, bool(gate_on), int(len(ytr)), top))
        if verbose:
            print(f"{R.date()}  n={len(ytr):5d}  " + "  ".join(f"{k}={v:.3f}" for k, v in scores.items())
                  + f"  -> {champion}{'' if gate_on else ' (COMPUERTA CERRADA)'}", flush=True)
    return out, logs, y_all


def oos_quality(pred: pd.DataFrame, y: pd.Series) -> dict:
    j = pred[["p_raw", "gate"]].join(y.rename("y")).dropna()
    if len(j) < 50 or j["y"].nunique() < 2:
        return {}
    yearly = {}
    for yr, g in j.groupby(j.index.year):
        if g["y"].nunique() > 1 and len(g) > 60:
            yearly[int(yr)] = round(roc_auc_score(g["y"], g["p_raw"]), 3)
    return {"n": len(j), "auc": round(roc_auc_score(j["y"], j["p_raw"]), 4),
            "auc_by_year": yearly, "share_gate_on": round(float(j["gate"].mean()), 3)}
