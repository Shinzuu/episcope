"""Data-quality rule catalogue for the raw sources S4-S6. Implements DQ-1 (12+ rules) and supports DQ-2.

Each rule is a Spark column expression that is TRUE when a row VIOLATES the rule. Rules carry a stable reason
code, a severity (``quarantine`` drops the row to quarantine, ``warn`` keeps it and counts it) and the
requirement ID it serves.

Raw columns may arrive as strings (bronze reads CSV/JSON as written) or already typed. Spark 4 runs in ANSI
mode, where a plain cast or comparison on a malformed string throws, so every rule reads values through
``try_cast`` and treats an unparseable value as its own defect (the DQ_TYPE_* rules).

Injected defects (ANALYSIS.md section 6, trap 4) map one-to-one:

    duplicated encounter_id      DQ_DUP_ENCOUNTER
    orphan facility_id           DQ_ORPHAN_FACILITY
    invalid age                  DQ_INVALID_AGE
    invalid sex                  DQ_INVALID_SEX
    onset_date > report_date     DQ_ONSET_AFTER_REPORT
    bad ICD-10 code              DQ_BAD_ICD10

Nothing here hard-codes dates, file names or row counts. The valid district pcodes come from the reference
file ``data/reference/districts.csv`` (or are passed in), never from a literal list.
"""

from __future__ import annotations

import csv
import os
import pathlib
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

QUARANTINE = "quarantine"
WARN = "warn"

# Your-call decisions (documented in the report): valid age range and the ICD-10 shape.
AGE_MIN, AGE_MAX = 0, 120
# WHO ICD-10: a letter, two digits, then optionally a dot and 1-4 subcategory characters (A90, A09.0, B54).
ICD10_REGEX = r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$"
SEXES = ("M", "F")
OUTCOMES = ("recovered", "referred", "died", "unknown")
FACILITY_TYPES = ("district_hospital", "upazila_health_complex", "clinic", "medical_college_hospital")
MAX_REPORT_DELAY_DAYS = 21  # generator contract: report = onset + 0-21 days
# Generous bounding box around Bangladesh (about 20.6-26.6 N, 88.0-92.7 E) for a coordinate sanity warning.
BD_LAT, BD_LON = (20.3, 26.9), (87.8, 92.9)
STOCK_COLS = ("iv_saline_units", "ns1_kits", "ors_packets", "antimalarial_courses", "occupied_beds")

# Internal helper columns added by the lookups below. apply_rules drops every column with this prefix.
INTERNAL_PREFIX = "_dq_"
FAC_KNOWN = "_dq_facility_known"
FAC_BEDS = "_dq_facility_beds"


@dataclass(frozen=True)
class Rule:
    code: str
    severity: str
    req: str
    description: str
    expr: Callable[[DataFrame], Column]  # returns a boolean Column: True = row fails the rule
    requires: tuple = field(default_factory=tuple)

    def __post_init__(self):
        if self.severity not in (QUARANTINE, WARN):
            raise ValueError(f"{self.code}: severity must be '{QUARANTINE}' or '{WARN}'")


# --------------------------------------------------------------------------- typed accessors (never throw)

def _str(c: str) -> Column:
    return F.col(c).cast("string")


def _int(c: str) -> Column:
    return F.col(c).try_cast("int")


def _dbl(c: str) -> Column:
    return F.col(c).try_cast("double")


def _date(c: str) -> Column:
    return F.col(c).try_cast("date")


def _blank(c: str) -> Column:
    return F.col(c).isNull() | (F.trim(_str(c)) == "")


def _unparseable(c: str, typed: Column) -> Column:
    """Present (non-blank) but cannot be read as its contract type."""
    return ~_blank(c) & typed.isNull()


def _any(cols: Iterable[Column]) -> Column:
    cols = list(cols)
    out = cols[0]
    for c in cols[1:]:
        out = out | c
    return out


def _duplicate(keys: Sequence[str], latest_first: Callable[[], Sequence[Column]] = list) -> Callable[[DataFrame], Column]:
    """True for every row except one per key. The kept row is the first by ``latest_first`` (for example the
    latest report_date); the full-row JSON breaks ties so the choice is deterministic and idempotent."""

    def build(df: DataFrame) -> Column:
        original = [c for c in df.columns if not c.startswith(INTERNAL_PREFIX)]
        order = list(latest_first()) + [F.to_json(F.struct(*[F.col(c) for c in original])).asc()]
        w = Window.partitionBy(*[F.col(k) for k in keys]).orderBy(*order)
        return F.row_number().over(w) > 1

    return build


