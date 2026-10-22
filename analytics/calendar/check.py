"""Calendar check (AN-9, report Section VI-E): the calendar's onset week against the onset seen in each year.

For every division x disease x year (2021-2025) with at least 40 weeks that have a case, weekly cases over the
division's districts are smoothed with a centred 3-week moving average and the calendar's own onset rule
(`onset_peak` on log1p) gives that year's observed onset. It is compared with the calendar's onset week in
calendar_summary.csv. Series too sparse for 40 weeks with a case (malaria outside the endemic districts) are
left out, and so are divisions the calendar marks no_seasonality.

Writes <results>/calendar_check.json: the per-row differences and, per disease, the number of division-years,
the median absolute difference in weeks and the share within two weeks.

Run: python -m analytics.calendar.check --source "SF=0.25, seed 42"   (or `make calendar-check`)
The committed file was run on the SF=0.25 build against the SF=1 calendar_summary.csv.
"""

import argparse
import json

import numpy as np
import pandas as pd

from analytics.calendar.stl_calendar import PERIOD, onset_peak

DISEASES = ("dengue", "diarrhoea", "malaria")
MIN_WEEKS_WITH_CASES = 40
YEARS = range(2021, 2026)


def yearly_onsets(cases, calendar):
    """cases: DataFrame(district_code, disease, epi_year, epi_week, cases); calendar: calendar_summary rows.
    Returns one dict per division x disease x year."""
    cases = cases[cases["disease"].isin(DISEASES)].copy()
    cases["div"] = cases["district_code"].str[:4]
    cal = {(r.division_code, r.disease): int(r.onset_week) for r in calendar.itertuples()
           if not str(r.no_seasonality).lower() == "true"}
    out = []
    for (div, dz, yr), g in cases.groupby(["div", "disease", "epi_year"]):
        w = g.groupby("epi_week")["cases"].sum().reindex(range(1, PERIOD + 1)).fillna(0).values
        c = cal.get((div, dz))
        if c is None or (w > 0).sum() < MIN_WEEKS_WITH_CASES or yr not in YEARS:
            continue
        ma = pd.Series(w).rolling(3, center=True, min_periods=1).mean().values
        onset, _ = onset_peak(np.log(ma + 1))
        d = (onset - c + PERIOD // 2) % PERIOD - PERIOD // 2
        out.append({"division": div, "disease": dz, "year": int(yr), "observed_onset": int(onset),
                    "calendar_onset": c, "diff_weeks": int(d)})
    return out


def summarise(rows):
    df = pd.DataFrame(rows)
    return {dz: {"cells": int(len(g)), "median_abs_diff_weeks": float(g["diff_weeks"].abs().median()),
                 "within_2_weeks": float((g["diff_weeks"].abs() <= 2).mean())} for dz, g in df.groupby("disease")}


def main(argv=None):
    from analytics.forecast import io
    from pipeline.spark import get_spark

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--calendar", default=None, help="calendar_summary.csv (default: <results>/calendar_summary.csv)")
    p.add_argument("--source", default="gold.cases_weekly of this checkout", help="what built the cases, for the record")
    a = p.parse_args(argv)
    out = io.results_dir()
    calendar = pd.read_csv(a.calendar or out / "calendar_summary.csv")

    io.limit_threads()
    spark = get_spark("episcope-calendar-check")
    try:
        cases = (io.read_gold(spark, "gold.cases_weekly")
                 .select("district_code", "disease", "epi_year", "epi_week", "cases").toPandas())
    finally:
        spark.stop()
    rows = yearly_onsets(cases, calendar)
    res = {"what": "calendar onset week (SF=1 calendar_summary.csv) against the onset seen in each year's weekly "
                   "counts per division (same rule on a 3-week moving average of observed cases)",
           "cases_source": a.source, "summary": summarise(rows), "rows": rows}
    (out / "calendar_check.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
