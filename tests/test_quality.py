"""Tests for pipeline/quality (DQ-1, DQ-2, DQ-3; ENG-4).

Every rule gets at least one failing row and one passing edge-case row, the split is checked to be
idempotent, and the report is checked against the split and the gold.dq_report contract.
"""

import datetime as dt
import os

import pytest

os.environ.setdefault("EPISCOPE_SPARK_MASTER", "local[2]")

from pyspark.sql import functions as F  # noqa: E402

from pipeline.quality import (  # noqa: E402
    QUARANTINE,
    WARN,
    apply_rules,
    dq_report,
    encounter_rules,
    facility_rules,
    load_district_codes,
    rules_for,
    stock_rules,
    with_facility_lookup,
)
from pipeline.quality.engine import _flagged  # noqa: E402

DISTRICTS = ["BD1004", "BD3026", "BD2003"]  # test pcodes only; production reads data/reference/districts.csv

ENC_COLS = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
            "icd10_code", "lab_confirmed", "admitted", "outcome"]
FAC_COLS = ["facility_id", "facility_name", "facility_type", "district_code", "upazila_code", "latitude",
            "longitude", "beds", "icu_beds"]
STOCK_COLS = ["facility_id", "snapshot_date", "iv_saline_units", "ns1_kits", "ors_packets",
              "antimalarial_courses", "occupied_beds"]


@pytest.fixture(scope="module")
def spark():
    from pipeline.spark import get_spark

    s = get_spark("test_quality", memory=os.environ.get("EPISCOPE_DRIVER_MEMORY", "1g"))
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


def enc(**kw):
    row = {"encounter_id": "E1", "patient_id": "P1", "facility_id": "F1", "onset_date": "2024-07-01",
           "report_date": "2024-07-03", "age_years": "30", "sex": "M", "icd10_code": "A90",
           "lab_confirmed": "true", "admitted": "false", "outcome": "recovered"}
    row.update(kw)
    return row


def fac(**kw):
    row = {"facility_id": "F1", "facility_name": "Test DH", "facility_type": "district_hospital",
           "district_code": "BD1004", "upazila_code": None, "latitude": "22.1", "longitude": "90.1",
           "beds": "100", "icu_beds": "10"}
    row.update(kw)
    return row


def stk(**kw):
    row = {"facility_id": "F1", "snapshot_date": "2024-07-01", "iv_saline_units": "50", "ns1_kits": "20",
           "ors_packets": "300", "antimalarial_courses": "0", "occupied_beds": "80"}
    row.update(kw)
    return row


def frame(spark, rows, cols):
    """Raw-looking frame: every column a string, as bronze reads CSV."""
    return spark.createDataFrame([tuple(r[c] for c in cols) for r in rows], ", ".join(f"{c} string" for c in cols))


@pytest.fixture(scope="module")
def facilities(spark):
    return frame(spark, [fac(), fac(facility_id="F2", beds="5", icu_beds="0")], FAC_COLS)


def fails(df, rule):
    """Per-row result of one rule, in input order (True = row fails)."""
    out = _flagged(df.withColumn("_dq_order", F.monotonically_increasing_id()), [rule])
    return [r[0] for r in out.orderBy("_dq_order").select("_dq_flag_0").collect()]


def by_code(rules):
    return {r.code: r for r in rules}


# --------------------------------------------------------------------------- the catalogue itself

def test_catalogue_size_and_metadata():
    rules = encounter_rules() + facility_rules(DISTRICTS) + stock_rules()
    assert len({(r.code) for r in rules}) >= 12
    for r in rules:
        assert r.severity in (QUARANTINE, WARN)
        assert r.req.startswith("DQ-")
        assert r.code.startswith("DQ_") and r.code.upper() == r.code
    injected = {"DQ_DUP_ENCOUNTER", "DQ_ORPHAN_FACILITY", "DQ_INVALID_AGE", "DQ_INVALID_SEX",
                "DQ_ONSET_AFTER_REPORT", "DQ_BAD_ICD10"}
    assert injected <= {r.code for r in encounter_rules() if r.severity == QUARANTINE}


# --------------------------------------------------------------------------- encounter rules, one by one

