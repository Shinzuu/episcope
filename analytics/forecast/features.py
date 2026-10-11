"""Weekly grid and design matrix for the per-district NB-GLM. Implements AN-4 (feature set).

Feature set (our call, documented here and in the report):
    sin/cos of the week's day-of-year, K = 2 harmonics      seasonality; by date, so ISO week 53 is harmless
    rain  = log1p(mean weekly precip over lags 4..8 weeks)   research.md §4.5: rainfall lag 1-2 months
    temp  = mean weekly mean temperature over lags 4..8      research.md §4.5: temperature lag 4-6 weeks
    level = log1p(mean cases over the 4 weeks ending t-h)    recent-level term, direct model per horizon h

Every weather lag is >= 4 weeks and the level term is lagged by h, so a 1-4-week forecast only uses data
already observed at the origin. No weather forecast is needed.
"""

import datetime as dt

import numpy as np
import pandas as pd

WEATHER_LAG_MIN = 4
WEATHER_LAG_MAX = 8
FOURIER_K = 2
LEVEL_WINDOW = 4
MIN_COMPLETENESS = 0.5   # AN-3 artefact defence: weeks with < 50% of facilities reporting are not training targets
MAX_HORIZON = 4


def _to_ts(s):
    return pd.to_datetime(pd.Series(s)).dt.normalize()


def iso_week_start(year, week):
    return pd.Timestamp(dt.date.fromisocalendar(int(year), int(week), 1))


def weekly_grid(cases, weather, start, end, extend=MAX_HORIZON):
    """One row per week from `start` to `end` + `extend` weeks.

    cases:   week_start, cases, optional reporting_facilities / expected_facilities
    weather: epi_year, epi_week, precip_mm_sum, tmean_c_avg (may be empty or None)
    Weeks up to `end` with no case row are true zeros; weeks after `end` have cases = NaN (the future).
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    idx = pd.date_range(start, end + pd.Timedelta(weeks=extend), freq="7D")
    g = pd.DataFrame(index=idx)
    g.index.name = "week_start"

    c = cases.copy()
    c["week_start"] = _to_ts(c["week_start"]).values
    c = c.groupby("week_start").sum(numeric_only=True)
    g["cases"] = c["cases"].reindex(idx).astype(float)
    g.loc[g.index <= end, "cases"] = g.loc[g.index <= end, "cases"].fillna(0.0)
    if {"reporting_facilities", "expected_facilities"} <= set(c.columns):
        comp = (c["reporting_facilities"] / c["expected_facilities"].where(c["expected_facilities"] > 0)).reindex(idx)
        g["completeness"] = comp.fillna(1.0).clip(0, 1)
    else:
        g["completeness"] = 1.0

    if weather is not None and len(weather):
        w = weather.copy()
        w["week_start"] = [iso_week_start(y, k) for y, k in zip(w["epi_year"], w["epi_week"])]
        w = w.groupby("week_start")[["precip_mm_sum", "tmean_c_avg"]].mean()
        full = w.reindex(w.index.union(idx)).sort_index().ffill().bfill()
        g["precip"] = full["precip_mm_sum"].reindex(idx).values
        g["tmean"] = full["tmean_c_avg"].reindex(idx).values
    else:
        g["precip"] = np.nan
        g["tmean"] = np.nan
    return g


def design(grid, h):
    """Feature matrix (no constant) for every row of `grid` as target week, for horizon h."""
    t = grid.index + pd.Timedelta(days=3)                      # mid-week
    angle = 2 * np.pi * (t.dayofyear.values - 1) / 365.25
    X = pd.DataFrame(index=grid.index)
    for k in range(1, FOURIER_K + 1):
        X[f"sin{k}"] = np.sin(k * angle)
        X[f"cos{k}"] = np.cos(k * angle)
    span = WEATHER_LAG_MAX - WEATHER_LAG_MIN + 1
    X["rain"] = np.log1p(grid["precip"].shift(WEATHER_LAG_MIN).rolling(span).mean().clip(lower=0))
    X["temp"] = grid["tmean"].shift(WEATHER_LAG_MIN).rolling(span).mean()
    X["level"] = np.log1p(grid["cases"].shift(h).rolling(LEVEL_WINDOW).mean())
    return X
