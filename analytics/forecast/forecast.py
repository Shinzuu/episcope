"""Spark forecast job. Implements AN-4: 1-4-week-ahead weekly counts per district for dengue and diarrhoea.

Cases and weather are cogrouped by district and passed to applyInPandas, so each district's models are
trained in parallel on the executors (one NB2 GLM per district x disease x horizon, see model.py).
The origin is the latest week in gold.cases_weekly, derived from the data, never hard-coded.

Output: gold.forecasts (docs/contracts.md): district_code, disease, origin_week_start, horizon,
target_week_start, mean, q10, q50, q90.

Run: python -m analytics.forecast.forecast
"""

import pandas as pd
from pyspark.sql import functions as Fn

from analytics.forecast import features as F
from analytics.forecast import io
from analytics.forecast.model import forecast_series, qcol

# Brief AN-4 asks for dengue and diarrhoea. Malaria is added so AN-6 can size antimalarials from a forecast.
FORECAST_DISEASES = ("dengue", "diarrhoea", "malaria")

OUT_SCHEMA = ("district_code string, disease string, origin_week_start date, horizon int, "
              "target_week_start date, mean double, q10 double, q50 double, q90 double, method string")
GOLD_COLS = ["district_code", "disease", "origin_week_start", "horizon", "target_week_start",
             "mean", "q10", "q50", "q90"]
WEATHER_SCHEMA = "district_code string, epi_year int, epi_week int, precip_mm_sum double, tmean_c_avg double"


OUT_SCHEMA_COLS = [p.split()[0] for p in OUT_SCHEMA.split(", ")]


def _empty_out():
    return pd.DataFrame({c: pd.Series(dtype="object") for c in OUT_SCHEMA_COLS})


def make_district_forecaster(diseases, start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)

    def run_district(cases, weather):
        if cases.empty:
            return _empty_out()
        district = cases["district_code"].iloc[0]
        out = []
        for dz in diseases:
            sub = cases[cases["disease"] == dz]
            grid = F.weekly_grid(sub, weather, start, end)
            fc = forecast_series(grid, [end])
            if fc.empty:
                continue
            fc["district_code"], fc["disease"] = district, dz
            out.append(fc)
        if not out:
            return _empty_out()
        df = pd.concat(out, ignore_index=True)
        df = df.rename(columns={qcol(0.1): "q10", qcol(0.5): "q50", qcol(0.9): "q90"})
        for c in ("origin_week_start", "target_week_start"):
            df[c] = pd.to_datetime(df[c]).dt.date
        df["horizon"] = df["horizon"].astype("int32")
        return df[OUT_SCHEMA_COLS]

    return run_district


def build_forecasts(spark, cases_df, weather_df=None, diseases=FORECAST_DISEASES):
    """Returns a Spark DataFrame with the gold.forecasts columns plus `method`."""
    c = (cases_df.filter(Fn.col("disease").isin(list(diseases)))
         .select("district_code", "disease", "week_start", "cases", "reporting_facilities", "expected_facilities"))
    b = c.agg(Fn.min("week_start").alias("s"), Fn.max("week_start").alias("e")).first()
    if b is None or b["e"] is None:
        return spark.createDataFrame([], OUT_SCHEMA)
    if weather_df is None:
        weather_df = spark.createDataFrame([], WEATHER_SCHEMA)
    w = weather_df.select("district_code", "epi_year", "epi_week", "precip_mm_sum", "tmean_c_avg")
    fn = make_district_forecaster(list(diseases), b["s"], b["e"])
    return c.groupBy("district_code").cogroup(w.groupBy("district_code")).applyInPandas(fn, OUT_SCHEMA)


def run(spark):
    cases = io.read_gold(spark, "gold.cases_weekly")
    weather = io.read_gold(spark, "gold.weather_weekly") if io.gold_exists("gold.weather_weekly") else None
    fc = build_forecasts(spark, cases, weather).cache()
    for r in fc.groupBy("method").count().collect():
        print(f"forecast method {r['method']}: {r['count']} rows")
    io.write_gold(fc.select(*GOLD_COLS), "gold.forecasts")
    n = fc.count()
    fc.unpersist()
    return n


def main():
    io.limit_threads()
    from pipeline.spark import get_spark

    spark = get_spark("episcope-forecast")
    try:
        print(f"gold.forecasts: {run(spark)} rows")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
