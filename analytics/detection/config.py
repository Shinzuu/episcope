"""Detection defaults and paths. Implements AN-1, AN-3.

Every default is a method parameter, not a property of one dataset: no dates, file names or row counts.
The evaluation sweep (analytics/evaluation) recommends the operating threshold; these are starting values.
"""

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAKE = pathlib.Path(os.environ.get("EPISCOPE_LAKE_DIR", ROOT / "lake"))
if not LAKE.is_absolute():
    LAKE = (ROOT / LAKE).resolve()

CASES_WEEKLY_PATH = LAKE / "gold" / "cases_weekly"
ALERTS_PATH = LAKE / "gold" / "alerts"

GROUP_KEYS = ["district_code", "disease"]

# --- AN-3: reporting completeness ---------------------------------------------------------------
# completeness = reporting_facilities / expected_facilities (capped at 1).
# Below COMPLETENESS_MIN the week is MISSING (null), not a low count. At or above it the count is scaled
# up by 1/completeness. A district-week with no row at all for any disease is missing too.
COMPLETENESS_MIN = 0.8

# --- Seasonal adjustment (research.md §3.1 pitfall 1) -------------------------------------------
# Multiplicative seasonal index per (division, disease, ISO week-of-year): the median, over the OTHER
# years, of the division's pooled count / centred 53-week level, smoothed over +-1 week (wider smoothing
# flattens the sharp dengue peak). Leaving the target year out stops an outbreak from inflating its own
# expected value and hiding itself. Pooling the districts of a division cuts the noise of small series
# (SEASON_POOLED = False: per district).
SEASON_LEVEL_HALF_WIDTH = 26
SEASON_SMOOTH_HALF_WIDTH = 1
SEASON_POOLED = True
SEASON_INDEX_CLAMP = (0.05, 20.0)

# --- EARS C2 (Hutwagner 2003; R surveillance earsC: baseline lags 3..9, 2-period guard band) -----
EARS_BASELINE_LAGS = (3, 9)
EARS_THRESHOLD = 3.0
MIN_SIGMA = 1.0              # R's minSigma: floors the SD so sparse series do not divide by ~0
MIN_BASELINE_WEEKS = 4       # non-missing weeks needed in the baseline before a statistic is produced

# --- CUSUM (Page 1954), one-sided upper, reset after an alarm ------------------------------------
# k = 0.5 targets a 1-SD shift. h = 5 is the upper end of the textbook 4-5; the sweep calibrates it.
# z_t uses the same seasonal quasi-Poisson baseline as EARS (methods.py) over 26 weeks behind a 2-week
# guard band: a longer, steadier baseline, so CUSUM accumulates small sustained excesses (slow outbreaks).
CUSUM_K = 0.5
CUSUM_H = 5.0
CUSUM_BASELINE_WEEKS = 26
CUSUM_GUARD_WEEKS = 2

# --- Alarm gate (Farrington's limit54, research.md §3.3) -----------------------------------------
# No alarm unless the last GATE_WEEKS weeks hold at least GATE_MIN_CASES cases. The statistic is still
# reported; only `alert` is gated. Stops near-empty series (malaria outside the CHT) from alarming on 2 cases.
GATE_WEEKS = 4
GATE_MIN_CASES = 5
