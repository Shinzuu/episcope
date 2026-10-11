"""k-anonymity measurement for de-identified tables. Implements PRV-4 helpers.

A release is k-anonymous if every record shares its quasi-identifier values with at least k-1 others
(Sweeney 2002). Quasi-identifiers here are typically age_band, sex, district_code, onset week, ICD-10 group
and facility. k-anonymity does not stop homogeneity attacks (a class where everyone has the same diagnosis);
l-diversity is the remedy, discussed in the report.
"""

from __future__ import annotations

from typing import Sequence

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


def _classes(df: DataFrame, quasi_identifiers: Sequence[str]) -> DataFrame:
    qis = list(quasi_identifiers)
    if not qis:
        raise ValueError("quasi_identifiers must name at least one column")
    return df.groupBy(*qis).agg(F.count(F.lit(1)).alias("_k"))


def k_anonymity(df: DataFrame, quasi_identifiers: Sequence[str]) -> int | None:
    """Minimum equivalence-class size over the quasi-identifiers (None for an empty table)."""
    row = _classes(df, quasi_identifiers).agg(F.min("_k").alias("k")).first()
    return None if row is None or row["k"] is None else int(row["k"])


def k_anonymity_report(df: DataFrame, quasi_identifiers: Sequence[str], k: int = 5) -> dict:
    """min_k, number of classes, and how many records sit in classes smaller than k (what generalising or
    suppressing would have to remove)."""
    r = _classes(df, quasi_identifiers).agg(
        F.min("_k").alias("min_k"),
        F.count(F.lit(1)).alias("classes"),
        F.sum("_k").alias("records"),
        F.sum(F.when(F.col("_k") < k, F.col("_k")).otherwise(0)).alias("below"),
    ).first()
    records = int(r["records"] or 0)
    below = int(r["below"] or 0)
    return {
        "k_target": k,
        "min_k": None if r["min_k"] is None else int(r["min_k"]),
        "classes": int(r["classes"] or 0),
        "records": records,
        "records_below_k": below,
        "share_below_k": (below / records) if records else 0.0,
    }
