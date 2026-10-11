"""Bronze ingestion: raw S4/S5/S6 files and the reference data into Delta, unmodified. Implements ING-1
(and the bronze end of DQ-3 lineage).

`python -m pipeline.bronze.ingest [--data-dir DIR] [--lake-dir DIR]`

- Files are discovered by glob under <EPISCOPE_DATA_DIR> (layout from generator/schema.json); no file name,
  date or row count is written into the code.
- Every file's SHA-256 is checked against the ingest log (bronze._ingest_log). A file whose checksum is
  already loaded for that source is never read again, so re-running is a no-op and an incremental drop only
  adds its new files.
- Rows are kept exactly as written: every contract field is read as a string (no type inference, no
  trimming), so bronze can always replay what arrived. Parquet reference data keeps its own types.
- Each row gets source, source_file (relative to the data dir), file_checksum, batch_id and load_time. One
  run is one batch: batch_id = <UTC timestamp>-<hash of the new checksums>, so ids sort chronologically.

Partitioning: each bronze table is partitioned by batch_id, because silver reads exactly one new batch at a
time and the batch_id partition lets it prune to that batch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys

from pipeline.bronze import lake

SCHEMA_FILE = lake.ROOT / "generator" / "schema.json"
LOG = "_ingest_log"


def contract_fields(section: str) -> list[str]:
    spec = json.loads(SCHEMA_FILE.read_text())
    return [f["name"] for f in spec[section]["fields"]]


# source -> (glob relative to the data dir, reader format, contract section or None for header-defined CSV)
SOURCES = {
    "encounters": ("raw/encounters/report_date=*/*.csv", "csv", "S4_encounters"),
    "facilities": ("raw/facilities/*.csv", "csv", "S5_facilities"),
    "stock": ("raw/stock/snapshot_date=*/*.json", "json", "S6_stock"),
    "ref_districts": ("reference/districts.csv", "csv", None),
    "ref_population": ("reference/population_age_sex.csv", "csv", None),
    "ref_weather": ("reference/weather_daily.parquet", "parquet", None),
}


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover(data: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Non-empty files per source, sorted, found by glob. Underscore/dot files (manifests) are skipped."""
    out = {}
    for source, (pattern, _, _) in SOURCES.items():
        files = sorted(p for p in data.glob(pattern)
                       if p.is_file() and p.stat().st_size > 0 and not p.name.startswith(("_", ".")))
        out[source] = files
    return out


def loaded_checksums(spark) -> set[tuple[str, str]]:
    if not lake.exists("bronze", LOG):
        return set()
    rows = lake.read(spark, "bronze", LOG).select("source", "file_checksum").distinct().collect()
    return {(r["source"], r["file_checksum"]) for r in rows}


def plan(spark, data: pathlib.Path):
    """[(source, path, checksum)] for files whose checksum is not yet loaded (first path wins for twins)."""
    done = loaded_checksums(spark)
    new = []
    for source, files in discover(data).items():
        seen = set()
        for p in files:
            c = sha256(p)
            if (source, c) in done or c in seen:
                continue
            seen.add(c)
            new.append((source, p, c))
    return new


def make_batch_id(checksums) -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256("".join(sorted(checksums)).encode()).hexdigest()[:8]
    return f"{ts}-{digest}"


def _read(spark, fmt: str, section: str | None, paths: list[str]):
    from pyspark.sql.types import StringType, StructField, StructType

    if fmt == "parquet":
        return spark.read.parquet(*paths)
    reader = spark.read.option("recursiveFileLookup", "true")  # no partition-column inference from paths
    if fmt == "csv":
        reader = reader.option("header", "true").option("mode", "PERMISSIVE")
        if section:
            reader = reader.schema(StructType([StructField(c, StringType()) for c in contract_fields(section)]))
        else:
            reader = reader.option("inferSchema", "false")
        return reader.csv(paths)
    schema = StructType([StructField(c, StringType()) for c in contract_fields(section)])
    return reader.schema(schema).json(paths)


