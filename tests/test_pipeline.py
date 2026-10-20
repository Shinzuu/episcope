"""End-to-end tests of the lakehouse jobs on tiny hand-written data (ING-1 to ING-4, DQ-2 to DQ-4, PRV-1,
PRV-2, VIS-4; ENG-4).

The raw files follow generator/schema.json. The HMAC key is generated in the test process and never written.
Scenario:
    batch 1  facilities F1, F2 (Dhaka) and F3 (Chattogram); encounters in ISO weeks 9 and 11 of 2024, with a
             within-load duplicate, an orphan facility, an invalid sex and a cross-facility repeat patient;
             one stock snapshot
    batch 2  a record 21 days late (onset in week 9) and a resubmission of E2 with a later report_date
"""

import csv
import json
import os
import secrets

import pytest

os.environ.setdefault("EPISCOPE_SPARK_MASTER", "local[2]")

from pyspark.sql import functions as F  # noqa: E402

from pipeline.bronze import lake  # noqa: E402

ROOT = lake.ROOT
FIXTURE_REF = ROOT / "tests" / "fixtures" / "reference"
ENC_COLS = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
            "icd10_code", "lab_confirmed", "admitted", "outcome"]
FAC_COLS = ["facility_id", "facility_name", "facility_type", "district_code", "upazila_code", "latitude",
            "longitude", "beds", "icu_beds"]


def enc(eid, pid, fac, onset, report, icd="A90", age=30, sex="M", outcome="recovered", admitted="false"):
    return [eid, pid, fac, onset, report, str(age), sex, icd, "true", admitted, outcome]


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def batch1(data):
    raw = data / "raw"
    write_csv(raw / "facilities" / "facilities.csv", FAC_COLS, [
        ["F1", "Dhaka MCH", "medical_college_hospital", "BD3026", "", "23.7", "90.4", "100", "10"],
        ["F2", "Dhaka DH", "district_hospital", "BD3026", "", "23.8", "90.3", "50", "5"],
        ["F3", "Ctg UHC", "upazila_health_complex", "BD2015", "", "22.3", "91.8", "30", "0"],
    ])
    week9 = [
        enc("E1", "P1", "F1", "2024-02-26", "2024-03-04"),                       # dengue, week 9
        enc("E2", "P2", "F1", "2024-03-01", "2024-03-04", icd="A09", outcome="unknown"),
        enc("E3", "P3", "FX", "2024-03-01", "2024-03-04"),                       # orphan facility
        enc("E4", "P4", "F1", "2024-03-01", "2024-03-04", sex="U"),              # invalid sex
        enc("E5", "P1", "F2", "2024-02-28", "2024-03-04"),                       # DQ-4 repeat of E1 at F2
    ]
    # 12 more dengue cases at F1 in week 9 and 2 malaria cases, so suppression has visible and hidden cells
    week9 += [enc(f"E1{i:02d}", f"PX{i}", "F1", "2024-02-27", "2024-03-04", age=20 + i) for i in range(12)]
    week9 += [enc(f"EM{i}", f"PM{i}", "F2", "2024-02-29", "2024-03-04", icd="B50") for i in range(2)]
    write_csv(raw / "encounters" / "report_date=2024-03-04" / "part-0.csv", ENC_COLS, week9)
    write_csv(raw / "encounters" / "report_date=2024-03-05" / "part-0.csv", ENC_COLS, [
        enc("E2", "P2", "F1", "2024-03-01", "2024-03-05", icd="A09", outcome="recovered"),  # in-load resubmission
    ])
    write_csv(raw / "encounters" / "report_date=2024-03-13" / "part-0.csv", ENC_COLS, [
        enc("E6", "P6", "F3", "2024-03-12", "2024-03-13", icd="J11"),            # ili, week 11
    ] + [enc(f"E6{i}", f"PY{i}", "F3", "2024-03-11", "2024-03-13", icd="J11") for i in range(6)])
    stock = raw / "stock" / "snapshot_date=2024-03-04" / "part-00000.json"
    stock.parent.mkdir(parents=True, exist_ok=True)
    stock.write_text("\n".join(json.dumps({"facility_id": f, "snapshot_date": "2024-03-04", "iv_saline_units": 100,
                                           "ns1_kits": 20, "ors_packets": 300, "antimalarial_courses": 5,
                                           "occupied_beds": 10}) for f in ("F1", "F2", "F3")) + "\n")