def load_district_codes(path: str | os.PathLike | None = None) -> list[str]:
    """Valid ADM2 pcodes from the reference districts file (small reference data, read without Spark)."""
    if path is None:
        root = pathlib.Path(os.environ.get("EPISCOPE_DATA_DIR", "./data"))
        path = root / "reference" / "districts.csv"
    path = pathlib.Path(path)
    if not path.exists():
        raise FileNotFoundError(f"district reference file not found: {path} (build pipeline/reference first)")
    with path.open(newline="", encoding="utf-8") as fh:
        codes = sorted({row["district_code"].strip() for row in csv.DictReader(fh) if row.get("district_code")})
    if not codes:
        raise ValueError(f"no district_code values in {path}")
    return codes


# --------------------------------------------------------------------------- lookups the rules depend on

def with_facility_lookup(df: DataFrame, facilities: DataFrame) -> DataFrame:
    """Left-join the (already quality-checked) facility register so the orphan and bed-capacity rules can see
    it. Adds ``_dq_facility_known`` and ``_dq_facility_beds``; apply_rules drops them again. The register is
    collapsed to one row per facility_id first, so the join can never multiply rows."""
    fac = (
        facilities.select(_str("facility_id").alias("facility_id"), _int("beds").alias("beds"))
        .where(F.col("facility_id").isNotNull())
        .groupBy("facility_id")
        .agg(F.max("beds").alias(FAC_BEDS))
        .withColumn(FAC_KNOWN, F.lit(True))
    )
    joined = df.withColumn("_dq_fid", _str("facility_id")).join(
        F.broadcast(fac.withColumnRenamed("facility_id", "_dq_fid")), on="_dq_fid", how="left"
    )
    return joined.drop("_dq_fid").withColumn(FAC_KNOWN, F.coalesce(F.col(FAC_KNOWN), F.lit(False)))


# --------------------------------------------------------------------------- S4 encounters

