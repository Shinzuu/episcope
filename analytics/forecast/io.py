"""Lake paths and Delta read/write helpers for the analytics outputs. Serves AN-4 … AN-9.

Gold tables live at <EPISCOPE_LAKE_DIR>/gold/<name> (Delta). Reference files at <EPISCOPE_DATA_DIR>/reference.
Result files for the report (CSV/JSON) go to <EPISCOPE_RESULTS_DIR>, default docs/experiments.
Nothing here hard-codes a date, file name of a drop, or row count.
"""

import os
import pathlib
import sys


def lake_dir():
    return pathlib.Path(os.environ.get("EPISCOPE_LAKE_DIR", "./lake")).resolve()


def data_dir():
    return pathlib.Path(os.environ.get("EPISCOPE_DATA_DIR", "./data")).resolve()


def results_dir():
    p = pathlib.Path(os.environ.get("EPISCOPE_RESULTS_DIR", "./docs/experiments")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def gold_path(table):
    """'gold.forecasts' or 'forecasts' -> file:///…/lake/gold/forecasts"""
    name = table.split(".", 1)[-1]
    return (lake_dir() / "gold" / name).as_uri()


def gold_exists(table):
    name = table.split(".", 1)[-1]
    return (lake_dir() / "gold" / name / "_delta_log").exists()


def read_gold(spark, table):
    return spark.read.format("delta").load(gold_path(table))


def write_gold(df, table, partition_by=None):
    w = df.write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    if partition_by:
        w = w.partitionBy(*partition_by)
    w.save(gold_path(table))


def districts_csv():
    return data_dir() / "reference" / "districts.csv"


def limit_threads():
    """Prepare the environment for Python UDF workers. Must run before the SparkSession starts.

    - Stop statsmodels/BLAS oversubscribing the cores Spark already uses (research.md §4.5).
    - Make the workers use this interpreter (the venv); otherwise Spark starts `python3` from PATH, which
      lacks pandas/numpy, and every applyInPandas task fails.
    """
    for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(k, "1")
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