def batch2(data):
    write_csv(data / "raw" / "encounters" / "report_date=2024-03-20" / "part-0.csv", ENC_COLS, [
        enc("E7", "P7", "F2", "2024-02-28", "2024-03-20"),                       # 21 days late, week 9
        enc("E2", "P2", "F1", "2024-03-01", "2024-03-20", icd="A09", outcome="died", admitted="true"),
    ])


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    base = tmp_path_factory.mktemp("lakehouse")
    data, lakedir = base / "data", base / "lake"
    (data / "reference").mkdir(parents=True)
    for name in ("districts.csv", "population_age_sex.csv"):
        (data / "reference" / name).write_bytes((FIXTURE_REF / name).read_bytes())
    saved = {k: os.environ.get(k) for k in ("EPISCOPE_DATA_DIR", "EPISCOPE_LAKE_DIR", "EPISCOPE_HASH_SALT")}
    os.environ.update({"EPISCOPE_DATA_DIR": str(data), "EPISCOPE_LAKE_DIR": str(lakedir),
                       "EPISCOPE_HASH_SALT": secrets.token_hex(32)})
    yield {"data": data, "lake": lakedir, "base": base}
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def spark(env):
    from pipeline.spark import get_spark

    s = get_spark("test_pipeline", memory=os.environ.get("EPISCOPE_DRIVER_MEMORY", "1g"))
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


def run_all(spark, data):
    from pipeline.bronze.ingest import ingest
    from pipeline.gold.build import build as gold
    from pipeline.silver.build import build as silver

    res = ingest(spark, data)
    s = silver(spark)
    g = gold(spark)
    return res, s, g


def cell(spark, district, disease, week, year=2024):
    rows = (lake.read(spark, "gold", "cases_weekly")
            .where((F.col("district_code") == district) & (F.col("disease") == disease)
                   & (F.col("epi_year") == year) & (F.col("epi_week") == week)).collect())
    assert len(rows) <= 1
    return rows[0] if rows else None


@pytest.fixture(scope="module")
def first_load(spark, env):
    batch1(env["data"])
    res, s, g = run_all(spark, env["data"])
    return {"bronze": res, "silver": s, "gold": g}


def test_bronze_keeps_raw_rows_with_metadata(spark, first_load):
    b = lake.read(spark, "bronze", "encounters")
    assert b.count() == 5 + 12 + 2 + 1 + 7  # every raw row, defects included
    for c in ("source", "source_file", "file_checksum", "batch_id", "load_time"):
        assert c in b.columns
    assert b.where(F.col("file_checksum").isNull()).count() == 0
    assert b.where(F.col("sex") == "U").count() == 1  # unmodified


def test_quality_split_and_quarantine(spark, first_load):
    q = lake.read(spark, "silver", "quarantine").where(F.col("source") == "encounters")
    keys = sorted(r["record_key"] for r in q.collect())
    assert keys == ["E2", "E3", "E4"]  # the older E2 copy, the orphan and the invalid sex
    silver = lake.read(spark, "silver", "encounters")
    assert silver.groupBy("encounter_id").count().where("count > 1").count() == 0
    e2 = silver.where(F.col("encounter_id") == "E2").first()
    assert str(e2["report_date"]) == "2024-03-05"  # the latest report_date wins
    dq = lake.read(spark, "gold", "dq_report").where(F.col("source") == "encounters").first()
    assert dq["rows_in"] == 27 and dq["rows_quarantined"] == 3 and dq["rows_clean"] == 24


