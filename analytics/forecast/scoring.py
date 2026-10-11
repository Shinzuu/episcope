"""Forecast scores. Implements AN-5: WIS, 80% interval coverage and MAE.

WIS exactly as research.md §4.1 (Bracher et al. 2021, PLoS Comput Biol 17(2):e1008618):
    IS_a(F, y)  = (u - l) + (2/a)(l - y)1(y < l) + (2/a)(y - u)1(y > u)
    WIS(F, y)   = 1/(K + 1/2) * ( w0 |y - m| + sum_k w_k IS_{a_k}(F, y) ),   w0 = 1/2, w_k = a_k / 2
returned with its decomposition into dispersion, over-prediction and under-prediction (they sum to WIS).
"""

import numpy as np


def wis(y, median, lower, upper, alphas):
    """Scalar or vector WIS. lower/upper: sequences aligned with alphas (each element scalar or array).
    Returns dict(wis, dispersion, overprediction, underprediction)."""
    y = np.asarray(y, float)
    m = np.asarray(median, float)
    K = len(alphas)
    disp = np.zeros_like(y)
    over = 0.5 * np.maximum(m - y, 0)
    under = 0.5 * np.maximum(y - m, 0)
    for a, lo, hi in zip(alphas, lower, upper):
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        w = a / 2
        disp = disp + w * (hi - lo)
        over = over + w * (2 / a) * np.maximum(lo - y, 0)
        under = under + w * (2 / a) * np.maximum(y - hi, 0)
    norm = K + 0.5
    out = {"dispersion": disp / norm, "overprediction": over / norm, "underprediction": under / norm}
    out["wis"] = out["dispersion"] + out["overprediction"] + out["underprediction"]
    if out["wis"].ndim == 0:
        out = {k: float(v) for k, v in out.items()}
    return out


def wis_from_quantiles(df, alphas, prefix=""):
    """WIS for a DataFrame holding y and quantile columns named q<level> (optionally prefixed)."""
    from analytics.forecast.model import qcol

    lo = [df[prefix + qcol(round(a / 2, 4))].values for a in alphas]
    hi = [df[prefix + qcol(round(1 - a / 2, 4))].values for a in alphas]
    return wis(df["y"].values, df[prefix + qcol(0.5)].values, lo, hi, alphas)


def coverage(y, lo, hi):
    y, lo, hi = (np.asarray(v, float) for v in (y, lo, hi))
    return float(np.mean((lo <= y) & (y <= hi))) if len(y) else float("nan")


def mae(y, point):
    y, point = np.asarray(y, float), np.asarray(point, float)
    return float(np.mean(np.abs(y - point))) if len(y) else float("nan")
