"""What did we know on date t? Implements ING-7 (Delta time travel for honest backtests).

`python -m pipeline.gold.asof 2024-03-15`

Every gold build records, in gold._snapshots, the Delta version of gold.cases_weekly it produced and the latest
report_date that version includes (pipeline/gold/build.py, record_snapshot). For a date t this module picks the
newest version whose data ends on or before t and reads gold.cases_weekly at that version with Delta's
versionAsOf. Records that were reported after t, including late records that later moved into old weeks, are
therefore not in the answer, which is what a backtest at origin t must see.

Delta keeps old versions until VACUUM removes their files (7 days by default, counted from when a version was
superseded); the pipeline never runs VACUUM.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from pyspark.sql import functions as F

from pipeline.bronze import lake
from pipeline.gold.build import SNAPSHOTS


def version_as_of(spark, t: dt.date):
    """(version, data_through) of the newest cases_weekly snapshot with data_through <= t, or None."""
    if not lake.exists("gold", SNAPSHOTS):
        return None
    r = (lake.read(spark, "gold", SNAPSHOTS).where(F.col("data_through") <= F.lit(t))
         .orderBy(F.col("data_through").desc(), F.col("version").desc()).first())
    return (r["version"], r["data_through"]) if r else None


def cases_as_of(spark, t: dt.date):
    """gold.cases_weekly as it stood with the data known on date t (None if nothing was known yet)."""
    v = version_as_of(spark, t)
    if v is None:
        return None
    return spark.read.format("delta").option("versionAsOf", v[0]).load(lake.table_uri("gold", "cases_weekly"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("date", type=dt.date.fromisoformat, help="YYYY-MM-DD")
    a = p.parse_args(argv)
    lake.load_env()
    spark = lake.spark_session("gold_asof")
    try:
        v = version_as_of(spark, a.date)
        if v is None:
            sys.exit(f"nothing was known on {a.date}: no snapshot ends on or before it")
        df = cases_as_of(spark, a.date)
        last = df.agg(F.max("week_start")).first()[0]
        print(f"as of {a.date}: gold.cases_weekly version {v[0]} (data through {v[1]}), "
              f"{df.count()} rows, latest week {last}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
