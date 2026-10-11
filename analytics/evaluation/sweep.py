"""Threshold sweep and operating-point choice for the C1 (sensitivity vs false alerts) trade-off. Implements AN-2.

Grids (research.md §3.4, widened at both ends so the curve reaches its corners):
- EARS C2 threshold 1.5 .. 6.0 in 0.25 steps. The statistic does not depend on the threshold, so the sweep
  re-thresholds one scored table (alert = statistic > thr and gate).
- CUSUM h 1.0 .. 10.0 in 0.5 steps, k fixed at 0.5. CUSUM resets after each alarm, so the statistic depends
  on h and every grid point re-runs CUSUM (the same `cusum_frame` the Spark job uses).

Recommendation: per method, the point with the highest sensitivity whose false alerts per district-year stay
within the budget; ties go to the shorter median delay, then fewer false alerts, then the higher threshold.
If no point meets the budget, the one with the fewest false alerts is returned with budget_met = false.
"""

import numpy as np
import pandas as pd

from analytics.detection import config as DC
from analytics.detection.methods import cusum_frame
from analytics.evaluation.metrics import evaluate

EARS_GRID = [round(x, 2) for x in np.arange(1.5, 6.0001, 0.25)]
CUSUM_GRID = [round(x, 2) for x in np.arange(1.0, 10.0001, 0.5)]
DEFAULT_BUDGET = 2.0  # false alert episodes per district-year, all diseases together (research.md §3.4 example)

METRIC_COLS = ["n_outbreaks", "detected", "sensitivity", "alert_episodes", "false_alerts", "district_years",
               "false_alerts_per_district_year", "ppv", "median_days_to_detection", "detected_within_7d",
               "detected_within_14d"]


def _row(method, param, m, k=None):
    r = {"method": method, "param_name": "threshold" if method == "ears_c2" else "h", "param": param, "k": k}
    r.update({c: m[c] for c in METRIC_COLS})
    return r


def sweep_ears(ears_scored, outbreaks, grid=EARS_GRID, buffer_days=7):
    rows = []
    base = ears_scored.copy()
    for thr in grid:
        base["alert"] = (base["statistic"].fillna(-np.inf) > thr) & base["gate"].astype(bool)
        rows.append(_row("ears_c2", thr, evaluate(base, outbreaks, buffer_days)))
    return pd.DataFrame(rows)


def cusum_scored(prepared, h, k=DC.CUSUM_K):
    parts = [cusum_frame(g, k=k, h=h) for _, g in prepared.groupby(DC.GROUP_KEYS, sort=False)]
    return pd.concat(parts, ignore_index=True)


def sweep_cusum(prepared, outbreaks, grid=CUSUM_GRID, k=DC.CUSUM_K, buffer_days=7):
    return pd.DataFrame([_row("cusum", h, evaluate(cusum_scored(prepared, h, k), outbreaks, buffer_days), k=k)
                         for h in grid])


def recommend(curve, budget=DEFAULT_BUDGET):
    """Best point per method under the false-alert budget. Returns {method: dict}."""
    out = {}
    for method, g in curve.groupby("method"):
        ok = g[g["false_alerts_per_district_year"] <= budget]
        met = len(ok) > 0
        pool = ok if met else g.nsmallest(1, "false_alerts_per_district_year")
        pool = pool.assign(_delay=pool["median_days_to_detection"].fillna(np.inf))
        best = pool.sort_values(["sensitivity", "_delay", "false_alerts_per_district_year", "param"],
                                ascending=[False, True, True, False]).iloc[0]
        rec = {c: (None if isinstance(best[c], float) and np.isnan(best[c]) else best[c])
               for c in ["param_name", "param", "k"] + METRIC_COLS}
        rec["budget"] = budget
        rec["budget_met"] = met
        out[method] = {k: (v.item() if hasattr(v, "item") else v) for k, v in rec.items()}
    return out
