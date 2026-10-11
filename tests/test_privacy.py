"""Tests for pipeline/privacy (PRV-1, PRV-2, PRV-4; ENG-4).

The HMAC key is generated inside the test process and set with monkeypatch; it is never written to disk.
"""

import hashlib
import hmac
import os
import random
import secrets
from collections import defaultdict

import pytest

os.environ.setdefault("EPISCOPE_SPARK_MASTER", "local[2]")


from pipeline.privacy import (  # noqa: E402
    SALT_ENV,
    age_band,
    audit_suppression,
    deidentify,
    hmac_sha256,
    k_anonymity,
    k_anonymity_report,
    suppress,
    suppress_derived,
)
from pipeline.quality import apply_rules, encounter_rules, with_facility_lookup  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    from pipeline.spark import get_spark

    s = get_spark("test_privacy", memory=os.environ.get("EPISCOPE_DRIVER_MEMORY", "1g"))
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


@pytest.fixture
def salt(monkeypatch):
    value = secrets.token_hex(32)
    monkeypatch.setenv(SALT_ENV, value)
    return value


IDS = ["1990123456789", "P-000001", "", "ঢাকা-৪২", "x" * 200]


def hashes(spark, ids):
    df = spark.createDataFrame([(i,) for i in ids], "patient_id string")
    return [r[0] for r in df.select(hmac_sha256("patient_id")).collect()]


# --------------------------------------------------------------------------- PRV-1 hashing

def test_hmac_matches_python_reference(spark, salt):
    expected = [hmac.new(salt.encode(), i.encode(), hashlib.sha256).hexdigest() for i in IDS]
    assert hashes(spark, IDS) == expected


def test_hmac_long_key_is_hashed_first(spark, monkeypatch):
    key = secrets.token_hex(100)  # 200 bytes > SHA-256 block
    monkeypatch.setenv(SALT_ENV, key)
    assert hashes(spark, ["P1"]) == [hmac.new(key.encode(), b"P1", hashlib.sha256).hexdigest()]


def test_hash_is_stable_and_keyed(spark, monkeypatch):
    monkeypatch.setenv(SALT_ENV, "first-" + secrets.token_hex(16))
    a1, a2 = hashes(spark, ["P1"]), hashes(spark, ["P1"])
    monkeypatch.setenv(SALT_ENV, "second-" + secrets.token_hex(16))
    b = hashes(spark, ["P1"])
    assert a1 == a2 and a1 != b
    assert len(a1[0]) == 64


def test_null_id_hashes_to_null(spark, salt):
    df = spark.createDataFrame([(None,)], "patient_id string")
    assert df.select(hmac_sha256("patient_id")).first()[0] is None


@pytest.mark.parametrize("value", [None, "", "   ", "change-me-to-a-long-random-string"])
def test_missing_or_placeholder_salt_fails_loudly(spark, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(SALT_ENV, raising=False)
    else:
        monkeypatch.setenv(SALT_ENV, value)
    with pytest.raises(RuntimeError, match=SALT_ENV):
        hmac_sha256("patient_id")


def test_age_band_edges(spark):
    ages = ["0", "4", "5", "14", "15", "24", "25", "44", "45", "64", "65", "120", "121", "-1", None, "abc"]
    want = ["0-4", "0-4", "5-14", "5-14", "15-24", "15-24", "25-44", "25-44", "45-64", "45-64", "65+", "65+",
            None, None, None, None]
    df = spark.createDataFrame([(a,) for a in ages], "age_years string")
    assert [r[0] for r in df.select(age_band("age_years")).collect()] == want


ENC_COLS = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
            "icd10_code", "lab_confirmed", "admitted", "outcome"]


def test_no_patient_id_survives(spark, salt):
    rows = [("E1", "1990000000001", "F1", "2024-07-01", "2024-07-02", "30", "M", "A90", "true", "false", "recovered"),
            ("E2", "1990000000002", "F1", "2024-07-01", "2024-07-02", "212", "F", "A90", "true", "false", "died"),
            ("E3", "1990000000003", "F9", "2024-07-01", "2024-07-02", "7", "F", "B54", "false", "true", "unknown")]
    raw = spark.createDataFrame(rows, ", ".join(f"{c} string" for c in ENC_COLS))
    facilities = spark.createDataFrame([("F1", "10")], "facility_id string, beds string")
    clean, q = apply_rules(with_facility_lookup(raw, facilities), encounter_rules(), "b", "encounters")
    # The silver job pseudonymises both outputs; quarantine keeps the exact age as evidence.
    outputs = [deidentify(clean), deidentify(q, keep_age=True)]
    raw_ids = {r[1] for r in rows}
    for out in outputs:
        assert "patient_id" not in out.columns and "patient_key" in out.columns
        values = {str(v) for row in out.collect() for v in row.asDict().values()}
        assert not raw_ids & values
        assert salt not in values
    assert "age_years" not in outputs[0].columns and "age_band" in outputs[0].columns
    assert outputs[0].count() == 1 and outputs[1].count() == 2


# --------------------------------------------------------------------------- PRV-2 suppression

