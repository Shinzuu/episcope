"""Data-quality library used by the bronze -> silver job. Implements DQ-1, DQ-2, DQ-3.

Typical use for one load of encounters:

    from pipeline.quality import rules_for, with_facility_lookup, apply_rules, dq_report
    enc = with_facility_lookup(raw_encounters, clean_facilities)
    rules = rules_for("encounters")
    clean, quarantine = apply_rules(enc, rules, batch_id, "encounters")
    report = dq_report(enc, rules, batch_id, "encounters")

Facilities go first (``rules_for("facilities", district_codes)``), because the orphan and bed-capacity rules
on encounters and stock join against the clean facility register.
"""

from .engine import REASON_CODES, apply_rules, dq_report, flag_rows
from .rules import (
    AGE_MAX,
    AGE_MIN,
    ICD10_REGEX,
    QUARANTINE,
    WARN,
    Rule,
    encounter_rules,
    facility_rules,
    load_district_codes,
    rules_for,
    stock_rules,
    with_facility_lookup,
)

__all__ = [
    "AGE_MAX", "AGE_MIN", "ICD10_REGEX", "QUARANTINE", "REASON_CODES", "WARN", "Rule", "apply_rules",
    "dq_report", "encounter_rules", "facility_rules", "flag_rows", "load_district_codes", "rules_for", "stock_rules",
    "with_facility_lookup",
]
