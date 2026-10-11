"""Apply quality rules, split clean from quarantine, and build the per-load report. Implements DQ-2 and DQ-3.

``apply_rules`` evaluates every rule once per row and writes each failing row to quarantine exactly once,
with the full list of reason codes. Warn-only rows stay in the clean output; ``dq_report`` counts them.

Both functions are deterministic for a given input (duplicates are resolved by an explicit ordering), so
re-running a load gives the same split, and running the clean output through again quarantines nothing.
For large loads the caller should ``persist()`` the input, because the clean and quarantine outputs are two
filters over the same flagged plan.
"""

from __future__ import annotations

from typing import Sequence

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from .rules import INTERNAL_PREFIX, QUARANTINE, Rule

REASON_CODES = "reason_codes"
_FLAG = "_dq_flag_"


def _check(df: DataFrame, rules: Sequence[Rule]) -> None:
    codes = [r.code for r in rules]
    dup = sorted({c for c in codes if codes.count(c) > 1})
    if dup:
        raise ValueError(f"rule codes must be unique within a rule set: {dup}")
    for r in rules:
        missing = [c for c in r.requires if c not in df.columns]
        if missing:
            hint = " (call with_facility_lookup first)" if any(m.startswith(INTERNAL_PREFIX) for m in missing) else ""
            raise ValueError(f"rule {r.code} needs columns {missing}{hint}")


def _flagged(df: DataFrame, rules: Sequence[Rule]) -> DataFrame:
    """Input plus one boolean flag column per rule (True = fails). Null results count as pass."""
    _check(df, rules)
    flags = [F.coalesce(r.expr(df), F.lit(False)).alias(f"{_FLAG}{i}") for i, r in enumerate(rules)]
    return df.select("*", *flags)


def _original_columns(df: DataFrame) -> list[str]:
    return [c for c in df.columns if not c.startswith(INTERNAL_PREFIX)]


def apply_rules(df: DataFrame, rules: Sequence[Rule], batch_id: str, source: str) -> tuple[DataFrame, DataFrame]:
    """Split ``df`` into (clean_df, quarantine_df).

    clean_df keeps the original columns of every row that fails no quarantine rule (warn failures included).
    quarantine_df keeps the original columns plus reason_codes (array of every failing code, warn codes too),
    batch_id, source and quarantined_at. Internal ``_dq_*`` helper columns are dropped from both.
    """
    original = _original_columns(df)
    flagged = _flagged(df, rules)
    q_flags = [F.col(f"{_FLAG}{i}") for i, r in enumerate(rules) if r.severity == QUARANTINE]
    is_quarantined = F.lit(False)
    for f in q_flags:
        is_quarantined = is_quarantined | f
    reasons = F.array_compact(F.array(*[F.when(F.col(f"{_FLAG}{i}"), F.lit(r.code)) for i, r in enumerate(rules)]))
    marked = flagged.withColumn("_dq_quarantined", is_quarantined)

    clean = marked.where(~F.col("_dq_quarantined")).select(*original)
    quarantine = marked.where(F.col("_dq_quarantined")).select(
        *original,
        reasons.alias(REASON_CODES),
        F.lit(batch_id).cast("string").alias("batch_id"),
        F.lit(source).cast("string").alias("source"),
        F.current_timestamp().alias("quarantined_at"),
    )
    return clean, quarantine


def dq_report(df: DataFrame, rules: Sequence[Rule], batch_id: str, source: str) -> DataFrame:
    """One-row DataFrame matching gold.dq_report (docs/contracts.md):
    batch_id, source, rows_in, rows_clean, rows_quarantined, rule_pass_rates (map code -> share passing),
    loaded_at. Computed from the same flags as apply_rules, so the counts always agree with the split.
    ``df`` is the input that was passed to apply_rules (with any lookups applied)."""
    flagged = _flagged(df, rules)
    q_idx = [i for i, r in enumerate(rules) if r.severity == QUARANTINE]
    any_q = F.lit(False)
    for i in q_idx:
        any_q = any_q | F.col(f"{_FLAG}{i}")
    aggs = [F.count(F.lit(1)).alias("rows_in"), F.sum(F.when(any_q, 1).otherwise(0)).alias("rows_quarantined")]
    aggs += [F.sum(F.col(f"{_FLAG}{i}").cast("long")).alias(f"_fail_{i}") for i in range(len(rules))]
    totals = flagged.agg(*aggs)

    rows_in = F.col("rows_in")
    rate = lambda i: F.when(rows_in == 0, F.lit(1.0)).otherwise(  # noqa: E731
        1.0 - F.coalesce(F.col(f"_fail_{i}"), F.lit(0)).cast("double") / rows_in)
    pass_rates = F.create_map(*[x for i, r in enumerate(rules) for x in (F.lit(r.code), rate(i))])
    return totals.select(
        F.lit(batch_id).cast("string").alias("batch_id"),
        F.lit(source).cast("string").alias("source"),
        rows_in.cast("long").alias("rows_in"),
        (rows_in - F.coalesce(F.col("rows_quarantined"), F.lit(0))).cast("long").alias("rows_clean"),
        F.coalesce(F.col("rows_quarantined"), F.lit(0)).cast("long").alias("rows_quarantined"),
        pass_rates.alias("rule_pass_rates"),
        F.current_timestamp().alias("loaded_at"),
    )
