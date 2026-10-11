"""Per-series NB2 GLM and the seasonal-naive baseline. Implements AN-4 (model) and AN-5 (baseline).

Model: log mu_t = b0 + Fourier(K=2) + b_r rain(lag 4-8) + b_T temp(lag 4-8) + b_y log1p(level_{t-h}),
Var(y) = mu + alpha mu^2. Fitted with statsmodels' discrete NegativeBinomial(loglike_method='nb2'), which
estimates alpha (sm.GLM with the NegativeBinomial family would fix alpha = 1; research.md §4.5).
One direct model per horizon h = 1..4. Predictive quantiles are the plug-in NB quantiles
scipy.stats.nbinom.ppf(q, n=1/alpha, p=1/(1+alpha mu)); they ignore parameter uncertainty.

Fallback chain, recorded in the `method` column: nb2 -> poisson_mm (Poisson GLM + moment alpha) ->
seasonal_naive -> naive_short (under a year of history). An all-zero training series gives `zero`.

Seasonal naive (FPP3 §5.2): y_hat_{T+h} = y_{T+h-52}, i.e. the same week by date (t - 364 days), so 53-week
ISO years need no special case. Its intervals are empirical quantiles of past seasonal-naive errors
(research.md §4.4), so WIS and coverage compare like with like.
"""

import warnings

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from analytics.forecast import features as F

# Central intervals used for WIS: alpha in {0.02, 0.05, 0.1, 0.2, ..., 0.9} (COVID-19 Forecast Hub, K = 11)
WIS_ALPHAS = [0.02, 0.05] + [round(0.1 * k, 1) for k in range(1, 10)]
QUANTILES = sorted({0.5} | {round(a / 2, 4) for a in WIS_ALPHAS} | {round(1 - a / 2, 4) for a in WIS_ALPHAS})
MIN_TRAIN_ROWS = 26
SEASON = 52


def qcol(q):
    """0.025 -> 'q0_025' (no dots, so the names are safe as Spark columns)."""
    return f"q{q:g}".replace(".", "_")


class Fitted:
    def __init__(self, method, cols, mean, std, beta, alpha, eta_cap):
        self.method, self.cols, self.mean, self.std = method, cols, mean, std
        self.beta, self.alpha, self.eta_cap = beta, alpha, eta_cap

    def predict_mu(self, xrow):
        x = (xrow[self.cols].astype(float).values - self.mean) / self.std
        eta = self.beta[0] + float(np.dot(self.beta[1:], x))
        return float(np.exp(min(eta, self.eta_cap)))


def fit_nb(X, y):
    """Fit NB2 on training rows. Returns Fitted or None (caller falls back)."""
    import statsmodels.api as sm
    from statsmodels.discrete.discrete_model import NegativeBinomial

    cand = [c for c in X.columns if X[c].notna().mean() >= 0.5]
    d = pd.concat([X[cand], y.rename("_y")], axis=1).dropna()
    if len(d) < MIN_TRAIN_ROWS:
        return None
    cols = [c for c in cand if d[c].std() > 1e-9]
    yy = d["_y"].values.astype(float)
    if yy.sum() == 0:
        return Fitted("zero", cols, np.zeros(len(cols)), np.ones(len(cols)), np.r_[-np.inf, np.zeros(len(cols))], 1e-6, -np.inf)
    mean, std = d[cols].mean().values, d[cols].std().values
    Xs = sm.add_constant((d[cols].values - mean) / std, has_constant="add")
    eta_cap = float(np.log(10 * yy.max() + 10))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            pois = sm.GLM(yy, Xs, family=sm.families.Poisson()).fit()
            start = pois.params
        except Exception:
            pois, start = None, np.r_[np.log(yy.mean() + 1e-3), np.zeros(len(cols))]
        # Newton from statsmodels' own start values first; BFGS from the Poisson fit if that fails.
        # (Newton started at an arbitrary alpha can step alpha below 0 and return NaN.)
        for kw in ({"method": "newton"}, {"method": "bfgs", "start_params": np.r_[start, 0.5]}):
            try:
                res = NegativeBinomial(yy, Xs, loglike_method="nb2").fit(maxiter=200, disp=0, **kw)
                p = np.asarray(res.params, float)
                if res.mle_retvals.get("converged", False) and np.all(np.isfinite(p)) and p[-1] > 0:
                    return Fitted("nb2", cols, mean, std, p[:-1], float(np.clip(p[-1], 1e-6, 50)), eta_cap)
            except Exception:
                pass
        if pois is not None and np.all(np.isfinite(pois.params)):
            mu = pois.fittedvalues
            a = float(np.sum((yy - mu) ** 2 - mu) / max(np.sum(mu ** 2), 1e-9))
            return Fitted("poisson_mm", cols, mean, std, np.asarray(pois.params, float), float(np.clip(a, 1e-6, 50)), eta_cap)
    return None