def recover_by_subtraction(original, published, dims, table_dims=()):
    """Attacker's view: published cells plus exact line totals from the true table. Returns every hidden
    cell that some line pins down exactly (a line with one hidden cell)."""
    recovered = {}
    for d_i in range(len(dims)):
        lines = defaultdict(list)
        for key in original:
            line = tuple(v for j, v in enumerate(key) if j != d_i)
            lines[line].append(key)
        for keys in lines.values():
            total = sum(original[k] for k in keys)
            hidden = [k for k in keys if published[k] is None]
            if len(hidden) == 1:
                recovered[hidden[0]] = total - sum(published[k] for k in keys if published[k] is not None)
    return recovered


def run(spark, cells, dims=("district", "week"), table_cols=()):
    cols = list(table_cols) + list(dims)
    df = spark.createDataFrame([k + (v,) for k, v in cells.items()],
                               ", ".join(f"{c} string" for c in cols) + ", cases long")
    out = suppress(df, "cases", list(dims), threshold=5, table_cols=list(table_cols))
    pub = {tuple(r[c] for c in cols): r["cases"] for r in out.collect()}
    flags = {tuple(r[c] for c in cols): r["suppressed"] for r in out.collect()}
    return out, pub, flags


def test_primary_and_complementary_suppression(spark):
    # 3 districts x 3 weeks; one small cell in row D1 and in column W1.
    cells = {("D1", "W1"): 3, ("D1", "W2"): 10, ("D1", "W3"): 12,
             ("D2", "W1"): 7, ("D2", "W2"): 0, ("D2", "W3"): 20,
             ("D3", "W1"): 9, ("D3", "W2"): 11, ("D3", "W3"): 30}
    out, pub, flags = run(spark, cells)
    assert pub[("D1", "W1")] is None and flags[("D1", "W1")]
    assert sum(flags.values()) >= 4  # a lone primary needs a rectangle of four
    assert pub[("D2", "W2")] == 0 or flags[("D2", "W2")]  # zeros publish unless needed as a complement
    for k, v in cells.items():
        if not flags[k]:
            assert pub[k] == v and not (1 <= v <= 4)
    assert recover_by_subtraction(cells, pub, ("district", "week")) == {}
    assert audit_suppression(out, "cases", ["district", "week"]) == {"small_visible": 0, "single_suppression_lines": 0}


def test_naive_suppression_would_leak(spark):
    """Control: without the complementary step, subtraction recovers the hidden cell."""
    cells = {("D1", "W1"): 2, ("D1", "W2"): 10, ("D2", "W1"): 8, ("D2", "W2"): 9}
    naive = {k: (None if 1 <= v <= 4 else v) for k, v in cells.items()}
    assert recover_by_subtraction(cells, naive, ("district", "week")) == {("D1", "W1"): 2}
    _, pub, _ = run(spark, cells)
    assert recover_by_subtraction(cells, pub, ("district", "week")) == {}


def test_random_tables_resist_subtraction(spark):
    rng = random.Random(7)
    for trial in range(3):
        cells = {}
        for dz in ("dengue", "malaria"):
            for d in range(6):
                for w in range(8):
                    cells[(dz, f"D{d}", f"W{w}")] = rng.choice([0, 0, 1, 2, 3, 4, 5, 6, 8, 15, 40])
        out, pub, flags = run(spark, cells, table_cols=("disease",))
        for dz in ("dengue", "malaria"):
            orig = {k[1:]: v for k, v in cells.items() if k[0] == dz}
            pubd = {k[1:]: v for k, v in pub.items() if k[0] == dz}
            assert recover_by_subtraction(orig, pubd, ("district", "week")) == {}, (trial, dz)
        assert not [v for v in pub.values() if v is not None and 1 <= v <= 4]
        assert audit_suppression(out, "cases", ["district", "week"], table_cols=["disease"])["single_suppression_lines"] == 0


def test_suppress_derived_nulls_rates(spark):
    df = spark.createDataFrame([("D1", "W1", 3, 1.5), ("D1", "W2", 50, 25.0), ("D2", "W1", 40, 20.0),
                                ("D2", "W2", 60, 30.0)],
                               "district string, week string, cases long, inc double")
    out = suppress_derived(suppress(df, "cases", ["district", "week"]), ["inc"])
    rows = {(r.district, r.week): r for r in out.collect()}
    assert all((r.inc is None) == r.suppressed for r in rows.values())
    assert rows[("D1", "W1")].inc is None


# --------------------------------------------------------------------------- PRV-4 k-anonymity

def test_k_anonymity(spark):
    rows = [("25-44", "M", "BD1004")] * 6 + [("25-44", "F", "BD1004")] * 3 + [("65+", "F", "BD3026")] * 5
    df = spark.createDataFrame(rows, "age_band string, sex string, district_code string")
    assert k_anonymity(df, ["age_band", "sex", "district_code"]) == 3
    assert k_anonymity(df, ["age_band"]) == 5
    rep = k_anonymity_report(df, ["age_band", "sex", "district_code"], k=5)
    assert rep["min_k"] == 3 and rep["classes"] == 3 and rep["records_below_k"] == 3
    assert rep["share_below_k"] == pytest.approx(3 / 14)
    assert k_anonymity(df.limit(0), ["sex"]) is None
