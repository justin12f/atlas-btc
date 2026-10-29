"""
Pruebas para separar habilidad de suerte cuando se prueban muchas variantes.

- Bootstrap estacionario (Politis y Romano, 1994): remuestrea bloques de dias de longitud
  aleatoria; se aplican los MISMOS indices a todas las variantes, asi se conserva la
  correlacion entre ellas.
- White (2000) Reality Check y Romano-Wolf stepdown sobre diferencias de Sharpe contra la base.
- PBO con CSCV (Bailey, Borwein, Lopez de Prado y Zhu, 2015).
- Sharpe deflactado (Bailey y Lopez de Prado, 2014).
"""
from __future__ import annotations

import itertools

import numpy as np
from scipy.stats import norm

ANN = np.sqrt(365)


def stationary_bootstrap(n: int, B: int, mean_block: float = 20.0, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    idx = np.empty((B, n), dtype=np.int64)
    starts = rng.integers(0, n, size=(B, n))
    new_block = rng.random((B, n)) < p
    idx[:, 0] = starts[:, 0]
    for t in range(1, n):
        idx[:, t] = np.where(new_block[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def sharpe_cols(R: np.ndarray) -> np.ndarray:
    sd = R.std(axis=0, ddof=1)
    return np.where(sd > 0, R.mean(axis=0) / sd * ANN, 0.0)


def bootstrap_sharpes(R: np.ndarray, idx: np.ndarray, chunk: int = 100) -> np.ndarray:
    """Devuelve matriz B x K de Sharpes remuestreados."""
    B = idx.shape[0]
    out = np.empty((B, R.shape[1]))
    for s in range(0, B, chunk):
        sl = idx[s:s + chunk]
        for j, ii in enumerate(sl):
            out[s + j] = sharpe_cols(R[ii])
    return out


def reality_check(sr: np.ndarray, sr_boot: np.ndarray, base: int) -> dict:
    """White RC estudentizado sobre d_k = SR_k - SR_base (k != base)."""
    d = sr - sr[base]
    d_b = sr_boot - sr_boot[:, [base]]
    mask = np.arange(len(sr)) != base
    sd = d_b[:, mask].std(axis=0, ddof=1)
    sd = np.where(sd > 0, sd, np.inf)                 # variantes identicas a la base: t = 0
    t_obs = d[mask] / sd
    t_boot = (d_b[:, mask] - d[mask]) / sd           # recentrado
    max_obs = t_obs.max()
    p = float((t_boot.max(axis=1) >= max_obs).mean())
    return {"p_value": p, "max_t": float(max_obs), "best_k": int(np.arange(len(sr))[mask][t_obs.argmax()])}


def romano_wolf(sr: np.ndarray, sr_boot: np.ndarray, base: int, alpha: float = 0.05) -> dict:
    """Stepdown: que variantes superan a la base controlando el error por familia."""
    d = sr - sr[base]
    d_b = sr_boot - sr_boot[:, [base]]
    ks = np.array([k for k in range(len(sr)) if k != base])
    sd = d_b[:, ks].std(axis=0, ddof=1)
    sd = np.where(sd > 0, sd, np.inf)
    t_obs = d[ks] / sd
    t_boot = (d_b[:, ks] - d[ks]) / sd
    active = np.ones(len(ks), bool)
    rejected = np.zeros(len(ks), bool)
    pvals = np.ones(len(ks))
    while active.any():
        mx = t_boot[:, active].max(axis=1)
        crit = np.quantile(mx, 1 - alpha)
        new = active & (t_obs > crit)
        for i in np.where(active)[0]:
            pvals[i] = min(pvals[i], float((mx >= t_obs[i]).mean()))
        if not new.any():
            break
        rejected |= new
        active &= ~new
    return {"significant": ks[rejected].tolist(), "p_adj": dict(zip(ks.tolist(), pvals.tolist()))}


def pbo_cscv(R: np.ndarray, S: int = 16) -> dict:
    """Probabilidad de sobreajuste del backtest via validacion cruzada combinatoria."""
    n, K = R.shape
    n_use = n - n % S
    blocks = np.array_split(np.arange(n_use), S)
    sums = np.stack([R[b].sum(axis=0) for b in blocks])          # S x K
    sq = np.stack([(R[b] ** 2).sum(axis=0) for b in blocks])
    cnt = np.array([len(b) for b in blocks])
    logits, oos_sr_best, is_sr_best = [], [], []
    for comb in itertools.combinations(range(S), S // 2):
        is_ = np.zeros(S, bool)
        is_[list(comb)] = True
        def sr(mask):
            N = cnt[mask].sum()
            m = sums[mask].sum(axis=0) / N
            v = sq[mask].sum(axis=0) / N - m ** 2
            return m / np.sqrt(np.maximum(v, 1e-18)) * ANN
        s_is, s_oos = sr(is_), sr(~is_)
        best = int(np.argmax(s_is))
        rank = (s_oos < s_oos[best]).sum() + 0.5 * ((s_oos == s_oos[best]).sum() - 1)
        w = (rank + 1) / (K + 1)                                       # rango relativo en (0,1)
        logits.append(np.log(w / (1 - w)))
        oos_sr_best.append(s_oos[best])
        is_sr_best.append(s_is[best])
    logits = np.array(logits)
    return {"pbo": float((logits <= 0).mean()), "n_splits": len(logits),
            "median_oos_sharpe_of_is_best": float(np.median(oos_sr_best)),
            "median_is_sharpe_of_is_best": float(np.median(is_sr_best))}


def deflated_sharpe(r: np.ndarray, sr_trials: np.ndarray, n_trials: int) -> dict:
    """DSR: probabilidad de que el Sharpe verdadero supere el maximo esperado por azar."""
    sr = r.mean() / r.std(ddof=1)                     # por periodo
    T = len(r)
    sk = ((r - r.mean()) ** 3).mean() / r.std() ** 3
    ku = ((r - r.mean()) ** 4).mean() / r.std() ** 4
    var_sr = np.var(sr_trials / ANN, ddof=1)          # varianza de Sharpes (por periodo) entre pruebas
    emc = 0.5772156649
    sr0 = np.sqrt(var_sr) * ((1 - emc) * norm.ppf(1 - 1 / n_trials) + emc * norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    return {"dsr": float(norm.cdf(z)), "sr_annual": float(sr * ANN), "sr0_annual": float(sr0 * ANN)}
