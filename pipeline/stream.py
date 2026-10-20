"""Near-real-time path: Spark Structured Streaming over the encounter file source refreshes the alerts.
Implements ING-6.

`python -m pipeline.stream [--interval 60] [--once] [--no-export]`

A file-source stream watches <EPISCOPE_DATA_DIR>/raw/encounters/report_date=*/ for new CSV files (the same
contract schema as bronze, every field a string). The stream itself only decides *when* to run: each
micro-batch that contains new files triggers the same incremental steps as the daily batch, in this session:

    bronze ingest (new checksums only) -> silver MERGE -> gold rebuild of the changed weeks
    -> detection over gold.cases_weekly -> gold.alerts -> dashboard export (unless --no-export)

Reusing the batch code keeps one set of rules, one quarantine and one lineage, so a record that arrives through
the stream is treated exactly like one from `make bronze silver gold`. The bronze checksum log makes the two
paths safe to mix: whichever sees a file first loads it, and the other skips it.

Latency (file written -> alerts written) is measured for every micro-batch from the newest file's modification
time and appended to docs/experiments/stream_latency.json; the target is under 5 minutes.

Why a trigger interval and not continuous processing: alerts are computed per epi-week, so a minute of delay
costs nothing, and between micro-batches the stream holds almost no memory on the 16 GB laptop (conflict C4).
The checkpoint lives in <lake>/_stream/checkpoint, so a restarted stream does not replay files it already saw.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time

from pipeline.bronze import lake
from pipeline.bronze.ingest import contract_fields

LATENCY_LOG = lake.ROOT / "docs" / "experiments" / "stream_latency.json"


def _log(entry: dict) -> None:
    LATENCY_LOG.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(LATENCY_LOG.read_text()) if LATENCY_LOG.exists() else []
    LATENCY_LOG.write_text(json.dumps(runs + [entry], indent=2) + "\n")


def refresh(spark, export: bool = True) -> dict:
    """The incremental chain behind one micro-batch. Returns step timings in seconds."""
    from analytics.detection import config as DC
    from analytics.detection.methods import detect, to_alerts
    from pipeline.bronze.ingest import ingest
    from pipeline.gold.build import build as gold
    from pipeline.silver.build import build as silver

    t, steps = time.time(), {}

    def mark(name):
        nonlocal t
        now = time.time()
        steps[name] = round(now - t, 1)
        t = now

    res = ingest(spark, lake.data_dir())
    mark("bronze")
    if res["batch_id"] is None:  # the batch path already loaded these files
        return {"batch_id": None, "steps": steps}
    silver(spark)
    mark("silver")
    gold(spark)
    mark("gold")
    alerts = to_alerts(detect(lake.read(spark, "gold", "cases_weekly"), DC.EARS_THRESHOLD, DC.CUSUM_K, DC.CUSUM_H))
    (alerts.repartition("epi_year").write.format("delta").mode("overwrite").option("overwriteSchema", "true")
     .partitionBy("epi_year").save(str(DC.ALERTS_PATH)))
    mark("alerts")
    if export:
        from serving.export import export as export_json

        export_json(spark, lake.ROOT / "dashboard" / "data", 10 * 1024 * 1024)
        mark("export")
    return {"batch_id": res["batch_id"], "rows": res["rows"], "steps": steps}


def on_batch(spark, export: bool):
    def handle(batch_df, epoch_id):
        files = [r[0] for r in batch_df.select("_file").distinct().collect()]
        if not files:
            return
        paths = [pathlib.Path(f.replace("file://", "").replace("file:", "")) for f in files]
        newest = max(p.stat().st_mtime for p in paths if p.exists())
        out = refresh(spark, export)
        if out["batch_id"] is None:  # files already loaded by the batch path (e.g. the first epoch after start)
            print(f"stream epoch {epoch_id}: {len(files)} files, nothing new for bronze", flush=True)
            return
        done = time.time()
        entry = {"epoch": epoch_id, "files": len(files), "batch_id": out["batch_id"], "steps": out["steps"],
                 "latency_seconds": round(done - newest, 1),
                 "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        _log(entry)
        print(f"stream epoch {epoch_id}: {len(files)} files, alerts refreshed {entry['latency_seconds']} s "
              f"after the newest file arrived; steps {out['steps']}", flush=True)
    return handle


def start(spark, interval: int = 60, once: bool = False, export: bool = True):
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    schema = StructType([StructField(c, StringType()) for c in contract_fields("S4_encounters")])
    # recursive lookup, not a report_date=* glob: no partition column may clash with the report_date field
    src = str(lake.data_dir() / "raw" / "encounters")
    stream = (spark.readStream.schema(schema).option("header", "true").option("recursiveFileLookup", "true")
              .csv(src).select(F.col("_metadata.file_path").alias("_file")))
    w = (stream.writeStream.foreachBatch(on_batch(spark, export))
         .option("checkpointLocation", str(lake.lake_dir() / "_stream" / "checkpoint")))
    w = w.trigger(availableNow=True) if once else w.trigger(processingTime=f"{interval} seconds")
    return w.start()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=int, default=60, help="seconds between checks for new files")
    p.add_argument("--once", action="store_true", help="process what is there now, then stop")
    p.add_argument("--no-export", action="store_true", help="refresh gold.alerts only, not dashboard/data")
    a = p.parse_args(argv)
    lake.load_env()
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    spark = lake.spark_session("stream_alerts")
    try:
        q = start(spark, a.interval, a.once, not a.no_export)
        print(f"stream: watching {lake.data_dir() / 'raw' / 'encounters'} every {a.interval} s (Ctrl-C stops)")
        q.awaitTermination()
    except KeyboardInterrupt:
        pass
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
