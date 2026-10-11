"""Patient de-duplication across facilities. Implements DQ-4 (should-have).

A patient seen at two facilities for the same illness is one case, not two. An encounter is a duplicate when an
EARLIER encounter (by onset_date, then encounter_id) exists with
    - the same patient_key (the PRV-1 keyed hash, so matching never needs the raw ID),
    - the same disease bucket,
    - compatible attributes: the same sex, and the same age_band (a missing band is compatible with any),
    - a DIFFERENT facility_id (same-facility revisits are follow-ups, kept as they are),
    - an onset no more than WINDOW_DAYS before it.
The duplicate keeps its row in silver; gold counts skip it. Because only an earlier encounter can make a row a
duplicate, a newly arrived record can change the status of rows at most WINDOW_DAYS later: gold therefore
rebuilds the week after every changed week too.

`python -m pipeline.silver.dedup --measure` scores the rule against the generator's injected repeat patients.
The generator reuses a patient_id only inside one district and one onset month (generator/encounters.py,
REPEAT_PATIENT_RATE). Independent random IDs collide too (10-digit space), and those collisions are different
people. So a flagged pair is a true duplicate when both rows share district and onset month, and a false one
otherwise. Precision = true flagged pairs / flagged pairs; recall = flagged pairs / injected pairs that fall
inside the rule's window and facility condition. Output: docs/experiments/dq4_patient_dedup.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

WINDOW_DAYS = 7
KEY_COLS = ("encounter_id", "patient_key", "facility_id", "onset_date", "disease", "sex", "age_band")


def duplicate_pairs(df: DataFrame, window_days: int = WINDOW_DAYS, compatible: bool = True) -> DataFrame:
    """(encounter_id, dup_of) for every (later, earlier) pair that meets the rule."""
    a = df.select(*[F.col(c).alias(f"a_{c}") for c in KEY_COLS])
    b = df.select(*[F.col(c).alias(f"b_{c}") for c in KEY_COLS])
    cond = [
        F.col("a_patient_key") == F.col("b_patient_key"),
        F.col("a_disease") == F.col("b_disease"),
        F.col("a_facility_id") != F.col("b_facility_id"),
        F.col("b_onset_date") >= F.date_sub(F.col("a_onset_date"), window_days),
        (F.col("b_onset_date") < F.col("a_onset_date"))
        | ((F.col("b_onset_date") == F.col("a_onset_date")) & (F.col("b_encounter_id") < F.col("a_encounter_id"))),
    ]
    if compatible:
        cond += [
            F.col("a_sex") == F.col("b_sex"),
            F.col("a_age_band").isNull() | F.col("b_age_band").isNull() | (F.col("a_age_band") == F.col("b_age_band")),
        ]
    on = cond[0]
    for c in cond[1:]:
        on = on & c
    return a.join(b, on, "inner").select(F.col("a_encounter_id").alias("encounter_id"),
                                         F.col("b_encounter_id").alias("dup_of"))


def flag_duplicates(df: DataFrame, window_days: int = WINDOW_DAYS) -> DataFrame:
    """df plus `dup_of` (the earliest matching encounter_id, null when the row is not a duplicate)."""
    pairs = duplicate_pairs(df, window_days).groupBy("encounter_id").agg(F.min("dup_of").alias("dup_of"))
    return df.join(pairs, "encounter_id", "left")


def measure(spark, out_path=None) -> dict:
    """Precision/recall of the rule on silver.encounters against the injected repeat-patient structure."""
    from pipeline.bronze import lake

    enc = lake.read(spark, "silver", "encounters").where(F.col("disease") != "other")
    enc = enc.select(*KEY_COLS, "district_code", F.date_trunc("month", "onset_date").alias("onset_month")).cache()
    attrs = enc.select("encounter_id", "district_code", "onset_month")
    pairs = duplicate_pairs(enc)

    def label(p):
        return (p.join(attrs.toDF("encounter_id", "a_d", "a_m"), "encounter_id")
                 .join(attrs.toDF("dup_of", "b_d", "b_m"), "dup_of")
                 .withColumn("true", (F.col("a_d") == F.col("b_d")) & (F.col("a_m") == F.col("b_m"))))

    flagged = label(pairs).agg(F.count(F.lit(1)).alias("n"), F.sum(F.col("true").cast("int")).alias("tp")).first()
    # injected pairs the rule could see: same key, district, month and disease, other facility, within the window
    cand = label(duplicate_pairs(enc, compatible=False)).where("true")
    reachable = cand.count()
    n, tp = int(flagged["n"] or 0), int(flagged["tp"] or 0)
    res = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rule": f"same patient_key, disease, sex and compatible age_band; other facility; onset within {WINDOW_DAYS} days",
        "flagged_pairs": n,
        "true_pairs": tp,
        "precision": (tp / n) if n else None,
        "injected_pairs_in_window": reachable,
        "recall_in_window": (tp / reachable) if reachable else None,
        "duplicate_encounters": pairs.select("encounter_id").distinct().count(),
        "encounters_checked": enc.count(),
        "note": ("Truth: the generator reuses a patient_id only within one district and onset month, so a flagged "
                 "pair across districts or months is an ID collision between different people. Recall is limited "
                 "by the attribute check: the generator draws age and sex afresh for a repeat visit."),
    }
    enc.unpersist()
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(res, indent=2) + "\n")
    return res


def main(argv=None):
    from pipeline.bronze import lake

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--measure", action="store_true", help="score DQ-4 against the injected repeat patients")
    p.add_argument("--out", default=str(lake.ROOT / "docs" / "experiments" / "dq4_patient_dedup.json"))
    a = p.parse_args(argv)
    if not a.measure:
        p.error("nothing to do (use --measure)")
    import pathlib

    spark = lake.spark_session("dq4_measure")
    try:
        print(json.dumps(measure(spark, pathlib.Path(a.out)), indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
