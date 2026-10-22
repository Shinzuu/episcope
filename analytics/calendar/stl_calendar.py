"""Preparedness calendar from STL seasonality. Implements AN-9.

Recipe (research.md §11):
1. Weekly cases per division x disease (gold.cases_weekly summed over districts), missing weeks = 0, log1p
   (seasonality is multiplicative).
2. STL(period=52, seasonal=13, robust=True); robust down-weights outbreak weeks. With under two full years the
   seasonal component falls back to the mean log1p count per epi-week, centred.
3. Seasonal index per epi-week w = exp(mean seasonal component at w), so 1.0 is an average week and 2.0 twice
   as many cases. ISO week 53 is folded into week 52.
4. Onset = first week after the seasonal trough where the index (case scale) rises above
   trough + 25% of (peak - trough).
   Peak = the week with the largest index.
5. Lead-time rule: pre-position supplies by onset - SUPPLY_LEAD_WEEKS; start the prevention campaign by
   onset - CAMPAIGN_LEAD_WEEKS[disease]. Weeks wrap around the year.
6. Low-signal rule: a series with fewer than MIN_MEAN_WEEKLY_CASES cases a week on average is too sparse to carry
   a season (malaria outside the endemic districts), so it is marked no_seasonality and gets no action weeks.

Output: gold.calendar (docs/contracts.md): division_code, disease, epi_week (1..52), seasonal_index,
action in {preposition, campaign, none}; plus <results>/calendar_summary.csv/.json with onset, peak,
the two action weeks, the year-to-year onset spread and the rule in words.

Caveat for the report: on synthetic data this recovers the generator's own seasonality; validate dengue
against the OpenDengue national pattern (peak Aug-Oct, research.md §1.2).

Run: python -m analytics.calendar.stl_calendar
"""

import json

import numpy as np
import pandas as pd
from pyspark.sql import functions as Fn

PERIOD = 52
STL_SEASONAL = 13
ONSET_FRACTION = 0.25
MIN_MEAN_WEEKLY_CASES = 1.0  # Below this the STL "season" is noise: the four non-endemic malaria divisions have
                             # 0.03-0.2 a week at SF=0.25, the endemic ones 1.9 or more (8 or more at SF=1).

SUPPLY_LEAD_WEEKS = 3        # ASSUMPTION. Procurement + transport to district stores, 2-4 weeks (research.md §11).
CAMPAIGN_LEAD_WEEKS = {      # ASSUMPTION. How long prevention needs to act before cases rise.
    "dengue": 6,             # larval source reduction must precede the 4-8-week rain-to-cases lag (research.md §4.5)
    "malaria": 6,            # bed-net and IRS rounds before the May-Oct transmission season
    "diarrhoea": 4,          # WASH and ORS-awareness messaging before the pre-monsoon rise
    "ili": 4,                # vaccination / respiratory hygiene messaging before the rainy-season peak
}
DEFAULT_CAMPAIGN_LEAD = 4

OUT_SCHEMA = "division_code string, disease string, epi_week int, seasonal_index double, action string"
RULE_TEXT = (f"Pre-position supplies {SUPPLY_LEAD_WEEKS} weeks before the seasonal onset; start the prevention "
             f"campaign {{lead}} weeks before onset. Onset = first week after the trough where the STL seasonal "
             f"index exceeds trough + {int(ONSET_FRACTION * 100)}% of the trough-to-peak range. No action weeks "
             f"where a division averages fewer than {MIN_MEAN_WEEKLY_CASES:g} case a week.")


def _wrap(w):
    return int((w - 1) % PERIOD) + 1


def seasonal_profile(weekly):
    """weekly: DataFrame(week_start, cases) for one division x disease. Returns (52-vector of log seasonal
    effects for epi-weeks 1..52, DataFrame(year, epi_week, seasonal) per observed week, mean weekly cases)."""
    s = weekly.copy()
    s["week_start"] = pd.to_datetime(s["week_start"])
    s = s.groupby("week_start")["cases"].sum()
    idx = pd.date_range(s.index.min(), s.index.max(), freq="7D")
    y = np.log1p(s.reindex(idx).fillna(0.0).astype(float))
    iso = idx.isocalendar()
    wk = np.minimum(iso["week"].values.astype(int), PERIOD)
    if len(y) >= 2 * PERIOD and y.std() > 0:
        from statsmodels.tsa.seasonal import STL

        seas = STL(y.values, period=PERIOD, seasonal=STL_SEASONAL, robust=True).fit().seasonal
    else:
        means = pd.Series(y.values).groupby(wk).transform("mean").values
        seas = means - y.values.mean()
    per = pd.DataFrame({"year": iso["year"].values.astype(int), "epi_week": wk, "seasonal": seas})
    prof = per.groupby("epi_week")["seasonal"].mean().reindex(range(1, PERIOD + 1))
    prof = prof.interpolate(limit_direction="both").fillna(0.0)
    return prof.values - prof.values.mean(), per, float(np.expm1(y).mean())