def test_disease_mapping_and_dq4(spark, first_load):
    c = cell(spark, "BD3026", "dengue", 9)
    assert c["cases"] == 13  # E1 + 12; E5 is a DQ-4 duplicate of E1 (same patient, other facility, 2 days)
    assert c["reporting_facilities"] == 2 and c["expected_facilities"] == 2
    assert cell(spark, "BD3026", "diarrhoea", 9)["cases"] == 1
    assert cell(spark, "BD3026", "malaria", 9)["cases"] == 2
    assert cell(spark, "BD2015", "ili", 11)["cases"] == 7
    assert cell(spark, "BD2015", "dengue", 11)["cases"] == 0  # zero row: the district did report that week
    assert cell(spark, "BD2015", "ili", 9) is None             # no row: nothing reported (missing, AN-3)


def snapshot(spark):
    return sorted(tuple(r) for r in lake.read(spark, "gold", "cases_weekly").drop("batch_ids").collect())


def test_rerun_is_idempotent(spark, env, first_load):
    from pipeline.bronze.ingest import ingest
    from pipeline.gold.build import build as gold
    from pipeline.silver.build import build as silver

    before_silver = lake.read(spark, "silver", "encounters").count()
    before_gold = snapshot(spark)
    assert ingest(spark, env["data"])["batch_id"] is None  # every checksum already loaded
    silver(spark, reprocess=True)  # force the same batch through the MERGE again
    gold(spark, full=True)
    s = lake.read(spark, "silver", "encounters")
    assert s.count() == before_silver
    assert s.groupBy("encounter_id").count().where("count > 1").count() == 0
    assert lake.read(spark, "silver", "quarantine").groupBy("source", "batch_id", "record_key").count() \
        .where("count > 1").count() == 0  # quarantine slices are replaced, never appended twice
    assert snapshot(spark) == before_gold


@pytest.fixture(scope="module")
def late_load(spark, env, first_load):
    batch2(env["data"])
    res, s, g = run_all(spark, env["data"])
    return {"bronze": res, "silver": s, "gold": g}


def test_late_records_update_their_onset_week(spark, late_load):
    assert late_load["gold"]["cases_weekly"]["mode"] == "incremental"
    assert late_load["gold"]["cases_weekly"]["weeks"] == 2  # week 9 and the DQ-4 follow-on week 10
    assert cell(spark, "BD3026", "dengue", 9)["cases"] == 14  # + E7, 21 days late
    d = cell(spark, "BD3026", "diarrhoea", 9)
    assert d["cases"] == 1 and d["deaths"] == 1 and d["admissions"] == 1  # E2 replaced, not added
    assert cell(spark, "BD2015", "ili", 11)["cases"] == 7  # untouched week


def test_lineage_gold_to_bronze(spark, late_load):
    bronze_ids = {r[0] for r in lake.read(spark, "bronze", "_ingest_log").select("batch_id").distinct().collect()}
    assert len(bronze_ids) == 2
    gold_ids = {b for r in lake.read(spark, "gold", "cases_weekly").select("batch_ids").collect() for b in r[0]}
    assert gold_ids and gold_ids <= bronze_ids
    c = cell(spark, "BD3026", "dengue", 9)
    assert late_load["bronze"]["batch_id"] in c["batch_ids"] and len(c["batch_ids"]) == 2
    silver_ids = {r[0] for r in lake.read(spark, "silver", "encounters").select("batch_id").distinct().collect()}
    assert silver_ids <= bronze_ids


def test_no_patient_id_past_bronze(spark, late_load):
    tables = [("silver", n) for n in ("encounters", "quarantine", "facilities", "stock", "_changes")] + \
             [("gold", n) for n in ("cases_weekly", "stock_latest", "dq_report")]
    for layer, name in tables:
        df = lake.read(spark, layer, name)
        assert "patient_id" not in df.columns, f"{layer}.{name}"
    q = lake.read(spark, "silver", "quarantine").where(F.col("source") == "encounters")
    for r in q.collect():
        rec = json.loads(r["record"])
        assert "patient_id" not in rec and len(rec["patient_key"]) == 64
    raw_ids = {r[0] for r in lake.read(spark, "bronze", "encounters").select("patient_id").collect()}
    keys = {r[0] for r in lake.read(spark, "silver", "encounters").select("patient_key").collect()}
    assert not raw_ids & keys


