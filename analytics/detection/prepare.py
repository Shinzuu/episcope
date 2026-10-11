"""Turn gold.cases_weekly into a dense, completeness-adjusted, seasonally adjusted weekly series.

Implements AN-3 (artefact handling) and the seasonal adjustment that AN-1's EARS C2 needs.

Steps, all in Spark (window functions and aggregates, no Python UDFs):
1. Dense calendar. Every (district, disease) gets every week between the first and last week_start in the
   data, so `rowsBetween` counts weeks, not rows.
2. Completeness (AN-3). completeness = reporting_facilities / expected_facilities, capped at 1.
   - A district-week with no row for any disease means nothing was reported: missing (null), never zero.
   - A missing row when the district did report other diseases that week is a true zero.
   - completeness < COMPLETENESS_MIN: missing. Otherwise the count is scaled by 1 / completeness.
   gold counts by onset week, so a facility's backfilled reports land on their onset weeks, not as a spike
   in the week they arrive.
3. Seasonal index. Multiplicative, per (division, disease, week-of-year): the median over the other years
   of the pooled ratio of adjusted counts to their centred 53-week level. Leave-one-year-out keeps an
   outbreak out of its own expected value. With a single year of data the index is 1 (no adjustment).

Output columns: district_code, disease, pool, week_start, epi_year, epi_week, cases, completeness, adj_cases,
season_index, value (= adj_cases / season_index, the series the detectors run on).
"""

from pyspark.sql import Window
from pyspark.sql import functions as F

from analytics.detection import config as C

KEYS = C.GROUP_KEYS


def _week_window():
    return Window.partitionBy(*KEYS).orderBy("week_start")


def densify(weekly):
    """Dense (district, disease, week) grid with cases and completeness. Missing reporting stays null."""
    w = weekly.select(
        *KEYS, "week_start",
        F.col("cases").cast("double").alias("cases_obs"),
        F.col("reporting_facilities").cast("double").alias("rep"),
        F.col("expected_facilities").cast("double").alias("exp"),
    )
    bounds = w.agg(F.min("week_start").alias("lo"), F.max("week_start").alias("hi"))
    weeks = bounds.select(F.explode(F.sequence("lo", "hi", F.expr("INTERVAL 7 DAYS"))).alias("week_start"))
    groups = w.select(*KEYS).distinct()
    # Pooling unit for the seasonal index; falls back to the district if division_code is absent or null.
    div_src = weekly.select("district_code", F.col("division_code").cast("string")) \
        if "division_code" in weekly.columns else weekly.select("district_code", F.lit(None).cast("string").alias("division_code"))
    divs = div_src.groupBy("district_code").agg(F.max("division_code").alias("division_code"))
    groups = groups.join(divs, "district_code", "left") \
        .withColumn("pool", F.coalesce("division_code", "district_code")).drop("division_code")
    # What the district reported that week across all diseases: decides "true zero" versus "missing".
    dw = w.groupBy("district_code", "week_start").agg(
        F.max("rep").alias("dw_rep"), F.max("exp").alias("dw_exp"), F.lit(True).alias("dw_seen")
    )
    d = (
        groups.crossJoin(weeks)
        .join(w, KEYS + ["week_start"], "left")
        .join(dw, ["district_code", "week_start"], "left")
    )
    rep = F.coalesce("rep", "dw_rep", F.lit(0.0))
    exp = F.coalesce("exp", "dw_exp")
    completeness = F.when(exp.isNull() | (exp <= 0), F.lit(1.0)).otherwise(F.least(rep / exp, F.lit(1.0)))
    cases = (
        F.when(F.col("cases_obs").isNotNull(), F.col("cases_obs"))
        .when(F.col("dw_seen"), F.lit(0.0))
        .otherwise(F.lit(None).cast("double"))
    )
    return d.select(
        *KEYS, "pool", "week_start",
        F.weekofyear("week_start").alias("epi_week"),
        F.expr("extract(YEAROFWEEK FROM week_start)").cast("int").alias("epi_year"),
        cases.alias("cases"),
        F.when(F.col("dw_seen"), completeness).otherwise(F.lit(0.0)).alias("completeness"),
    )


