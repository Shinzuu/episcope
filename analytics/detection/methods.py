"""Detection methods. Implements AN-1 (EARS C2 primary, CUSUM secondary, both in Spark) and AN-3 (two methods,
plus the naive detector the artefact demonstration compares against).

Both methods standardise this week's completeness-adjusted count against a baseline taken from earlier weeks,
under a seasonal quasi-Poisson model. With s_i the seasonal index and y_i the adjusted count of baseline week i
(missing weeks skipped):

    level      m   = sum(y_i) / sum(s_i)                         (Poisson MLE of the deseasonalised level)
    dispersion phi = max(1, sum((y_i - s_i m)^2 / (s_i m)) / (n - 1))   (Pearson; never below Poisson)
    expected   E_t = s_t m,   sigma_t = max(sqrt(phi E_t), MIN_SIGMA),   z_t = (y_t - E_t) / sigma_t

With s = 1 this is EARS C2's (y - mean) / sd, with the SD floored at the Poisson SD. The seasonal model matters
because rescaling low-season counts up to a high-season week inflates their variance by s^2, while count
variance only grows with s. All of it is window-function sums, so it runs in Spark at scale.

- EARS C2: baseline lags 3..9 (R surveillance::earsC: B = 7 with a 2-week guard band). Alarm when z > threshold.
- CUSUM: baseline lags 3..28 (26 weeks behind the same guard band), then the sequential recursion in grouped
  applyInPandas: S_t = max(0, S_{t-1} + z_t - k), alarm when S_t > h, then reset to 0.
- Naive detector (AN-3 comparison only): textbook EARS C2 on raw counts, z = (y - mean) / max(sd, MIN_SIGMA),
  missing weeks as zero, no completeness or seasonal adjustment.

Every scored frame has the gold.alerts columns plus `week_start`, `gate` and `cases`; `to_alerts` keeps the
contract columns only.
"""

import math

import numpy as np
import pandas as pd
from pyspark.sql import Window
from pyspark.sql import functions as F

from analytics.detection import config as C

KEYS = C.GROUP_KEYS
ALERT_COLS = ["district_code", "disease", "epi_year", "epi_week", "method", "statistic", "threshold", "alert"]
CUSUM_INPUT = ["week_start", "epi_year", "epi_week", "cases", "cusum_z"]
SCORED_SCHEMA = (
    "district_code string, disease string, week_start date, epi_year int, epi_week int, method string, "
    "statistic double, threshold double, alert boolean, gate boolean, cases double"
)


def _w():
    return Window.partitionBy(*KEYS).orderBy("week_start")


def _gate_col(gate_weeks=C.GATE_WEEKS, gate_min=C.GATE_MIN_CASES):
    w = _w().rowsBetween(-(gate_weeks - 1), 0)
    return F.sum(F.coalesce("cases", F.lit(0.0))).over(w) >= gate_min


def baseline_z(df, near, far, out, min_baseline=C.MIN_BASELINE_WEEKS, min_sigma=C.MIN_SIGMA):
    """Add column `out` = z_t under the seasonal quasi-Poisson baseline over lags near..far (see module doc)."""
    base = _w().rowsBetween(-far, -near)
    y, s = F.col("adj_cases"), F.col("season_index")
    s_ok = F.when(y.isNotNull(), s)
    n = F.count(y).over(base)
    sy, ss = F.sum(y).over(base), F.sum(s_ok).over(base)
    syy_s = F.sum(F.when(y.isNotNull(), y * y / s)).over(base)
    df = df.withColumn("_n", n).withColumn("_m", sy / ss).withColumn("_q", syy_s)
    m = F.col("_m")
    # sum((y - s m)^2 / (s m)) = (sum(y^2 / s) - m sum(y)) / m  because m^2 sum(s) = m sum(y)
    pearson = (F.col("_q") - m * sy) / m
    phi = F.when(m > 0, F.greatest(pearson / (F.col("_n") - 1), F.lit(1.0))).otherwise(F.lit(1.0))
    expected = s * m
    sigma = F.greatest(F.sqrt(phi * expected), F.lit(float(min_sigma)))
    ok = (F.col("_n") >= min_baseline) & y.isNotNull() & m.isNotNull()
    return df.withColumn(out, F.when(ok, (y - expected) / sigma)).drop("_n", "_m", "_q")


def score_inputs(prepared, ears_lags=C.EARS_BASELINE_LAGS, cusum_baseline=C.CUSUM_BASELINE_WEEKS,
                 cusum_guard=C.CUSUM_GUARD_WEEKS):
    """prepared -> + ears_stat, cusum_z, gate. Threshold-free, so the evaluation sweep can reuse it."""
    near, far = ears_lags
    df = baseline_z(prepared, near, far, "ears_stat")
    df = baseline_z(df, cusum_guard + 1, cusum_guard + cusum_baseline, "cusum_z")
    return df.withColumn("gate", _gate_col())