ENC_CASES = {
    # code: (failing rows, passing edge-case rows)
    "DQ_NULL_REQUIRED": ([enc(patient_id=None), enc(icd10_code="  "), enc(report_date="")],
                         [enc(age_years=None)]),
    "DQ_TYPE_DATE": ([enc(onset_date="2023-02-29"), enc(report_date="not-a-date")],
                     [enc(onset_date="2024-02-29", report_date="2024-02-29")]),
    "DQ_TYPE_AGE": ([enc(age_years="abc"), enc(age_years="12.5")], [enc(age_years="0"), enc(age_years=None)]),
    "DQ_TYPE_BOOL": ([enc(lab_confirmed="maybe"), enc(admitted="2")], [enc(lab_confirmed="false", admitted="true")]),
    "DQ_INVALID_AGE": ([enc(age_years="212"), enc(age_years="-3"), enc(age_years="121")],
                       [enc(age_years="0"), enc(age_years="120")]),
    "DQ_MISSING_AGE": ([enc(age_years=None), enc(age_years="")], [enc(age_years="0")]),
    "DQ_INVALID_SEX": ([enc(sex="X"), enc(sex=None), enc(sex="m")], [enc(sex="F"), enc(sex="M")]),
    "DQ_ONSET_AFTER_REPORT": ([enc(onset_date="2024-07-04", report_date="2024-07-03")],
                              [enc(onset_date="2024-07-03", report_date="2024-07-03")]),
    "DQ_BAD_ICD10": ([enc(icd10_code="A9"), enc(icd10_code="XYZ"), enc(icd10_code="a90"), enc(icd10_code="A90.12345")],
                     [enc(icd10_code="A09.0"), enc(icd10_code="B54"), enc(icd10_code="J11.1")]),
    "DQ_LONG_DELAY": ([enc(onset_date="2024-07-01", report_date="2024-07-23")],
                      [enc(onset_date="2024-07-01", report_date="2024-07-22")]),
    "DQ_FUTURE_DATE": ([enc(report_date=(dt.date.today() + dt.timedelta(days=400)).isoformat())],
                       [enc(report_date=(dt.date.today() - dt.timedelta(days=1)).isoformat(), onset_date="2000-01-01")]),
    "DQ_OUTCOME_DOMAIN": ([enc(outcome="dead"), enc(outcome=None)], [enc(outcome="unknown"), enc(outcome="died")]),
}


@pytest.mark.parametrize("code", sorted(ENC_CASES))
def test_encounter_rule(spark, facilities, code):
    bad, good = ENC_CASES[code]
    df = with_facility_lookup(frame(spark, bad + good, ENC_COLS), facilities)
    assert fails(df, by_code(encounter_rules())[code]) == [True] * len(bad) + [False] * len(good)


def test_orphan_facility(spark, facilities):
    df = with_facility_lookup(frame(spark, [enc(facility_id="F999"), enc(facility_id="F2")], ENC_COLS), facilities)
    assert fails(df, by_code(encounter_rules())["DQ_ORPHAN_FACILITY"]) == [True, False]


def test_orphan_rule_requires_lookup(spark):
    with pytest.raises(ValueError, match="with_facility_lookup"):
        apply_rules(frame(spark, [enc()], ENC_COLS), encounter_rules(), "b", "encounters")


def test_duplicate_keeps_latest_report(spark, facilities):
    rows = [enc(encounter_id="E1", report_date="2024-07-03"), enc(encounter_id="E1", report_date="2024-07-09"),
            enc(encounter_id="E1", report_date="2024-07-05"), enc(encounter_id="E2")]
    df = with_facility_lookup(frame(spark, rows, ENC_COLS), facilities)
    assert fails(df, by_code(encounter_rules())["DQ_DUP_ENCOUNTER"]) == [True, False, True, False]


def test_exact_duplicate_rows_keep_one(spark, facilities):
    df = with_facility_lookup(frame(spark, [enc(), enc(), enc()], ENC_COLS), facilities)
    clean, q = apply_rules(df, encounter_rules(), "b1", "encounters")
    assert clean.count() == 1 and q.count() == 2


# --------------------------------------------------------------------------- facility and stock rules

FAC_CASES = {
    "DQ_NULL_REQUIRED": ([fac(facility_id=None), fac(district_code="")], [fac(upazila_code=None)]),
    "DQ_TYPE_NUMERIC": ([fac(beds="many"), fac(latitude="north")], [fac(beds="0", icu_beds="0")]),
    "DQ_GEO_DISTRICT": ([fac(district_code="BD9999"), fac(district_code="Dhaka")],
                        [fac(district_code="BD3026"), fac(district_code="BD2003")]),
    "DQ_GEO_COORDS": ([fac(latitude="40.0"), fac(longitude=None)], [fac(latitude="20.6", longitude="92.6")]),
    "DQ_NEG_CAPACITY": ([fac(beds="-1"), fac(beds="10", icu_beds="11")], [fac(beds="10", icu_beds="10")]),
    "DQ_FACILITY_TYPE": ([fac(facility_type="pharmacy")], [fac(facility_type="medical_college_hospital")]),
}


