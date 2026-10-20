"""Gold marts for analytics and serving. Implements ING-3 (marts, partitioning), DQ-3 (lineage, dq_report) and
uses DQ-4 (patient de-duplication) when counting.

`python -m pipeline.gold.build [--full]`

Tables (docs/contracts.md), Delta at <EPISCOPE_LAKE_DIR>/gold/<name>:
    cases_weekly    district x disease x ISO week of ONSET. cases / admissions / deaths / lab_confirmed exclude
                    DQ-4 duplicates. reporting_facilities = facilities with any encounter (any diagnosis) with
                    onset that week; expected_facilities = facilities on the clean register. Every district-week
                    with at least one reporting facility gets a row for all four diseases (zero where none), so
                    "no row" means "nothing reported" (missing), which AN-3 relies on. batch_ids = the bronze
                    batches of the rows counted (lineage gold -> silver.batch_id -> bronze._ingest_log).
    weather_weekly  district x ISO week from the latest reference weather batch (skipped if there is none).
    stock_latest    district x resource from each facility's latest snapshot; beds = free beds (beds - occupied).
    dq_report       one row per batch and source, from silver._dq_log.
    _snapshots      ING-7 ledger: Delta version of cases_weekly -> latest report_date it includes.

Incremental (ING-4): cases_weekly rebuilds only the weeks listed in silver._changes by batches gold has not yet
applied, plus the week after each (a new record can turn a later record into a DQ-4 duplicate), using a Delta
replaceWhere on exactly those weeks. --full, or a missing table, rebuilds everything.

Partitioning: cases_weekly and weather_weekly by epi_year only. A year holds at most 64 x 4 x 53 rows, so a
second key (week, district) would only create tiny files; the incremental rebuild prunes by epi_year and then
filters on week_start. stock_latest and dq_report have no epi_year and are small: unpartitioned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from pipeline.bronze import lake
from pipeline.bronze.ingest import latest_batch
from pipeline.silver.build import CHANGES, DQ_LOG
from pipeline.silver.dedup import WINDOW_DAYS, flag_duplicates
from pipeline.silver.transform import DISEASES, with_epi_week

APPLIED = "_applied"
SNAPSHOTS = "_snapshots"
RESOURCES = {  # gold resource -> silver.stock column (beds handled separately)
    "iv_saline": "iv_saline_units", "ns1_kits": "ns1_kits", "ors": "ors_packets",
    "antimalarials": "antimalarial_courses",
}
CASES_COLS = ["district_code", "division_code", "disease", "epi_year", "epi_week", "week_start", "cases",
              "admissions", "deaths", "lab_confirmed", "reporting_facilities", "expected_facilities", "batch_ids"]


# ------------------------------------------------------------------------------------------------ ledger

def applied(spark, kind: str) -> set[str]:
    if not lake.exists("gold", APPLIED):
        return set()
    return {r["batch_id"] for r in lake.read(spark, "gold", APPLIED).where(F.col("kind") == kind).collect()}


def mark_applied(spark, kind: str, batch_ids) -> None:
    batch_ids = sorted(set(batch_ids))
    if not batch_ids:
        return
    df = spark.createDataFrame([(kind, b) for b in batch_ids], "kind string, batch_id string") \
        .withColumn("applied_at", F.current_timestamp())
    if lake.exists("gold", APPLIED):
        (lake.delta_table(spark, "gold", APPLIED).alias("t")
         .merge(df.alias("s"), "t.kind = s.kind AND t.batch_id = s.batch_id")
         .whenNotMatchedInsertAll().execute())
    else:
        lake.write(df, "gold", APPLIED)


# ------------------------------------------------------------------------------------------------ cases_weekly

def cases_for(spark, weeks: list | None) -> DataFrame:
    """gold.cases_weekly rows for the given week_starts (None = every week)."""
    enc = lake.read(spark, "silver", "encounters")
    if weeks is not None:
        halo = sorted(set(weeks) | {w - dt.timedelta(days=7) for w in weeks})
        years = sorted({w.isocalendar()[0] for w in halo} | {w.year for w in halo})
        enc = enc.where(F.col("epi_year").isin(years) & F.col("week_start").isin(halo))
        target = F.col("week_start").isin(weeks)
    else:
        target = F.lit(True)
    enc = enc.where(F.col("district_code").isNotNull())
    # DQ-4: duplicates need the previous week as context, but only target weeks are counted
    flagged = flag_duplicates(enc.where(F.col("disease").isin(*DISEASES)), WINDOW_DAYS).where(target)
    in_target = enc.where(target)

    dw = in_target.groupBy("district_code", "week_start").agg(
        F.countDistinct("facility_id").alias("reporting_facilities"),
        F.array_sort(F.collect_set("batch_id")).alias("dw_batches"))
    cells = flagged.where(F.col("dup_of").isNull()).groupBy("district_code", "disease", "week_start").agg(
        F.count(F.lit(1)).alias("cases"),
        F.sum(F.col("admitted").cast("int")).alias("admissions"),
        F.sum((F.col("outcome") == "died").cast("int")).alias("deaths"),
        F.sum(F.col("lab_confirmed").cast("int")).alias("lab_confirmed"),
        F.array_sort(F.collect_set("batch_id")).alias("cell_batches"))
    diseases = spark.createDataFrame([(d,) for d in DISEASES], "disease string")
    expected = lake.read(spark, "silver", "facilities").where(F.col("district_code").isNotNull()) \
        .groupBy("district_code").agg(F.countDistinct("facility_id").alias("expected_facilities"))
    div = lake.read(spark, "silver", "districts").select("district_code", "division_code")

    grid = (dw.crossJoin(diseases)
              .join(cells, ["district_code", "disease", "week_start"], "left")
              .join(F.broadcast(expected), "district_code", "left")
              .join(F.broadcast(div), "district_code", "left"))
    return grid.select(
        "district_code", "division_code", "disease",
        F.expr("extract(YEAROFWEEK FROM week_start)").cast("int").alias("epi_year"),
        F.weekofyear("week_start").cast("int").alias("epi_week"),
        "week_start",
        *[F.coalesce(F.col(c), F.lit(0)).cast("long").alias(c) for c in ("cases", "admissions", "deaths", "lab_confirmed")],
        F.col("reporting_facilities").cast("long").alias("reporting_facilities"),
        F.coalesce("expected_facilities", F.lit(0)).cast("long").alias("expected_facilities"),
        F.coalesce("cell_batches", "dw_batches").alias("batch_ids"),
    ).select(*CASES_COLS)


def build_cases(spark, full: bool = False) -> dict:
    if not lake.exists("silver", CHANGES):
        return {"mode": "skip", "weeks": 0}
    changes = lake.read(spark, "silver", CHANGES)
    done = applied(spark, "changes")
    pending = sorted({r[0] for r in changes.select("batch_id").distinct().collect()} - done)
    if full or not lake.exists("gold", "cases_weekly"):
        df = cases_for(spark, None)
        lake.write(df, "gold", "cases_weekly", partition_by=["epi_year"])
        mark_applied(spark, "changes", {r[0] for r in changes.select("batch_id").distinct().collect()})
        return {"mode": "full", "weeks": df.select("week_start").distinct().count()}
    if not pending:
        return {"mode": "incremental", "weeks": 0}
    changed = {r[0] for r in changes.where(F.col("batch_id").isin(pending)).select("week_start").distinct().collect()}
    weeks = sorted(changed | {w + dt.timedelta(days=WINDOW_DAYS) for w in changed})
    df = cases_for(spark, weeks)
    years = sorted({w.isocalendar()[0] for w in weeks})
    where = (f"epi_year IN ({', '.join(str(y) for y in years)}) AND week_start IN ("
             + ", ".join(f"DATE'{w.isoformat()}'" for w in weeks) + ")")
    lake.write(df, "gold", "cases_weekly", mode="overwrite", replace_where=where)
    mark_applied(spark, "changes", pending)
    return {"mode": "incremental", "weeks": len(weeks), "first": weeks[0].isoformat(), "last": weeks[-1].isoformat()}


# ------------------------------------------------------------------------------------------------ others

def build_weather(spark, full: bool = False) -> dict:
    b = latest_batch(spark, "ref_weather")
    if b is None:
        return {"mode": "skip", "reason": "no reference weather in bronze (generator used synthetic climate)"}
    if not full and lake.exists("gold", "weather_weekly") and b in applied(spark, "weather"):
        return {"mode": "unchanged", "batch_id": b}
    w = lake.read(spark, "bronze", "ref_weather").where(F.col("batch_id") == b).select(
        F.col("district_code").cast("string").alias("district_code"),
        F.col("date").cast("date").alias("date"),
        F.col("precip_mm").cast("double").alias("precip_mm"),
        F.col("tmean_c").cast("double").alias("tmean_c"),
        F.col("rh_pct").cast("double").alias("rh_pct"))
    weekly = with_epi_week(w.where(F.col("date").isNotNull()), "date").groupBy("district_code", "epi_year", "epi_week") \
        .agg(F.sum("precip_mm").alias("precip_mm_sum"), F.avg("tmean_c").alias("tmean_c_avg"),
             F.avg("rh_pct").alias("rh_pct_avg"))
    lake.write(weekly, "gold", "weather_weekly", partition_by=["epi_year"])
    mark_applied(spark, "weather", [b])
    return {"mode": "full", "batch_id": b}


def build_stock(spark) -> dict:
    if not lake.exists("silver", "stock"):
        return {"mode": "skip"}
    from pyspark.sql import Window

    st = lake.read(spark, "silver", "stock")
    w = Window.partitionBy("facility_id").orderBy(F.col("snapshot_date").desc())
    latest = st.withColumn("_r", F.row_number().over(w)).where("_r = 1").drop("_r")
    fac = lake.read(spark, "silver", "facilities").select("facility_id", "district_code", "beds")
    j = latest.join(fac, "facility_id", "inner")
    parts = [j.select("district_code", F.lit(r).alias("resource"), F.col(c).alias("stock"), "snapshot_date")
             for r, c in RESOURCES.items()]
    parts.append(j.select("district_code", F.lit("beds").alias("resource"),
                          F.greatest(F.col("beds") - F.col("occupied_beds"), F.lit(0)).alias("stock"),
                          "snapshot_date"))
    out = parts[0]
    for p in parts[1:]:
        out = out.unionByName(p)
    out = out.groupBy("district_code", "resource").agg(
        F.sum(F.coalesce("stock", F.lit(0))).cast("long").alias("stock"), F.max("snapshot_date").alias("as_of"))
    lake.write(out, "gold", "stock_latest")
    return {"mode": "full", "rows": out.count()}


def build_dq(spark) -> dict:
    if not lake.exists("silver", DQ_LOG):
        return {"mode": "skip"}
    dq = lake.read(spark, "silver", DQ_LOG).select("batch_id", "source", "rows_in", "rows_clean",
                                                   "rows_quarantined", "rule_pass_rates", "loaded_at")
    lake.write(dq, "gold", "dq_report")
    return {"mode": "full", "rows": dq.count()}


# ------------------------------------------------------------------------------------------------ snapshots

def record_snapshot(spark) -> dict | None:
    """ING-7: note which Delta version of gold.cases_weekly holds the data known through which report date.
    One row per version in gold._snapshots; pipeline/gold/asof.py reads it to answer "what did we know on date t".
    A re-run that wrote nothing new leaves the version, and so the ledger, unchanged."""
    if not lake.exists("gold", "cases_weekly"):
        return None
    version = lake.delta_table(spark, "gold", "cases_weekly").history(1).first()["version"]
    if lake.exists("gold", SNAPSHOTS) and \
            lake.read(spark, "gold", SNAPSHOTS).where(F.col("version") == version).limit(1).count():
        return {"version": version, "new": False}
    through = lake.read(spark, "silver", "encounters").agg(F.max("report_date")).first()[0]
    row = spark.createDataFrame([(version, through)], "version long, data_through date") \
        .withColumn("recorded_at", F.current_timestamp())
    lake.write(row, "gold", SNAPSHOTS, mode="append")
    return {"version": version, "data_through": str(through), "new": True}


def build(spark, full: bool = False) -> dict:
    res = {"cases_weekly": build_cases(spark, full), "weather_weekly": build_weather(spark, full),
           "stock_latest": build_stock(spark), "dq_report": build_dq(spark)}
    res["snapshot"] = record_snapshot(spark)
    return res


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--full", action="store_true", help="rebuild every week instead of the changed ones")
    a = p.parse_args(argv)
    lake.load_env()
    if not lake.exists("silver", "encounters"):
        sys.exit("silver.encounters is missing: run `make silver` first")
    spark = lake.spark_session("gold_build")
    try:
        res = build(spark, a.full)
    finally:
        spark.stop()
    for k, v in res.items():
        print(f"gold.{k}: {v}")
    return res


if __name__ == "__main__":
    main()
