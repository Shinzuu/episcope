"""`python -m analytics.detection.run`: gold.cases_weekly -> gold.alerts (Delta, partitioned by epi_year).

Implements AN-1 (EARS C2 + CUSUM at scale in Spark) and AN-3 (completeness-aware onset-week detection).
Recomputes every alert from the full weekly table on each run, so a backfill that changes past weeks is picked
up and a re-run is idempotent. Paths come from EPISCOPE_LAKE_DIR or the command line; nothing is hard-coded
about dates or sizes.
"""

import argparse
import os
import sys

from analytics.detection import config as C
from analytics.detection.methods import detect, to_alerts


def read_cases(spark, path):
    path = str(path)
    if os.path.exists(path):
        return spark.read.format("delta").load(path)
    return spark.table("gold.cases_weekly")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cases", default=str(C.CASES_WEEKLY_PATH), help="gold.cases_weekly Delta path")
    p.add_argument("--out", default=str(C.ALERTS_PATH), help="gold.alerts Delta path")
    p.add_argument("--ears-threshold", type=float, default=C.EARS_THRESHOLD)
    p.add_argument("--cusum-k", type=float, default=C.CUSUM_K)
    p.add_argument("--cusum-h", type=float, default=C.CUSUM_H)
    a = p.parse_args(argv)

    from pipeline.spark import get_spark

    # applyInPandas workers must use this interpreter (the venv), not the system python3.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

    spark = get_spark("episcope-detection")
    try:
        weekly = read_cases(spark, a.cases)
        alerts = to_alerts(detect(weekly, a.ears_threshold, a.cusum_k, a.cusum_h))
        (alerts.repartition("epi_year").write.format("delta").mode("overwrite")
         .option("overwriteSchema", "true").partitionBy("epi_year").save(a.out))
        written = spark.read.format("delta").load(a.out)
        summary = written.groupBy("method").agg({"alert": "count"}).collect()
        n_alerts = written.where("alert").groupBy("method").count().collect()
        print(f"gold.alerts -> {a.out}")
        for r in summary:
            fired = next((x["count"] for x in n_alerts if x["method"] == r["method"]), 0)
            print(f"  {r['method']}: {r['count(alert)']} rows, {fired} alerts")
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