def adjust_completeness(dense, completeness_min=C.COMPLETENESS_MIN, use_completeness=True):
    """adj_cases: null when the week is missing, cases / completeness otherwise (AN-3)."""
    if not use_completeness:
        return dense.withColumn("adj_cases", F.coalesce("cases", F.lit(0.0)))
    c = F.col("completeness")
    adj = F.when(F.col("cases").isNull() | (c < completeness_min) | (c <= 0), F.lit(None).cast("double")) \
        .otherwise(F.col("cases") / c)
    return dense.withColumn("adj_cases", adj)


def seasonal_index(df, half_width=C.SEASON_LEVEL_HALF_WIDTH, smooth=C.SEASON_SMOOTH_HALF_WIDTH,
                   clamp=C.SEASON_INDEX_CLAMP, pooled=C.SEASON_POOLED):
    """Add season_index and value = adj_cases / season_index.

    ratio  = sum(adj_cases) / sum(centred 53-week level) over the districts of a pool (division) and disease,
             counting only non-missing district-weeks, then a centred moving average over +-smooth weeks;
    index  = median of that ratio at the same week-of-year (53 folded into 52) over the OTHER years, clamped.
    Pooling over a division's districts cuts the noise of small district series; the district's own level
    is still tracked by the detectors' moving baselines. With pooled=False the pool is the district alone.
    """
    w = _week_window()
    pool = "pool" if pooled else "district_code"
    level = F.avg("adj_cases").over(w.rowsBetween(-half_width, half_width))  # avg ignores nulls
    df = df.withColumn("_level", level).withColumn("_woy", F.least(F.col("epi_week"), F.lit(52)))
    ok = F.col("adj_cases").isNotNull() & F.col("_level").isNotNull()
    pr = (
        df.groupBy(pool, "disease", "week_start", "epi_year", "_woy")
        .agg(F.sum(F.when(ok, F.col("adj_cases"))).alias("_num"), F.sum(F.when(ok, F.col("_level"))).alias("_den"))
        .withColumn("_ratio", F.when(F.col("_den") > 0, F.col("_num") / F.col("_den")))
    )
    pw = Window.partitionBy(pool, "disease").orderBy("week_start").rowsBetween(-smooth, smooth)
    pr = pr.withColumn("_ratio_s", F.avg("_ratio").over(pw))
    profile = (
        pr.where(F.col("_ratio_s").isNotNull())
        .groupBy(pool, "disease", "_woy")
        .agg(F.collect_list(F.struct(F.col("epi_year").alias("y"), F.col("_ratio_s").alias("r"))).alias("_yr"))
    )
    df = df.join(profile, [pool, "disease", "_woy"], "left")
    others = "array_sort(transform(filter(coalesce(_yr, array()), x -> x.y != epi_year), x -> x.r))"
    df = df.withColumn("_arr", F.expr(others)).withColumn("_n", F.size("_arr"))
    med = F.expr(
        "CASE WHEN _n = 0 THEN NULL "
        "WHEN _n % 2 = 1 THEN element_at(_arr, cast((_n + 1) / 2 AS INT)) "
        "ELSE (element_at(_arr, cast(_n div 2 AS INT)) + element_at(_arr, cast(_n div 2 AS INT) + 1)) / 2 END"
    )
    lo, hi = clamp
    idx = F.least(F.greatest(F.coalesce(med, F.lit(1.0)), F.lit(lo)), F.lit(hi))
    df = df.withColumn("season_index", idx)
    df = df.withColumn("value", F.col("adj_cases") / F.col("season_index"))
    return df.drop("_level", "_woy", "_yr", "_arr", "_n")


def prepare(weekly, use_completeness=True, use_seasonal=True, completeness_min=C.COMPLETENESS_MIN):
    """Full preparation. The naive baseline is prepare(..., use_completeness=False, use_seasonal=False)."""
    df = adjust_completeness(densify(weekly), completeness_min, use_completeness)
    if use_seasonal:
        return seasonal_index(df)
    return df.withColumn("season_index", F.lit(1.0)).withColumn("value", F.col("adj_cases"))