ENCOUNTER_REQUIRED = ("encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "icd10_code")


def encounter_rules() -> list[Rule]:
    req = ENCOUNTER_REQUIRED
    return [
        Rule("DQ_NULL_REQUIRED", QUARANTINE, "DQ-1", f"one of {', '.join(req)} is null or blank",
             lambda df: _any(_blank(c) for c in req)),
        Rule("DQ_TYPE_DATE", QUARANTINE, "DQ-1", "onset_date or report_date is not a valid ISO date",
             lambda df: _unparseable("onset_date", _date("onset_date"))
             | _unparseable("report_date", _date("report_date"))),
        Rule("DQ_TYPE_AGE", QUARANTINE, "DQ-1", "age_years is present but not an integer",
             lambda df: _unparseable("age_years", _int("age_years"))),
        Rule("DQ_TYPE_BOOL", QUARANTINE, "DQ-1", "lab_confirmed or admitted is present but not a boolean",
             lambda df: _unparseable("lab_confirmed", F.col("lab_confirmed").try_cast("boolean"))
             | _unparseable("admitted", F.col("admitted").try_cast("boolean"))),
        Rule("DQ_DUP_ENCOUNTER", QUARANTINE, "DQ-1",
             "encounter_id repeated in the load; the latest report_date is kept, the rest quarantined",
             _duplicate(["encounter_id"], lambda: [_date("report_date").desc_nulls_last()]),
             requires=("encounter_id", "report_date")),
        Rule("DQ_ORPHAN_FACILITY", QUARANTINE, "DQ-1", "facility_id not in the facility register (S5)",
             lambda df: ~F.col(FAC_KNOWN), requires=(FAC_KNOWN,)),
        Rule("DQ_INVALID_AGE", QUARANTINE, "DQ-1", f"age_years outside {AGE_MIN}-{AGE_MAX}",
             lambda df: F.coalesce((_int("age_years") < AGE_MIN) | (_int("age_years") > AGE_MAX), F.lit(False))),
        Rule("DQ_MISSING_AGE", WARN, "DQ-1", "age_years is missing (kept, age_band will be null)",
             lambda df: _blank("age_years")),
        Rule("DQ_INVALID_SEX", QUARANTINE, "DQ-1", "sex is not M or F",
             lambda df: F.coalesce(~_str("sex").isin(*SEXES), F.lit(True))),
        Rule("DQ_ONSET_AFTER_REPORT", QUARANTINE, "DQ-1", "onset_date is after report_date",
             lambda df: F.coalesce(_date("onset_date") > _date("report_date"), F.lit(False))),
        Rule("DQ_BAD_ICD10", QUARANTINE, "DQ-1", "icd10_code is not a well-formed ICD-10 code",
             lambda df: ~_blank("icd10_code") & ~F.coalesce(_str("icd10_code").rlike(ICD10_REGEX), F.lit(False))),
        Rule("DQ_LONG_DELAY", WARN, "DQ-1", f"report_date more than {MAX_REPORT_DELAY_DAYS} days after onset",
             lambda df: F.coalesce(F.datediff(_date("report_date"), _date("onset_date")) > MAX_REPORT_DELAY_DAYS,
                                   F.lit(False))),
        Rule("DQ_FUTURE_DATE", WARN, "DQ-1", "report_date is later than today",
             lambda df: F.coalesce(_date("report_date") > F.current_date(), F.lit(False))),
        Rule("DQ_OUTCOME_DOMAIN", WARN, "DQ-1", f"outcome not one of {'|'.join(OUTCOMES)}",
             lambda df: F.coalesce(~_str("outcome").isin(*OUTCOMES), F.lit(True))),
    ]


# --------------------------------------------------------------------------- S5 facilities

def facility_rules(district_codes: Sequence[str] | None = None) -> list[Rule]:
    codes = list(district_codes) if district_codes is not None else load_district_codes()
    nums = (("latitude", _dbl), ("longitude", _dbl), ("beds", _int), ("icu_beds", _int))
    return [
        Rule("DQ_NULL_REQUIRED", QUARANTINE, "DQ-1", "facility_id or district_code is null or blank",
             lambda df: _blank("facility_id") | _blank("district_code")),
        Rule("DQ_TYPE_NUMERIC", QUARANTINE, "DQ-1", "latitude, longitude, beds or icu_beds is not numeric",
             lambda df: _any(_unparseable(c, f(c)) for c, f in nums)),
        Rule("DQ_DUP_FACILITY", QUARANTINE, "DQ-1", "facility_id repeated; one row is kept",
             _duplicate(["facility_id"]), requires=("facility_id",)),
        Rule("DQ_GEO_DISTRICT", QUARANTINE, "DQ-1", "district_code is not one of the reference ADM2 pcodes",
             lambda df: ~_blank("district_code") & ~_str("district_code").isin(*codes)),
        Rule("DQ_GEO_COORDS", WARN, "DQ-1", "latitude/longitude missing or outside Bangladesh",
             lambda df: F.coalesce(~_dbl("latitude").between(*BD_LAT) | ~_dbl("longitude").between(*BD_LON),
                                   F.lit(True))),
        Rule("DQ_NEG_CAPACITY", QUARANTINE, "DQ-1", "beds or icu_beds negative, or icu_beds > beds",
             lambda df: F.coalesce((_int("beds") < 0) | (_int("icu_beds") < 0)
                                   | (_int("icu_beds") > _int("beds")), F.lit(False))),
        Rule("DQ_FACILITY_TYPE", WARN, "DQ-1", "facility_type outside the contract's four values",
             lambda df: F.coalesce(~_str("facility_type").isin(*FACILITY_TYPES), F.lit(True))),
    ]


# --------------------------------------------------------------------------- S6 stock

def stock_rules() -> list[Rule]:
    return [
        Rule("DQ_NULL_REQUIRED", QUARANTINE, "DQ-1", "facility_id or snapshot_date is null or blank",
             lambda df: _blank("facility_id") | _blank("snapshot_date")),
        Rule("DQ_TYPE_DATE", QUARANTINE, "DQ-1", "snapshot_date is not a valid ISO date",
             lambda df: _unparseable("snapshot_date", _date("snapshot_date"))),
        Rule("DQ_TYPE_NUMERIC", QUARANTINE, "DQ-1", "a stock or occupied_beds value is not an integer",
             lambda df: _any(_unparseable(c, _int(c)) for c in STOCK_COLS)),
        Rule("DQ_DUP_STOCK", QUARANTINE, "DQ-1", "facility_id + snapshot_date repeated; one row is kept",
             _duplicate(["facility_id", "snapshot_date"]), requires=("facility_id", "snapshot_date")),
        Rule("DQ_ORPHAN_FACILITY", QUARANTINE, "DQ-1", "facility_id not in the facility register (S5)",
             lambda df: ~F.col(FAC_KNOWN), requires=(FAC_KNOWN,)),
        Rule("DQ_NEG_STOCK", QUARANTINE, "DQ-1", "a stock or occupied_beds value is negative",
             lambda df: F.coalesce(_any(_int(c) < 0 for c in STOCK_COLS), F.lit(False))),
        # Corridor patients are real in a dengue surge, so over-capacity is a warning, not a rejection.
        Rule("DQ_BEDS_OVER_CAPACITY", WARN, "DQ-1", "occupied_beds exceeds the facility's beds",
             lambda df: F.coalesce(_int("occupied_beds") > F.col(FAC_BEDS), F.lit(False)),
             requires=(FAC_BEDS,)),
    ]


def rules_for(source: str, district_codes: Sequence[str] | None = None) -> list[Rule]:
    """Rule set for a source name: encounters (S4), facilities (S5) or stock (S6)."""
    if source == "encounters":
        return encounter_rules()
    if source == "facilities":
        return facility_rules(district_codes)
    if source == "stock":
        return stock_rules()
    raise ValueError(f"unknown source {source!r}; expected encounters, facilities or stock")
