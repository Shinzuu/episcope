"""Shared SparkSession for every EpiScope job.

Forces the local filesystem: a machine with a Hadoop install (HADOOP_CONF_DIR set) would otherwise send
file paths to hdfs://localhost:9000, which also matters for the instructor's hidden test drop.
"""

import os
import sys

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession


def get_spark(app="episcope", memory=None):
    memory = memory or os.environ.get("EPISCOPE_DRIVER_MEMORY", "6g")
    # Python workers must use this interpreter (the venv), not the system python3 without pandas.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    builder = (
        SparkSession.builder.master(os.environ.get("EPISCOPE_SPARK_MASTER", "local[*]"))
        .appName(app)
        .config("spark.driver.memory", memory)
        .config("spark.hadoop.fs.defaultFS", "file:///")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.session.timeZone", "Asia/Dhaka")
        .config("spark.sql.adaptive.enabled", "true")
    )
    return configure_spark_with_delta_pip(builder).getOrCreate()