def ingest(spark, data: pathlib.Path | None = None) -> dict:
    """Load every new file into bronze. Returns {batch_id, files, rows: {source: n}} (batch_id None if nothing new)."""
    from pyspark.sql import functions as F

    data = (data or lake.data_dir()).resolve()
    new = plan(spark, data)
    if not new:
        return {"batch_id": None, "files": 0, "rows": {}}
    batch_id = make_batch_id(c for _, _, c in new)
    # The explicit file list is already known: list it on the driver instead of launching one Spark task per
    # file (Spark's parallel listing starts above 32 paths; a full load has thousands of daily files).
    spark.conf.set("spark.sql.sources.parallelPartitionDiscovery.threshold", str(len(new) + 1))
    load_time = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = {}
    log_rows = []
    for source in SOURCES:
        items = [(p, c) for s, p, c in new if s == source]
        if not items:
            continue
        _, fmt, section = SOURCES[source]
        files = spark.createDataFrame(
            [(str(p.resolve()), str(p.resolve().relative_to(data)), c, p.stat().st_size) for p, c in items],
            "local_path string, source_file string, file_checksum string, file_bytes long")
        df = _read(spark, fmt, section, [str(p) for p, _ in items])
        # _metadata.file_path is a URI (file:/a/b%20c.csv); a literal '+' must survive url_decode
        local = F.url_decode(F.regexp_replace(F.regexp_replace(F.col("_metadata.file_path"), r"^file:/+", "/"),
                                              r"\+", "%2B"))
        df = df.select("*", local.alias("local_path"))
        out = (df.join(F.broadcast(files.drop("file_bytes")), "local_path", "left").drop("local_path")
                 .withColumn("source", F.lit(source))
                 .withColumn("batch_id", F.lit(batch_id))
                 .withColumn("load_time", F.lit(load_time).cast("timestamp")))
        lake.write(out, "bronze", source, mode="append", partition_by=["batch_id"])
        written = (lake.read(spark, "bronze", source).where(F.col("batch_id") == batch_id)
                   .groupBy("source_file").count().collect())
        per_file = {r["source_file"]: r["count"] for r in written}
        unmatched = per_file.pop(None, 0)
        if unmatched:
            raise RuntimeError(f"{source}: {unmatched} rows could not be traced to a file checksum")
        rows[source] = sum(per_file.values())
        for p, c in items:
            rel = str(p.resolve().relative_to(data))
            log_rows.append((source, rel, c, p.stat().st_size, per_file.get(rel, 0), batch_id, load_time))
    log = spark.createDataFrame(log_rows, "source string, source_file string, file_checksum string, "
                                          "file_bytes long, rows long, batch_id string, load_time timestamp")
    lake.write(log, "bronze", LOG, mode="append")
    return {"batch_id": batch_id, "files": len(new), "rows": rows}


def latest_batch(spark, source: str):
    """batch_id of the most recent bronze load of a source, or None."""
    from pyspark.sql import functions as F

    if not lake.exists("bronze", LOG):
        return None
    r = lake.read(spark, "bronze", LOG).where(F.col("source") == source).agg(F.max("batch_id").alias("b")).first()
    return r["b"] if r else None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", help="default EPISCOPE_DATA_DIR")
    p.add_argument("--lake-dir", help="default EPISCOPE_LAKE_DIR")
    a = p.parse_args(argv)
    lake.load_env()
    if a.data_dir:
        os.environ["EPISCOPE_DATA_DIR"] = a.data_dir
    if a.lake_dir:
        os.environ["EPISCOPE_LAKE_DIR"] = a.lake_dir
    data = lake.data_dir()
    if not (data / "raw").exists():
        sys.exit(f"no raw data under {data}/raw: run `make generate` first")
    spark = lake.spark_session("bronze_ingest")
    try:
        res = ingest(spark, data)
    finally:
        spark.stop()
    if res["batch_id"] is None:
        print("bronze: nothing new (every file checksum already loaded)")
    else:
        print(f"bronze: batch {res['batch_id']}: {res['files']} new files, rows {res['rows']}")
    return res


if __name__ == "__main__":
    main()
