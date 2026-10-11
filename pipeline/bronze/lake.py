"""Lake paths, environment and Delta helpers shared by the bronze, silver and gold jobs and by serving.
Serves ING-1, ING-2, ING-3, ING-4.

Every location is derived from two environment variables (read from `.env` when present):
    EPISCOPE_DATA_DIR  raw and reference inputs (default ./data)
    EPISCOPE_LAKE_DIR  Delta tables, laid out as <lake>/<layer>/<table> (default ./lake)
Relative values resolve against the repository root, so the jobs behave the same from any working directory.
Nothing here names a date, a drop file or a row count.
"""

from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load <repo>/.env into os.environ without overriding variables that are already set."""
    env = ROOT / ".env"
    if not env.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is pinned in requirements.txt; tiny fallback parser just in case
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return
    load_dotenv(env, override=False)


def _dir(var: str, default: str) -> pathlib.Path:
    p = pathlib.Path(os.environ.get(var, default))
    return p if p.is_absolute() else (ROOT / p).resolve()


def data_dir() -> pathlib.Path:
    return _dir("EPISCOPE_DATA_DIR", "./data")


def lake_dir() -> pathlib.Path:
    return _dir("EPISCOPE_LAKE_DIR", "./lake")


def table_dir(layer: str, name: str) -> pathlib.Path:
    return lake_dir() / layer / name


def table_uri(layer: str, name: str) -> str:
    return table_dir(layer, name).as_uri()


def exists(layer: str, name: str) -> bool:
    return (table_dir(layer, name) / "_delta_log").exists()


def read(spark, layer: str, name: str):
    return spark.read.format("delta").load(table_uri(layer, name))


def write(df, layer: str, name: str, mode: str = "overwrite", partition_by=None, replace_where: str | None = None):
    w = df.write.format("delta").mode(mode)
    if mode == "overwrite" and replace_where is None:
        w = w.option("overwriteSchema", "true")
    if replace_where is not None:
        w = w.option("replaceWhere", replace_where)
    if partition_by:
        w = w.partitionBy(*partition_by)
    w.save(table_uri(layer, name))


def delta_table(spark, layer: str, name: str):
    from delta.tables import DeltaTable

    return DeltaTable.forPath(spark, table_uri(layer, name))


def spark_session(app: str):
    """Load .env, then the shared SparkSession (pipeline.spark.get_spark: local filesystem, Delta, AQE)."""
    load_env()
    from pipeline.spark import get_spark

    spark = get_spark(app)
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def sql_str(value) -> str:
    """Quote a value as a Spark SQL string literal (for replaceWhere predicates built from data)."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
