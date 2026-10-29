"""
Detector de regimen con HMM gaussiano (3 estados) sobre [retorno diario, log vol realizada].

Importante: hmmlearn.predict_proba usa forward-backward (suavizado) -> usa el futuro.
Aqui se implementa el FILTRO hacia adelante: P(estado_t | obs_1..t), sin fuga de informacion.
El modelo se ajusta solo con datos de entrenamiento (walk-forward) y los estados se
reordenan por retorno medio: 0 = bajista/estres, 1 = lateral, 2 = alcista.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp
from scipy.stats import multivariate_normal


def regime_obs(d: pd.DataFrame) -> pd.DataFrame:
    r = np.log(d["close"]).diff()
    o = pd.DataFrame({
        "r7": r.rolling(7).sum(),
        "lv7": np.log(np.sqrt(d["rv"].clip(lower=1e-10).rolling(7).mean() * 365)),
    }, index=d.index)
    return o


class RegimeHMM:
    def __init__(self, n_states: int = 3, seed: int = 7):
        self.n = n_states
        self.seed = seed
        self.model: GaussianHMM | None = None
        self.order: np.ndarray | None = None
        self.mu = None
        self.sd = None

    def fit(self, obs: pd.DataFrame) -> "RegimeHMM":
        x = obs.dropna()
        self.mu, self.sd = x.mean(), x.std()
        z = ((x - self.mu) / self.sd).to_numpy()
        best, best_ll = None, -np.inf
        for k in range(4):  # varios inicios, nos quedamos con el de mayor verosimilitud
            m = GaussianHMM(self.n, covariance_type="full", n_iter=200, random_state=self.seed + k)
            m.fit(z)
            ll = m.score(z)
            if ll > best_ll:
                best, best_ll = m, ll
        self.model = best
        self.order = np.argsort(best.means_[:, 0])  # por retorno medio
        return self

    def filter_proba(self, obs: pd.DataFrame) -> pd.DataFrame:
        m = self.model
        x = obs.dropna()
        z = ((x - self.mu) / self.sd).to_numpy()
        logB = np.column_stack([
            multivariate_normal(mean=m.means_[k], cov=m.covars_[k], allow_singular=True).logpdf(z)
            for k in range(self.n)
        ])
        logA = np.log(m.transmat_ + 1e-300)
        la = np.log(m.startprob_ + 1e-300) + logB[0]
        out = np.empty_like(logB)
        out[0] = la - logsumexp(la)
        for t in range(1, len(z)):
            la = logsumexp(out[t - 1][:, None] + logA, axis=0) + logB[t]
            out[t] = la - logsumexp(la)
        p = np.exp(out)[:, self.order]
        cols = ["p_bear", "p_side", "p_bull"] if self.n == 3 else [f"p_s{i}" for i in range(self.n)]
        return pd.DataFrame(p, index=x.index, columns=cols).reindex(obs.index)