@pytest.mark.parametrize("code", sorted(FAC_CASES))
def test_facility_rule(spark, code):
    bad, good = FAC_CASES[code]
    df = frame(spark, bad + good, FAC_COLS)
    assert fails(df, by_code(facility_rules(DISTRICTS))[code]) == [True] * len(bad) + [False] * len(good)


def test_duplicate_facility(spark):
    df = frame(spark, [fac(), fac(beds="200"), fac(facility_id="F2")], FAC_COLS)
    assert sum(fails(df, by_code(facility_rules(DISTRICTS))["DQ_DUP_FACILITY"])) == 1


STOCK_CASES = {
    "DQ_NULL_REQUIRED": ([stk(snapshot_date=None)], [stk(antimalarial_courses=None)]),
    "DQ_TYPE_DATE": ([stk(snapshot_date="2024-13-01")], [stk(snapshot_date="2024-12-31")]),
    "DQ_TYPE_NUMERIC": ([stk(ns1_kits="lots")], [stk(ns1_kits="0")]),
    "DQ_ORPHAN_FACILITY": ([stk(facility_id="F404")], [stk(facility_id="F2", occupied_beds="5")]),
    "DQ_NEG_STOCK": ([stk(iv_saline_units="-1"), stk(occupied_beds="-2")], [stk(iv_saline_units="0", occupied_beds="0")]),
    "DQ_BEDS_OVER_CAPACITY": ([stk(occupied_beds="101"), stk(facility_id="F2", occupied_beds="6")],
                              [stk(occupied_beds="100"), stk(facility_id="F2", occupied_beds="5")]),
}


@pytest.mark.parametrize("code", sorted(STOCK_CASES))
def test_stock_rule(spark, facilities, code):
    bad, good = STOCK_CASES[code]
    df = with_facility_lookup(frame(spark, bad + good, STOCK_COLS), facilities)
    assert fails(df, by_code(stock_rules())[code]) == [True] * len(bad) + [False] * len(good)


def test_duplicate_stock(spark, facilities):
    rows = [stk(), stk(ors_packets="1"), stk(snapshot_date="2024-07-02")]
    df = with_facility_lookup(frame(spark, rows, STOCK_COLS), facilities)
    assert sum(fails(df, by_code(stock_rules())["DQ_DUP_STOCK"])) == 1


def test_every_rule_has_a_case():
    covered = set(ENC_CASES) | {"DQ_ORPHAN_FACILITY", "DQ_DUP_ENCOUNTER"}
    assert {r.code for r in encounter_rules()} <= covered
    assert {r.code for r in facility_rules(DISTRICTS)} <= set(FAC_CASES) | {"DQ_DUP_FACILITY"}
    assert {r.code for r in stock_rules()} <= set(STOCK_CASES) | {"DQ_DUP_STOCK"}


# --------------------------------------------------------------------------- splitting and report

MIXED = [
    enc(encounter_id="E1"),                                             # clean
    enc(encounter_id="E2", age_years="212", sex="X", icd10_code="ZZ"),  # three quarantine codes
    enc(encounter_id="E3", outcome="dead"),                             # warn only -> stays clean
    enc(encounter_id="E4", facility_id="F999"),                         # orphan
    enc(encounter_id="E5", report_date="2024-07-03"),                   # older duplicate
    enc(encounter_id="E5", report_date="2024-07-06"),                   # latest duplicate, kept
    enc(encounter_id="E6", onset_date="2024-07-05", report_date="2024-07-03"),
    enc(encounter_id="E7", age_years="0"),
    enc(encounter_id="E8", age_years="120"),
]


def split(spark, facilities, rows=MIXED, batch="2024-07-06"):
    df = with_facility_lookup(frame(spark, rows, ENC_COLS), facilities)
    rules = encounter_rules()
    clean, q = apply_rules(df, rules, batch, "encounters")
    return df, rules, clean, q