def onset_peak(profile):
    """Onset and peak epi-weeks (1-based) of a 52-vector of log seasonal effects, scanning forward from the
    trough with wrap-around. The 25% threshold is taken on the case scale (exp), i.e. expected cases have
    covered a quarter of the way from the trough to the peak; on the log scale the threshold would be met
    within a few weeks of the trough and pre-position far too early."""
    p = np.exp(np.asarray(profile, float))
    lo, hi = int(np.argmin(p)), int(np.argmax(p))
    thr = p[lo] + ONSET_FRACTION * (p[hi] - p[lo])
    onset = hi
    for k in range(1, PERIOD + 1):
        j = (lo + k) % PERIOD
        if p[j] >= thr:
            onset = j
            break
    return onset + 1, hi + 1


def calendar_for_series(weekly, disease):
    """Returns (52 gold.calendar-shaped rows without division_code, summary dict)."""
    prof, per, mean_weekly = seasonal_profile(weekly)
    onset, peak = onset_peak(prof)
    lead_c = CAMPAIGN_LEAD_WEEKS.get(disease, DEFAULT_CAMPAIGN_LEAD)
    flat = float(np.ptp(prof)) < 1e-9 or mean_weekly < MIN_MEAN_WEEKLY_CASES
    prep, camp = (None, None) if flat else (_wrap(onset - SUPPLY_LEAD_WEEKS), _wrap(onset - lead_c))
    rows = pd.DataFrame({"epi_week": np.arange(1, PERIOD + 1, dtype="int32"), "seasonal_index": np.exp(prof),
                         "action": "none"})
    if not flat:
        rows.loc[rows["epi_week"] == camp, "action"] = "campaign"
        rows.loc[rows["epi_week"] == prep, "action"] = "preposition"   # wins if both land on the same week
    yearly = []
    for _, g in per.groupby("year"):
        if g["epi_week"].nunique() >= PERIOD:
            yp = g.groupby("epi_week")["seasonal"].mean().reindex(range(1, PERIOD + 1)).values
            yearly.append(onset_peak(yp)[0])
    summary = {"disease": disease, "onset_week": onset, "peak_week": peak, "preposition_by_week": prep,
               "campaign_by_week": camp, "supply_lead_weeks": SUPPLY_LEAD_WEEKS, "campaign_lead_weeks": lead_c,
               "peak_to_trough_ratio": float(np.exp(np.ptp(prof))),
               "onset_week_sd_across_years": float(np.std(yearly)) if len(yearly) > 1 else None,
               "years_used": len(yearly), "mean_weekly_cases": mean_weekly, "no_seasonality": flat,
               "rule": RULE_TEXT.format(lead=lead_c)}
    return rows, summary


def _run_group(pdf):
    if pdf.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in [p.split()[0] for p in OUT_SCHEMA.split(", ")]})
    rows, _ = calendar_for_series(pdf[["week_start", "cases"]], pdf["disease"].iloc[0])
    rows.insert(0, "disease", pdf["disease"].iloc[0])
    rows.insert(0, "division_code", pdf["division_code"].iloc[0])
    return rows


def division_weekly(cases_df):
    return (cases_df.groupBy("division_code", "disease", "week_start")
            .agg(Fn.sum("cases").alias("cases")))


def build_calendar(cases_df):
    """Spark: one STL per division x disease, in parallel via applyInPandas."""
    return division_weekly(cases_df).groupBy("division_code", "disease").applyInPandas(_run_group, OUT_SCHEMA)


def summaries(cases_df):
    pdf = division_weekly(cases_df).toPandas()   # small: divisions x diseases x weeks
    out = []
    for (div, dz), g in pdf.groupby(["division_code", "disease"]):
        _, s = calendar_for_series(g, dz)
        out.append({"division_code": div, **s})
    df = pd.DataFrame(out)
    for c in ("preposition_by_week", "campaign_by_week"):   # empty where no_seasonality, integers elsewhere
        df[c] = df[c].astype("Int64")
    return df


def main():
    from analytics.forecast import io
    from pipeline.spark import get_spark

    io.limit_threads()
    spark = get_spark("episcope-calendar")
    try:
        cases = io.read_gold(spark, "gold.cases_weekly")
        cal = build_calendar(cases)
        io.write_gold(cal, "gold.calendar")
        summ = summaries(cases)
    finally:
        spark.stop()
    out = io.results_dir()
    summ.to_csv(out / "calendar_summary.csv", index=False)
    (out / "calendar_summary.json").write_text(json.dumps(json.loads(summ.to_json(orient="records")), indent=2))
    print(summ[["division_code", "disease", "onset_week", "peak_week", "preposition_by_week", "campaign_by_week"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
