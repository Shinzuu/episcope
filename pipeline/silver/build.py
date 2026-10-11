"""Silver build: quality rules, quarantine, de-identification and idempotent MERGE of every new bronze batch.
Implements ING-2, ING-4, DQ-1, DQ-2, DQ-3, DQ-4 (flag support) and PRV-1.

`python -m pipeline.silver.build [--reprocess]`

Order per run (each bronze batch once, oldest first; bronze._ingest_log says what exists, silver._batches
what is done):
1. Reference: the latest bronze batch of districts and population becomes silver.districts /
   silver.population (typed, overwritten; small tables).
2. Facilities (S5) first: apply_rules(facility_rules) on the latest facilities batch -> silver.facilities.
   The orphan and bed-capacity rules of S4/S6 join against this clean register.
3. Encounters (S4): with_facility_lookup, apply_rules(encounter_rules). Quarantined rows are de-identified with
   deidentify(keep_age=True) and written to silver.quarantine. Clean rows are typed, de-identified
   (patient_id -> patient_key HMAC, age -> age_band), mapped to a disease bucket and an ISO epi-week, given
   their district and division, then MERGEd into silver.encounters on encounter_id. A matched row is replaced
   only when the incoming report_date is later, so re-running a batch changes nothing (ING-4) and a resubmitted
   or late record (up to 21 days, or more) moves into its onset week.
4. Stock (S6): the same rules path, MERGE into silver.stock on (facility_id, snapshot_date).
5. The weeks each encounter batch touched (its rows' onset weeks plus the old weeks of rows it replaced) go to
   silver._changes; gold rebuilds only those weeks. One dq_report row per batch and source goes to
   silver._dq_log (gold.dq_report reads it).

Partitioning: silver.encounters is partitioned by epi_year only: gold rebuilds by week and the MERGE prunes to
the years in the batch, while a finer key (district or week) would make thousands of small files at SF=1.
silver.stock is small and unpartitioned. The patient_id column never leaves this job (PRV-1).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from pipeline.bronze import lake
from pipeline.bronze.ingest import LOG as BRONZE_LOG, contract_fields, latest_batch
from pipeline.privacy import deidentify
from pipeline.quality import (REASON_CODES, apply_rules, dq_report, encounter_rules, facility_rules,
                              load_district_codes, stock_rules, with_facility_lookup)
from pipeline.silver import transform as T

BATCHES, CHANGES, DQ_LOG = "_batches", "_changes", "_dq_log"
QUARANTINE = "quarantine"
RECORD_KEY = {"encounters": "encounter_id", "facilities": "facility_id", "stock": "facility_id"}


# ------------------------------------------------------------------------------------------------ ledgers

def pending_batches(spark, source: str, reprocess: bool = False) -> list[str]:
    if not lake.exists("bronze", BRONZE_LOG):
        return []
    log = lake.read(spark, "bronze", BRONZE_LOG).where(F.col("source") == source)
    all_b = sorted(r["batch_id"] for r in log.select("batch_id").distinct().collect())
    if reprocess or not lake.exists("silver", BATCHES):
        return all_b
    done = {r["batch_id"] for r in lake.read(spark, "silver", BATCHES).where(F.col("source") == source)
            .select("batch_id").collect()}
    return [b for b in all_b if b not in done]


def mark_done(spark, source: str, batch_id: str) -> None:
    row = spark.createDataFrame([(source, batch_id, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))],
                                "source string, batch_id string, processed_at timestamp")
    _replace(row, "silver", BATCHES, f"source = {lake.sql_str(source)} AND batch_id = {lake.sql_str(batch_id)}")


def _replace(df: DataFrame, layer: str, name: str, where: str, partition_by=None) -> None:
    """Idempotent write of one slice: replace the rows matching `where` (create the table if needed)."""
    if lake.exists(layer, name):
        lake.write(df, layer, name, mode="overwrite", replace_where=where)
    else:
        lake.write(df, layer, name, mode="overwrite", partition_by=partition_by)


def bronze_batch(spark, source: str, batch_id: str, section: str) -> DataFrame:
    """Contract columns (strings, as ingested) of one bronze batch, plus bronze_file for lineage."""
    b = lake.read(spark, "bronze", source).where(F.col("batch_id") == batch_id)
    return b.select(*contract_fields(section), F.col("source_file").alias("bronze_file"))


# ------------------------------------------------------------------------------------------------ outputs

def write_quarantine(quar: DataFrame, source: str, batch_id: str) -> int:
    if source == "encounters":
        quar = deidentify(quar, keep_age=True)  # PRV-1 applies to quarantine too; exact age kept for review
    meta = {REASON_CODES, "batch_id", "source", "quarantined_at"}
    payload = [c for c in quar.columns if c not in meta]
    out = quar.select(
        F.lit(source).alias("source"), F.col("batch_id"),
        F.col(RECORD_KEY[source]).cast("string").alias("record_key"),
        F.col(REASON_CODES).alias("reason_codes"),
        F.to_json(F.struct(*payload)).alias("record"),
        F.col("quarantined_at"),
    )
    _replace(out, "silver", QUARANTINE, f"source = {lake.sql_str(source)} AND batch_id = {lake.sql_str(batch_id)}")
    return out.count()


def write_dq(report: DataFrame, source: str, batch_id: str) -> None:
    _replace(report, "silver", DQ_LOG, f"source = {lake.sql_str(source)} AND batch_id = {lake.sql_str(batch_id)}")


# ------------------------------------------------------------------------------------------------ reference

def build_reference(spark) -> None:
    b = latest_batch(spark, "ref_districts")
    if b is None:
        raise SystemExit("no districts reference in bronze: run `make bronze` with data/reference present")
    d = lake.read(spark, "bronze", "ref_districts").where(F.col("batch_id") == b).select(
        "district_code", "district_name", "division_code", "division_name",
        F.col("lat").try_cast("double").alias("lat"), F.col("lon").try_cast("double").alias("lon"),
        F.col("population_2022").try_cast("long").alias("population_2022"),
        F.col("area_km2").try_cast("double").alias("area_km2"), F.lit(b).alias("batch_id"))
    lake.write(d, "silver", "districts")
    p = latest_batch(spark, "ref_population")
    if p is not None:
        pop = lake.read(spark, "bronze", "ref_population").where(F.col("batch_id") == p).select(
            "district_code", "age_band", "sex", F.col("population").try_cast("long").alias("population"),
            F.lit(p).alias("batch_id"))
        lake.write(pop, "silver", "population")


def district_codes(spark) -> list[str]:
    """Valid pcodes from the reference districts (silver copy; falls back to the CSV)."""
    if lake.exists("silver", "districts"):
        codes = sorted(r[0] for r in lake.read(spark, "silver", "districts").select("district_code").distinct()
                       .collect() if r[0])
        if codes:
            return codes
    return load_district_codes(lake.data_dir() / "reference" / "districts.csv")


# ------------------------------------------------------------------------------------------------ facilities

def build_facilities(spark, reprocess: bool) -> list[str]:
    pending = pending_batches(spark, "facilities", reprocess)
    if not pending and lake.exists("silver", "facilities"):
        return []
    b = latest_batch(spark, "facilities")
    if b is None:
        raise SystemExit("no facilities in bronze: the S5 register is required before encounters")
    raw = bronze_batch(spark, "facilities", b, "S5_facilities")
    rules = facility_rules(district_codes(spark))
    clean, quar = apply_rules(raw, rules, b, "facilities")
    lake.write(T.type_facilities(clean).withColumn("batch_id", F.lit(b)), "silver", "facilities")
    write_quarantine(quar, "facilities", b)
    write_dq(dq_report(raw, rules, b, "facilities"), "facilities", b)
    for x in pending:  # older facility batches are superseded by the latest register
        mark_done(spark, "facilities", x)
    return pending


def facilities(spark) -> DataFrame:
    return lake.read(spark, "silver", "facilities")


# ------------------------------------------------------------------------------------------------ encounters

def clean_encounters(clean: DataFrame, fac: DataFrame, districts: DataFrame, batch_id: str) -> DataFrame:
    typed = T.type_encounters(clean)
    deid = deidentify(typed)  # patient_id -> patient_key; age_years -> age_band
    out = T.with_epi_week(deid.withColumn("disease", T.disease_of("icd10_code")))
    fac_d = fac.select("facility_id", "district_code")
    div = districts.select("district_code", "division_code")
    return (out.join(F.broadcast(fac_d), "facility_id", "left")
               .join(F.broadcast(div), "district_code", "left")
               .withColumn("batch_id", F.lit(batch_id))
               .withColumn("silver_loaded_at", F.current_timestamp()))


SILVER_ENC_COLS = ["encounter_id", "patient_key", "facility_id", "district_code", "division_code", "onset_date",
                   "report_date", "age_band", "sex", "icd10_code", "disease", "lab_confirmed", "admitted",
                   "outcome", "epi_year", "epi_week", "week_start", "batch_id", "bronze_file", "silver_loaded_at"]


def merge_encounters(spark, src: DataFrame, batch_id: str) -> DataFrame:
    """MERGE src into silver.encounters on encounter_id, keeping the latest report_date (ING-4).
    Returns the distinct (epi_year, epi_week, week_start) the batch changed: new and replaced rows' onset weeks
    and the old weeks of replaced rows."""
    src = src.select(*SILVER_ENC_COLS).persist()
    weeks = src.select("epi_year", "epi_week", "week_start")
    if not lake.exists("silver", "encounters"):
        lake.write(src, "silver", "encounters", partition_by=["epi_year"])
        changed = weeks.distinct()
    else:
        tgt = lake.read(spark, "silver", "encounters")
        years = sorted({r[0] for r in src.select("epi_year").distinct().collect() if r[0] is not None})
        prune = sorted(set(years) | {y - 1 for y in years} | {y + 1 for y in years})
        tgt_p = tgt.where(F.col("epi_year").isin(prune)) if prune else tgt
        s, t = src.alias("s"), tgt_p.alias("t")
        joined = s.join(t, F.col("s.encounter_id") == F.col("t.encounter_id"), "left")
        effective = joined.where(F.col("t.encounter_id").isNull() | (F.col("s.report_date") > F.col("t.report_date")))
        changed = (effective.select("s.epi_year", "s.epi_week", "s.week_start")
                   .unionByName(effective.where(F.col("t.encounter_id").isNotNull())
                                .select("t.epi_year", "t.epi_week", "t.week_start"))
                   .distinct().localCheckpoint())
        year_list = ", ".join(str(y) for y in prune) if prune else "NULL"
        (lake.delta_table(spark, "silver", "encounters").alias("t")
         .merge(src.alias("s"), f"t.epi_year IN ({year_list}) AND t.encounter_id = s.encounter_id")
         .whenMatchedUpdateAll(condition="s.report_date > t.report_date")
         .whenNotMatchedInsertAll()
         .execute())
    changed = changed.where(F.col("week_start").isNotNull()).withColumn("batch_id", F.lit(batch_id))
    _replace(changed, "silver", CHANGES, f"batch_id = {lake.sql_str(batch_id)}")
    src.unpersist()
    return changed


def process_encounters(spark, batch_id: str) -> dict:
    raw = bronze_batch(spark, "encounters", batch_id, "S4_encounters")
    fac = facilities(spark)
    enc = with_facility_lookup(raw, fac).persist()
    rules = encounter_rules()
    clean, quar = apply_rules(enc, rules, batch_id, "encounters")
    report = dq_report(enc, rules, batch_id, "encounters")
    nq = write_quarantine(quar, "encounters", batch_id)
    write_dq(report, "encounters", batch_id)
    src = clean_encounters(clean, fac, lake.read(spark, "silver", "districts"), batch_id)
    changed = merge_encounters(spark, src, batch_id)
    n_weeks = changed.count()
    enc.unpersist()
    return {"quarantined": nq, "weeks_changed": n_weeks}


# ------------------------------------------------------------------------------------------------ stock

def process_stock(spark, batch_id: str) -> dict:
    raw = bronze_batch(spark, "stock", batch_id, "S6_stock")
    st = with_facility_lookup(raw, facilities(spark)).persist()
    rules = stock_rules()
    clean, quar = apply_rules(st, rules, batch_id, "stock")
    nq = write_quarantine(quar, "stock", batch_id)
    write_dq(dq_report(st, rules, batch_id, "stock"), "stock", batch_id)
    src = T.type_stock(clean).withColumn("batch_id", F.lit(batch_id)).persist()
    if not lake.exists("silver", "stock"):
        lake.write(src, "silver", "stock")
    else:
        (lake.delta_table(spark, "silver", "stock").alias("t")
         .merge(src.alias("s"), "t.facility_id = s.facility_id AND t.snapshot_date = s.snapshot_date")
         .whenNotMatchedInsertAll()
         .execute())
    src.unpersist()
    st.unpersist()
    return {"quarantined": nq}


# ------------------------------------------------------------------------------------------------ driver

def build(spark, reprocess: bool = False) -> dict:
    build_reference(spark)
    summary = {"facilities": build_facilities(spark, reprocess), "encounters": {}, "stock": {}}
    for b in pending_batches(spark, "encounters", reprocess):
        summary["encounters"][b] = process_encounters(spark, b)
        mark_done(spark, "encounters", b)
    for b in pending_batches(spark, "stock", reprocess):
        summary["stock"][b] = process_stock(spark, b)
        mark_done(spark, "stock", b)
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--reprocess", action="store_true",
                   help="run every bronze batch again (a no-op on the data: proves ING-4 idempotency)")
    a = p.parse_args(argv)
    lake.load_env()
    if not lake.exists("bronze", BRONZE_LOG):
        sys.exit("bronze is empty: run `make bronze` first")
    spark = lake.spark_session("silver_build")
    try:
        s = build(spark, a.reprocess)
    finally:
        spark.stop()
    print(f"silver: facilities batches {len(s['facilities'])}, encounter batches {len(s['encounters'])}, "
          f"stock batches {len(s['stock'])}")
    for b, r in s["encounters"].items():
        print(f"  encounters {b}: quarantined {r['quarantined']}, epi-weeks changed {r['weeks_changed']}")
    return s


if __name__ == "__main__":
    main()