def test_apply_rules_split(spark, facilities):
    _, _, clean, q = split(spark, facilities)
    assert clean.columns == ENC_COLS
    assert q.columns == ENC_COLS + ["reason_codes", "batch_id", "source", "quarantined_at"]
    assert sorted(r.encounter_id for r in clean.collect()) == ["E1", "E3", "E5", "E7", "E8"]
    assert [r.report_date for r in clean.where("encounter_id = 'E5'").collect()] == ["2024-07-06"]
    qrows = {(r.encounter_id, r.report_date): r for r in q.collect()}
    assert len(qrows) == q.count() == 4  # each row written once
    e2 = qrows[("E2", "2024-07-03")]
    assert {"DQ_INVALID_AGE", "DQ_INVALID_SEX", "DQ_BAD_ICD10"} <= set(e2.reason_codes)
    assert e2.batch_id == "2024-07-06" and e2.source == "encounters" and e2.quarantined_at is not None
    assert qrows[("E4", "2024-07-03")].reason_codes == ["DQ_ORPHAN_FACILITY"]
    assert qrows[("E5", "2024-07-03")].reason_codes == ["DQ_DUP_ENCOUNTER"]
    assert qrows[("E6", "2024-07-03")].reason_codes == ["DQ_ONSET_AFTER_REPORT"]
    assert not [c for c in clean.columns + q.columns if c.startswith("_dq_")]


def test_apply_rules_is_idempotent(spark, facilities):
    _, rules, clean, q = split(spark, facilities)
    key = lambda d: sorted(tuple(r) for r in d.select(*ENC_COLS).collect())  # noqa: E731
    # Same input twice -> same split.
    _, _, clean_b, q_b = split(spark, facilities)
    assert key(clean) == key(clean_b) and key(q) == key(q_b)
    # The clean output run through again -> unchanged, nothing quarantined.
    clean2, q2 = apply_rules(with_facility_lookup(clean, facilities), rules, "again", "encounters")
    assert key(clean2) == key(clean) and q2.count() == 0


def test_dq_report_matches_contract_and_split(spark, facilities):
    df, rules, clean, q = split(spark, facilities)
    rep = dq_report(df, rules, "2024-07-06", "encounters")
    assert rep.columns == ["batch_id", "source", "rows_in", "rows_clean", "rows_quarantined", "rule_pass_rates",
                           "loaded_at"]
    assert dict((f.name, f.dataType.simpleString()) for f in rep.schema)["rule_pass_rates"] == "map<string,double>"
    (r,) = rep.collect()
    assert (r.rows_in, r.rows_clean, r.rows_quarantined) == (len(MIXED), clean.count(), q.count())
    rates = r.rule_pass_rates
    assert set(rates) == {x.code for x in rules}
    n = len(MIXED)
    assert rates["DQ_OUTCOME_DOMAIN"] == pytest.approx(1 - 1 / n)  # warn row counted
    assert rates["DQ_DUP_ENCOUNTER"] == pytest.approx(1 - 1 / n)
    assert rates["DQ_NULL_REQUIRED"] == 1.0


def test_dq_report_empty_load(spark, facilities):
    df = with_facility_lookup(frame(spark, [enc()], ENC_COLS).limit(0), facilities)
    (r,) = dq_report(df, encounter_rules(), "b0", "encounters").collect()
    assert (r.rows_in, r.rows_clean, r.rows_quarantined) == (0, 0, 0)
    assert all(v == 1.0 for v in r.rule_pass_rates.values())


def test_typed_input_works(spark, facilities):
    """Bronze may infer types; rules must not depend on everything being strings."""
    df = spark.createDataFrame(
        [("E1", "P1", "F1", dt.date(2024, 7, 1), dt.date(2024, 7, 2), 212, "M", "A90", True, False, "recovered")],
        "encounter_id string, patient_id string, facility_id string, onset_date date, report_date date, "
        "age_years int, sex string, icd10_code string, lab_confirmed boolean, admitted boolean, outcome string")
    clean, q = apply_rules(with_facility_lookup(df, facilities), encounter_rules(), "b", "encounters")
    assert clean.count() == 0 and q.first().reason_codes == ["DQ_INVALID_AGE"]


def test_rules_for_and_district_loader(tmp_path):
    p = tmp_path / "districts.csv"
    p.write_text("district_code,district_name\nBD1004,Barguna\nBD3026,Dhaka\n")
    assert load_district_codes(p) == ["BD1004", "BD3026"]
    with pytest.raises(FileNotFoundError):
        load_district_codes(tmp_path / "missing.csv")
    assert {r.code for r in rules_for("facilities", ["BD1004"])} == {r.code for r in facility_rules(["BD1004"])}
    with pytest.raises(ValueError):
        rules_for("weather")
