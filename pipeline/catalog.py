"""Register every lake table in a Hive metastore catalog. Implements ING-3 (catalog registration).

`python -m pipeline.catalog [--list]`

Each Delta table at <EPISCOPE_LAKE_DIR>/<layer>/<name> becomes an external table <layer>.<name> in a Derby-backed
Hive metastore kept inside the lake (<lake>/_catalog/metastore). The data stays where the jobs wrote it; the
catalog only stores the name, location and schema, so registering is cheap and re-running it changes nothing.
Bookkeeping tables whose folder starts with an underscore (_ingest_log, _batches, _changes, _dq_log, _applied)
are registered without the underscore and with an `ops_` prefix, because Hive table names cannot start with one.

The jobs keep reading and writing by path (pipeline/bronze/lake.py), so a missing or locked metastore never
stops the pipeline. The catalog is for people: after `make all`, any Spark SQL session started with
pipeline.catalog.session() can run `SELECT ... FROM gold.cases_weekly`.

Derby allows one process at a time, which is why the catalog has its own session and step instead of being
turned on in every job.
"""

from __future__ import annotations

import argparse
import os
import sys

from pipeline.bronze import lake

LAYERS = ("bronze", "silver", "gold")


def catalog_dir():
    return lake.lake_dir() / "_catalog"


def session(app: str = "episcope_catalog"):
    """SparkSession with Hive support and the lake's own metastore (Delta enabled as in every job)."""
    lake.load_env()
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    cdir = catalog_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    builder = (
        SparkSession.builder.master(os.environ.get("EPISCOPE_SPARK_MASTER", "local[2]")).appName(app)
        .config("spark.driver.memory", os.environ.get("EPISCOPE_DRIVER_MEMORY", "2g"))
        .config("spark.hadoop.fs.defaultFS", "file:///")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.warehouse.dir", (cdir / "warehouse").as_uri())
        .config("spark.hadoop.javax.jdo.option.ConnectionURL",
                f"jdbc:derby:;databaseName={cdir / 'metastore'};create=true")
        .config("spark.driver.extraJavaOptions", f"-Dderby.system.home={cdir}")
        .config("spark.sql.session.timeZone", "Asia/Dhaka")
        .enableHiveSupport()
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def table_name(folder: str) -> str:
    return "ops_" + folder.lstrip("_") if folder.startswith("_") else folder


def tables() -> list[tuple[str, str, str]]:
    """[(layer, catalog name, location URI)] for every Delta table in the lake, found on disk."""
    out = []
    for layer in LAYERS:
        root = lake.lake_dir() / layer
        if not root.exists():
            continue
        for d in sorted(p for p in root.iterdir() if (p / "_delta_log").exists()):
            out.append((layer, table_name(d.name), d.as_uri()))
    return out


def register(spark) -> list[str]:
    """Create each layer database and each external table that is not yet registered. Returns new names.
    A table whose registered location differs (for example a moved lake) is re-pointed."""
    new = []
    for layer in LAYERS:
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {layer}")
    for layer, name, uri in tables():
        full = f"{layer}.{name}"
        if spark.catalog.tableExists(full):
            loc = spark.sql(f"DESCRIBE DETAIL {full}").first()["location"]
            if loc.rstrip("/") == uri.rstrip("/") or loc.rstrip("/") == uri.replace("file:///", "file:/").rstrip("/"):
                continue
            spark.sql(f"DROP TABLE {full}")  # external table: dropping it never touches the data
        spark.sql(f"CREATE TABLE {full} USING DELTA LOCATION '{uri}'")
        new.append(full)
    return new


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true", help="print every registered table with its row count")
    a = p.parse_args(argv)
    lake.load_env()
    if not tables():
        sys.exit("the lake is empty: run `make bronze silver gold` first")
    spark = session()
    try:
        new = register(spark)
        print(f"catalog: {len(new)} tables registered, {len(tables())} in total "
              f"(metastore {catalog_dir() / 'metastore'})")
        if a.list:
            for layer in LAYERS:
                for t in spark.catalog.listTables(layer):
                    n = spark.table(f"{layer}.{t.name}").count()
                    print(f"  {layer}.{t.name:<22} {n:>12,} rows")
    finally:
        spark.stop()
    return new


if __name__ == "__main__":
    main()