def ears_c2(scored_inputs, threshold=C.EARS_THRESHOLD):
    """EARS C2 rows from score_inputs(...)."""
    df = scored_inputs
    return df.select(
        *KEYS, "week_start", "epi_year", "epi_week", F.lit("ears_c2").alias("method"),
        F.col("ears_stat").alias("statistic"), F.lit(float(threshold)).alias("threshold"),
        (F.coalesce(F.col("ears_stat") > threshold, F.lit(False)) & F.col("gate")).alias("alert"),
        "gate", "cases",
    )


def naive_ears(prepared_naive, threshold=C.EARS_THRESHOLD, lags=C.EARS_BASELINE_LAGS, min_sigma=C.MIN_SIGMA):
    """Textbook EARS C2 on raw counts. Pass prepare(..., use_completeness=False, use_seasonal=False)."""
    near, far = lags
    base = _w().rowsBetween(-far, -near)
    x = F.coalesce("cases", F.lit(0.0))
    df = prepared_naive.withColumn("_x", x)
    stat = (F.col("_x") - F.avg("_x").over(base)) / F.greatest(F.coalesce(F.stddev_samp("_x").over(base), F.lit(0.0)),
                                                               F.lit(float(min_sigma)))
    df = df.withColumn("statistic", F.when(F.count("_x").over(base) >= C.MIN_BASELINE_WEEKS, stat))
    return df.select(
        *KEYS, "week_start", "epi_year", "epi_week", F.lit("naive_ears").alias("method"), "statistic",
        F.lit(float(threshold)).alias("threshold"),
        F.coalesce(F.col("statistic") > threshold, F.lit(False)).alias("alert"),
        F.lit(True).alias("gate"), "cases",
    )


# --- CUSUM ----------------------------------------------------------------------------------------

def cusum_frame(pdf, k=C.CUSUM_K, h=C.CUSUM_H):
    """One (district, disease) series in pandas -> scored rows. Pure function, also used by the sweep.

    Needs week_start, epi_year, epi_week, cases, cusum_z, gate (gate is recomputed if absent).
    A missing z (missing week or no baseline yet) leaves S unchanged and gives a null statistic.
    """
    pdf = pdf.sort_values("week_start").reset_index(drop=True)
    z = pd.to_numeric(pdf["cusum_z"], errors="coerce").astype(float).to_numpy()
    cases = pd.to_numeric(pdf["cases"], errors="coerce")
    if "gate" in pdf:
        gate = pdf["gate"].fillna(False).astype(bool).to_numpy()
    else:
        gate = (cases.fillna(0.0).rolling(C.GATE_WEEKS, min_periods=1).sum() >= C.GATE_MIN_CASES).to_numpy()

    s, stats, alerts = 0.0, np.full(len(z), np.nan), np.zeros(len(z), dtype=bool)
    for i, zi in enumerate(z):
        if math.isnan(zi):
            continue
        s = max(0.0, s + zi - k)
        stats[i] = s
        if s > h and gate[i]:
            alerts[i] = True
            s = 0.0                       # reset after an alarm
    return pd.DataFrame({
        "district_code": pdf["district_code"], "disease": pdf["disease"], "week_start": pdf["week_start"],
        "epi_year": pdf["epi_year"].astype("int32"), "epi_week": pdf["epi_week"].astype("int32"),
        "method": "cusum", "statistic": stats, "threshold": float(h), "alert": alerts, "gate": gate,
        "cases": cases.astype(float),
    })


def cusum(scored_inputs, k=C.CUSUM_K, h=C.CUSUM_H):
    """CUSUM rows from score_inputs(...), one applyInPandas group per (district, disease)."""
    def fn(pdf):
        return cusum_frame(pdf, k=k, h=h)

    return scored_inputs.select(*KEYS, *CUSUM_INPUT, "gate").groupBy(*KEYS).applyInPandas(fn, schema=SCORED_SCHEMA)


def detect(weekly, ears_threshold=C.EARS_THRESHOLD, cusum_k=C.CUSUM_K, cusum_h=C.CUSUM_H,
           use_completeness=True, use_seasonal=True):
    """gold.cases_weekly DataFrame -> scored rows for both methods (contract columns + week_start, gate, cases)."""
    from analytics.detection.prepare import prepare

    inputs = score_inputs(prepare(weekly, use_completeness=use_completeness, use_seasonal=use_seasonal)).cache()
    return ears_c2(inputs, threshold=ears_threshold).unionByName(cusum(inputs, k=cusum_k, h=cusum_h))


def to_alerts(scored):
    """Keep exactly the gold.alerts contract columns."""
    return scored.select(*ALERT_COLS)