def nb_quantiles(mu, alpha, qs=QUANTILES):
    if mu <= 0:
        return np.zeros(len(qs))
    n = 1.0 / alpha
    return nbinom.ppf(qs, n, n / (n + mu)).astype(float)   # p = 1/(1 + alpha mu)


def seasonal_naive(y, t_origin, h, qs=QUANTILES):
    """y: cases Series on the weekly grid; t_origin: integer position of the origin. Returns (method, mean, quantiles)."""
    tgt = t_origin + h
    hist = y.iloc[: t_origin + 1].values.astype(float)
    if tgt - SEASON >= 0 and not np.isnan(y.iloc[tgt - SEASON]):
        point = float(y.iloc[tgt - SEASON])
        err = hist[SEASON:] - hist[:-SEASON] if len(hist) > SEASON else np.array([])
        err = err[~np.isnan(err)]
        if len(err) >= 10:
            return "seasonal_naive", point, np.maximum(0.0, point + np.quantile(err, qs))
        return "seasonal_naive", point, poisson.ppf(qs, max(point, 1e-9)).astype(float)
    recent = hist[~np.isnan(hist)][-F.LEVEL_WINDOW:]
    point = float(recent.mean()) if len(recent) else 0.0
    return "naive_short", point, poisson.ppf(qs, max(point, 1e-9)).astype(float)


def forecast_series(grid, origins, horizons=range(1, F.MAX_HORIZON + 1), refit_every=1, qs=QUANTILES, baseline=False):
    """Forecast one district x disease series from each origin (list of week_start Timestamps on the grid).

    Model h is refitted every `refit_every` origins on data <= that origin; between refits the latest fit
    is reused with features as known at each origin. Returns one row per (origin, h) with mean, every
    quantile in `qs`, the method used and the observed y (NaN for the future). With baseline=True the
    seasonal-naive forecast is also returned, as columns prefixed `bl_`.
    """
    y = grid["cases"]
    pos = {ts: i for i, ts in enumerate(grid.index)}
    train_ok = grid["completeness"] >= F.MIN_COMPLETENESS
    rows = []
    for h in horizons:
        X = F.design(grid, h)
        fit = None
        for k, origin in enumerate(origins):
            t = pos[pd.Timestamp(origin)]
            if t + h >= len(grid):
                continue
            if k % refit_every == 0 or fit is None:
                m = (grid.index <= origin) & y.notna().values & train_ok.values
                fit = fit_nb(X[m], y[m])
            target = grid.index[t + h]
            xrow = X.iloc[t + h]
            if fit is not None and fit.method == "zero":
                method, mean, q = "zero", 0.0, np.zeros(len(qs))
            elif fit is not None and not xrow[fit.cols].isna().any():
                mean = fit.predict_mu(xrow)
                method, q = fit.method, nb_quantiles(mean, fit.alpha, qs)
            else:
                method, mean, q = seasonal_naive(y, t, h, qs)
            row = {"origin_week_start": origin, "horizon": h, "target_week_start": target,
                   "method": method, "mean": mean, "y": y.iloc[t + h]}
            row.update({qcol(a): b for a, b in zip(qs, q)})
            if baseline:
                bm, bmean, bq = seasonal_naive(y, t, h, qs)
                row.update({"bl_method": bm, "bl_mean": bmean})
                row.update({"bl_" + qcol(a): b for a, b in zip(qs, bq)})
            rows.append(row)
    return pd.DataFrame(rows)
