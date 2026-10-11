"""Forecast -> resource demand. Implements AN-6.

For every district, horizon and resource:
    demand_stat = sum over diseases of  factor(disease, resource) * forecast_stat(disease)
where factor is units per reported case (demand_assumptions.py), and for beds
factor = admission_share * LOS_days / 7. Every conversion is linear and non-decreasing, so applying it to a
quantile gives the quantile of demand for one disease (research.md §4.6). Adding the q90 of several diseases
assumes they peak together, which is conservative.

Output: gold.demand (docs/contracts.md): district_code, resource, horizon_weeks, demand_mean, demand_q90.
horizon_weeks = h means demand during target week h (1..4). `planning_demand` turns these into the single
4-week figure the optimiser uses (sum for consumables, busiest week for beds).

Run: python -m analytics.forecast.demand
"""

import pandas as pd
from pyspark.sql import functions as Fn

from analytics.forecast import demand_assumptions as A
from analytics.forecast import io

DEMAND_COLS = ["district_code", "resource", "horizon_weeks", "demand_mean", "demand_q90"]


def admission_shares(cases_pdf=None):
    """sum(admissions)/sum(cases) per disease from gold.cases_weekly rows; config fallback otherwise."""
    shares = dict(A.ADMISSION_SHARE_FALLBACK)
    if cases_pdf is not None and len(cases_pdf) and "admissions" in cases_pdf:
        g = cases_pdf.groupby("disease")[["admissions", "cases"]].sum()
        for dz, r in g.iterrows():
            if r["cases"] > 0 and r["admissions"] > 0:
                shares[dz] = float(r["admissions"] / r["cases"])
    return shares


def factors(shares):
    """{(disease, resource): units per reported case}"""
    f = {}
    for dz, los in A.LOS_DAYS.items():
        f[(dz, "beds")] = shares.get(dz, 0.0) * los / 7.0
    for (dz, res), (basis, rate) in A.CONVERSIONS.items():
        f[(dz, res)] = rate * (shares.get(dz, 0.0) if basis == "admission" else 1.0)
    return f


def compute_demand(forecasts, shares=None):
    """forecasts: pandas gold.forecasts rows. Returns pandas gold.demand rows, one per district x resource x
    horizon (resources with no contributing disease forecast come out as 0)."""
    fac = factors(shares or dict(A.ADMISSION_SHARE_FALLBACK))
    keys = forecasts[["district_code", "horizon"]].drop_duplicates()
    out = []
    for res in A.RESOURCES:
        parts = []
        for (dz, r), k in fac.items():
            if r != res:
                continue
            f = forecasts[forecasts["disease"] == dz]
            parts.append(pd.DataFrame({"district_code": f["district_code"], "horizon": f["horizon"],
                                       "demand_mean": k * f["mean"], "demand_q90": k * f[A.PLANNING_QUANTILE]}))
        d = pd.concat(parts) if parts else pd.DataFrame(columns=["district_code", "horizon", "demand_mean", "demand_q90"])
        d = d.groupby(["district_code", "horizon"], as_index=False)[["demand_mean", "demand_q90"]].sum()
        d = keys.merge(d, on=["district_code", "horizon"], how="left").fillna({"demand_mean": 0.0, "demand_q90": 0.0})
        d["resource"] = res
        out.append(d)
    dem = pd.concat(out, ignore_index=True).rename(columns={"horizon": "horizon_weeks"})
    dem["horizon_weeks"] = dem["horizon_weeks"].astype("int32")
    return dem[DEMAND_COLS].sort_values(["district_code", "resource", "horizon_weeks"]).reset_index(drop=True)


def planning_demand(demand, column="demand_q90"):
    """One planning figure per district x resource over the forecast window (PLANNING_AGGREGATION)."""
    rows = []
    for (d, r), g in demand.groupby(["district_code", "resource"]):
        how = A.PLANNING_AGGREGATION.get(r, "sum")
        rows.append({"district_code": d, "resource": r, "demand": float(g[column].max() if how == "max" else g[column].sum())})
    return pd.DataFrame(rows, columns=["district_code", "resource", "demand"])


def run(spark):
    fc = io.read_gold(spark, "gold.forecasts").toPandas()
    cases = (io.read_gold(spark, "gold.cases_weekly").groupBy("disease")
             .agg(Fn.sum("admissions").alias("admissions"), Fn.sum("cases").alias("cases")).toPandas())
    shares = admission_shares(cases)
    print("admission shares used:", {k: round(v, 4) for k, v in shares.items()})
    dem = compute_demand(fc, shares)
    io.write_gold(spark.createDataFrame(dem, "district_code string, resource string, horizon_weeks int, "
                                             "demand_mean double, demand_q90 double"), "gold.demand")
    return len(dem)


def main():
    from pipeline.spark import get_spark

    io.limit_threads()
    spark = get_spark("episcope-demand")
    try:
        print(f"gold.demand: {run(spark)} rows")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
