"""Small-cell suppression with complementary suppression for every export. Implements PRV-2.

Algorithm (research.md section 9.2, after Hundepool et al., Handbook on Statistical Disclosure Control):

1. Primary: every cell with 1 <= count < threshold is suppressed. Zeros stay published.
2. Secondary: treat each column in ``group_cols`` as a dimension of the table. A *line* is the set of cells
   that share every other dimension (and the ``table_cols``), i.e. the cells summed into one row or column
   total. While any line of two or more cells holds exactly one suppressed cell, also suppress the smallest
   unsuppressed cell of that line (non-zero first, ties broken by the dimension values). Repeat until no line
   has exactly one suppression.

After step 2, every published line total minus its visible cells leaves at least two hidden cells, so no
single hidden cell can be recovered by subtraction. This heuristic does not guarantee a protection interval
against a linear-programming attack across many lines (tau-ARGUS-style optimal suppression does); the report
says so. Hierarchies (district -> division) are separate tables: suppress each one it is published.
A line of one cell is not protected by this step: its total *is* the cell, so do not publish such totals.

Derived values (rates, percentages) must be nulled wherever their count is: ``suppress_derived``.
"""

from __future__ import annotations

from typing import Sequence

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

SUPPRESSED = "suppressed"
_MAX_ROUNDS = 1000


def _primary(count_col: str, threshold: int):
    c = F.col(count_col)
    return F.coalesce((c >= 1) & (c < threshold), F.lit(False))


def suppress(df: DataFrame, count_col: str, group_cols: Sequence[str], threshold: int = 5,
             table_cols: Sequence[str] = ()) -> DataFrame:
    """Return ``df`` with ``count_col`` set to null where suppressed and a boolean ``suppressed`` column.

    group_cols: the table's dimensions (for example ["district_code", "epi_week"]); each one defines a set of
    additive lines. table_cols: columns that split the input into independent tables (for example disease),
    each protected on its own. One row per cell is expected.
    """
    group_cols, table_cols = list(group_cols), list(table_cols)
    if not group_cols:
        raise ValueError("group_cols must name at least one dimension")
    dims = group_cols
    work = df.withColumn("_sup", _primary(count_col, threshold))
    cnt = F.col(count_col)
    work = work.localCheckpoint()  # keep the plan flat across rounds
    before = work.where("_sup").count()

    for _ in range(_MAX_ROUNDS):
        new_flags = []
        for d in dims:
            line = table_cols + [x for x in dims if x != d]
            w = Window.partitionBy(*line) if line else Window.partitionBy(F.lit(1))
            ranked = w.orderBy(
                F.col("_sup").asc(),                                   # unsuppressed cells first
                F.coalesce(cnt == 0, F.lit(True)).asc(),               # non-zero before zero/null
                F.coalesce(cnt, F.lit(0)).asc(),                       # smallest count
                *[F.col(x).asc_nulls_last() for x in dims],            # deterministic tie-break
            )
            flag = (
                (F.sum(F.col("_sup").cast("int")).over(w) == 1)
                & (F.count(F.lit(1)).over(w) > 1)
                & (F.row_number().over(ranked) == 1)
                & ~F.col("_sup")
            )
            name = f"_new_{len(new_flags)}"
            work = work.withColumn(name, flag)
            new_flags.append(name)
        any_new = F.lit(False)
        for n in new_flags:
            any_new = any_new | F.col(n)
        work = work.withColumn("_sup", F.col("_sup") | any_new).drop(*new_flags)
        work = work.localCheckpoint()
        after = work.where("_sup").count()
        if after == before:
            break
        before = after
    else:  # pragma: no cover - each round adds a cell, so this needs more rounds than cells
        raise RuntimeError("complementary suppression did not converge")

    return work.withColumn(count_col, F.when(F.col("_sup"), F.lit(None)).otherwise(cnt)) \
               .withColumn(SUPPRESSED, F.col("_sup")).drop("_sup")


def suppress_derived(df: DataFrame, cols: Sequence[str], flag_col: str = SUPPRESSED) -> DataFrame:
    """Null every derived value (rate, percentage, cumulative) on suppressed rows: rate x population would
    otherwise give the count back."""
    for c in cols:
        df = df.withColumn(c, F.when(F.col(flag_col), F.lit(None)).otherwise(F.col(c)))
    return df


def audit_suppression(df: DataFrame, count_col: str, group_cols: Sequence[str], threshold: int = 5,
                      table_cols: Sequence[str] = ()) -> dict:
    """Check a suppressed table: visible counts in [1, threshold) and lines with exactly one hidden cell.
    Both must be 0. Usable by serving and by the verify gate."""
    dims, table_cols = list(group_cols), list(table_cols)
    small = df.where(F.col(count_col).isNotNull() & (F.col(count_col) >= 1) & (F.col(count_col) < threshold)).count()
    single = 0
    for d in dims:
        line = table_cols + [x for x in dims if x != d]
        g = df.groupBy(*line) if line else df.groupBy()
        single += g.agg(F.sum(F.col(SUPPRESSED).cast("int")).alias("h"), F.count(F.lit(1)).alias("n")) \
                   .where("h = 1 AND n > 1").count()
    return {"small_visible": small, "single_suppression_lines": single}