def test_export_has_no_small_counts(spark, env, late_load):
    from serving.export import export

    out = env["base"] / "dashboard"
    res = export(spark, out, 10 * 1024 * 1024)
    assert res["audit"] == {"small_visible": 0, "single_suppression_lines": 0}
    weekly = json.loads((out / "weekly.json").read_text())
    assert weekly and {"d", "dz", "w", "c", "s", "inc"} == set(weekly[0])
    assert not [r for r in weekly if r["c"] is not None and 1 <= r["c"] <= 4]
    assert any(r["s"] for r in weekly) and any(r["c"] and r["c"] >= 5 for r in weekly)
    for r in weekly:
        assert (r["c"] is None) == r["s"] and (r["inc"] is None or not r["s"])
    for name in ("meta.json", "alerts.json", "forecasts.json", "allocation.json", "calendar.json", "dq.json"):
        text = (out / name).read_text()
        assert "patient" not in text
        json.loads(text)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["suppression_threshold"] == 5 and meta["data_through"] == "2024-03-20"


def test_catalog_registers_every_table(spark, env, late_load):
    """ING-3: every lake table is queryable by name from a Hive-catalog session (own process: Derby is
    single-user and this module's SparkSession has no Hive support)."""
    import subprocess
    import sys

    code = ("from pipeline import catalog as c; c.register(s := c.session()); "
            "print('ROWS', s.sql('SELECT count(*) FROM gold.cases_weekly').first()[0]); "
            "print('LEDGER', s.catalog.tableExists('bronze.ops_ingest_log')); "
            "print('AGAIN', len(c.register(s))); s.stop()")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=900,
                       env={**os.environ, "EPISCOPE_SPARK_MASTER": "local[1]", "EPISCOPE_DRIVER_MEMORY": "1g"})
    assert r.returncode == 0, r.stderr[-2000:]
    out = dict(line.split(" ", 1) for line in r.stdout.splitlines() if line.startswith(("ROWS", "LEDGER", "AGAIN")))
    assert int(out["ROWS"]) == lake.read(spark, "gold", "cases_weekly").count()
    assert out["LEDGER"] == "True"
    assert out["AGAIN"] == "0"  # re-registering is a no-op


def test_time_travel_reproduces_what_we_knew(spark, late_load):
    """ING-7: as of a date between the two loads, gold is the first load's version, without the late record."""
    import datetime as dt

    from pipeline.gold.asof import cases_as_of, version_as_of

    assert version_as_of(spark, dt.date(2024, 3, 1)) is None            # before any report
    before = cases_as_of(spark, dt.date(2024, 3, 15))                    # batch 1 runs to 13 March
    now = cases_as_of(spark, dt.date(2024, 3, 31))
    key = (F.col("district_code") == "BD3026") & (F.col("disease") == "dengue") & (F.col("epi_week") == 9)
    assert now.where(key).first()["cases"] == before.where(key).first()["cases"] + 1  # E7, 21 days late
    assert version_as_of(spark, dt.date(2024, 3, 15))[1] == dt.date(2024, 3, 13)


def test_stream_refreshes_alerts_on_new_file(spark, env, late_load, monkeypatch, tmp_path):
    """ING-6: a new encounter file picked up by the file-source stream runs the incremental chain and rewrites
    gold.alerts; the latency is logged."""
    from analytics.detection import config as DC
    from pipeline import stream

    monkeypatch.setattr(DC, "ALERTS_PATH", env["lake"] / "gold" / "alerts")
    monkeypatch.setattr(stream, "LATENCY_LOG", tmp_path / "latency.json")
    write_csv(env["data"] / "raw" / "encounters" / "report_date=2024-03-27" / "part-0.csv", ENC_COLS, [
        enc("E8", "P8", "F3", "2024-03-25", "2024-03-27", icd="J11")])
    stream.start(spark, once=True, export=False).awaitTermination()
    log = json.loads((tmp_path / "latency.json").read_text())
    assert log[-1]["batch_id"] is not None and log[-1]["latency_seconds"] >= 0
    assert lake.exists("gold", "alerts")
    assert cell(spark, "BD2015", "ili", 13)["cases"] == 1  # the streamed record reached gold
