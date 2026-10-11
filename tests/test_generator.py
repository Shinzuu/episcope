"""Tests for the synthetic generator (ING-1; defects for DQ-1/DQ-2; ground truth for AN-2/AN-3).

Runs at a tiny scale factor on the 8-district fixture in tests/fixtures/reference (created if missing).
"""

import glob
import hashlib
import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from generator import calendar_bd as cal
from generator import facilities as F
from generator import generate as G
from generator import reference as R

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "reference"
SCHEMA = json.loads((ROOT / "generator" / "schema.json").read_text())
CHT_COX = {"BD2003", "BD2084", "BD2046", "BD2022"}


def fields(section):
    return [f["name"] for f in SCHEMA[section]["fields"]]


def run(out, truth, *extra, sf="0.03", seed="42"):
    if not (FIX / "weather_daily.parquet").exists():
        R.write_fixture(FIX)
    args = ["--sf", sf, "--seed", seed, "--reference", str(FIX), "--out", str(out), "--truth-dir", str(truth),
            "--workers", "2", *extra]
    return G.main(args)


def load_encounters(out):
    files = sorted(glob.glob(f"{out}/encounters/report_date=*/part-*.csv"))
    return pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in files], ignore_index=True)


def tree_hash(*dirs):
    h = hashlib.sha256()
    for d in dirs:
        for p in sorted(pathlib.Path(d).rglob("*")):
            if p.is_file() and p.name != "_manifest.json":
                h.update(str(p.relative_to(d)).encode())
                h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    base = tmp_path_factory.mktemp("gen")
    manifest = run(base / "raw", base / "gt", "--start", "2023-01-01", "--end", "2024-12-31")
    df = load_encounters(base / "raw")
    return {"base": base, "raw": base / "raw", "gt": base / "gt", "df": df, "manifest": manifest,
            "fac": pd.read_csv(base / "raw/facilities/facilities.csv", dtype=str)}


def test_layout_and_schema(gen):
    raw = gen["raw"]
    enc_files = glob.glob(f"{raw}/encounters/report_date=*/part-*.csv")
    assert enc_files
    want = fields("S4_encounters")
    for f in enc_files:
        part = pathlib.Path(f).parent.name
        assert part.startswith("report_date=") and str(np.datetime64(part[12:], "D")) == part[12:]
        assert open(f).readline().strip().replace('"', "").split(",") == want
    df = gen["df"]
    pd.to_datetime(df.onset_date, format="%Y-%m-%d")
    pd.to_datetime(df.report_date, format="%Y-%m-%d")
    assert pd.to_numeric(df.age_years).dtype.kind == "i"
    assert set(df.lab_confirmed) <= {"true", "false"} and set(df.admitted) <= {"true", "false"}
    assert set(df.outcome) <= {"recovered", "referred", "died", "unknown"}

    fac = pd.read_csv(raw / "facilities/facilities.csv")
    assert list(fac.columns) == fields("S5_facilities")
    assert fac.facility_id.is_unique and (fac.beds > 0).all()
    assert set(fac.facility_type) <= {"district_hospital", "upazila_health_complex", "clinic",
                                      "medical_college_hospital"}

    stock = sorted(glob.glob(f"{raw}/stock/snapshot_date=*/part-*.json"))
    assert len(stock) == 731  # one snapshot per day of the 2023-2024 window
    rec = json.loads(open(stock[0]).readline())
    assert list(rec) == fields("S6_stock")
    assert all(isinstance(rec[k], int) for k in fields("S6_stock") if k not in ("facility_id", "snapshot_date"))
    assert rec["snapshot_date"] == pathlib.Path(stock[0]).parent.name.split("=")[1]

    gt = pd.read_csv(gen["gt"] / "outbreaks.csv")
    assert list(gt.columns) == fields("ground_truth_outbreaks")
    # ground truth never lands inside the pipeline input tree
    assert not list(raw.rglob("*outbreak*"))


def test_determinism(tmp_path):
    window = ("--start", "2023-03-01", "--end", "2023-04-15")
    run(tmp_path / "a/raw", tmp_path / "a/gt", *window)
    run(tmp_path / "b/raw", tmp_path / "b/gt", *window)
    run(tmp_path / "c/raw", tmp_path / "c/gt", *window, seed="43")
    a = tree_hash(tmp_path / "a/raw", tmp_path / "a/gt")
    assert a == tree_hash(tmp_path / "b/raw", tmp_path / "b/gt")
    assert a != tree_hash(tmp_path / "c/raw", tmp_path / "c/gt")


def test_defect_rates(gen):
    df, fac = gen["df"], gen["fac"]
    n = len(df)
    assert n > 50_000
    dup = (n - df.encounter_id.nunique()) / n
    assert 0.015 <= dup <= 0.025, dup
    orphan = (~df.facility_id.isin(fac.facility_id)).mean()
    assert 0.003 <= orphan <= 0.008, orphan
    age = pd.to_numeric(df.age_years)
    bad_age, bad_sex = ((age < 0) | (age > 120)).mean(), (~df.sex.isin(["M", "F"])).mean()
    assert 0.003 <= bad_age <= 0.008 and 0.003 <= bad_sex <= 0.008
    assert 0.006 <= ((age < 0) | (age > 120) | ~df.sex.isin(["M", "F"])).mean() <= 0.015
    after = (df.onset_date > df.report_date).mean()
    assert 0 < after < 0.01
    bad_icd = (~df.icd10_code.str.fullmatch(r"[A-Z]\d\d")).mean()
    assert 0 < bad_icd < 0.01


def test_outbreak_ground_truth(gen):
    gt = pd.read_csv(gen["gt"] / "outbreaks.csv")
    assert len(gt) >= 30
    assert gt.outbreak_id.is_unique
    assert {"fast", "slow", "nationwide"} <= set(gt["shape"])
    assert (gt.loc[gt["shape"] == "nationwide", "district_code"] == "ALL").all()
    assert set(gt.disease) <= {"dengue", "diarrhoea", "ili", "malaria"}
    assert (gt.start_date >= "2023-01-01").all() and (gt.end_date <= "2024-12-31").all()
    assert (gt.start_date <= gt.end_date).all()
    assert gt.peak_multiplier.between(1.5, 6.0).all()
    assert gt.district_code.nunique() >= 5  # varied places


def disease_of(icd):
    c = icd.str[:3]
    return np.select([c.isin(["A90", "A91"]), c.isin(["A00", "A09"]), c.isin(["J10", "J11"]),
                      c.isin(["B50", "B51", "B52", "B53", "B54"])], ["dengue", "diarrhoea", "ili", "malaria"],
                     "other")


def test_seasonality_and_geography(gen):
    df, fac = gen["df"], gen["fac"]
    valid = df[df.onset_date <= df.report_date]
    dz = disease_of(valid.icd10_code)
    month = pd.to_datetime(valid.onset_date).dt.month
    peak = {d: month[dz == d].value_counts().idxmax() for d in ["dengue", "ili", "malaria"]}
    assert peak["dengue"] in (9, 10, 11), peak
    assert peak["ili"] in (5, 6, 7, 8, 9), peak
    assert peak["malaria"] in (5, 6, 7, 8, 9, 10), peak
    dia = month[dz == "diarrhoea"].value_counts()
    assert dia[[3, 4, 5]].mean() > dia[[1, 12]].mean() and dia[[9, 10, 11]].mean() > dia[[1, 12]].mean()

    district = valid.facility_id.map(dict(zip(fac.facility_id, fac.district_code)))
    mal = district[dz == "malaria"].dropna()
    assert mal.isin(CHT_COX).mean() >= 0.8
    pop = pd.read_csv(FIX / "districts.csv").set_index("district_code").population_2022
    dengue_rate = district[dz == "dengue"].value_counts() / pop
    assert dengue_rate.idxmax() == "BD3026"  # concentrated in the big city


def test_reporting_delay_and_weekend(gen):
    df = gen["df"]
    arts = set(pd.read_csv(gen["gt"] / "artefacts.csv").facility_id)
    ok = df[(df.onset_date <= df.report_date) & ~df.facility_id.isin(arts)]
    delay = (pd.to_datetime(ok.report_date) - pd.to_datetime(ok.onset_date)).dt.days
    assert (delay <= 21).mean() > 0.995
    assert delay.skew() > 1  # right-skewed
    share = pd.to_datetime(df.report_date).dt.dayofweek.value_counts(normalize=True)
    assert share[[4, 5]].mean() < 0.6 * share[[0, 1, 2, 3]].mean()  # Fri/Sat dip


def test_severity_cascade(gen):
    df = gen["df"]
    adm = df.admitted == "true"
    assert 0.05 < adm.mean() < 0.4
    died = df.outcome == "died"
    assert 0 < died.mean() < 0.02
    assert died[adm].mean() > died[~adm].mean()
    j10 = df[df.icd10_code == "J10"]
    assert (j10.lab_confirmed == "true").all()


def test_silent_facility_backfills(gen):
    df = gen["df"]
    arts = pd.read_csv(gen["gt"] / "artefacts.csv")
    assert len(arts) >= 1
    for a in arts.itertuples():
        rep = df[df.facility_id == a.facility_id].report_date
        silent = rep[(rep >= a.silent_start) & (rep <= a.silent_end)]
        assert silent.empty
        days = (pd.Timestamp(a.backfill_date) - pd.Timestamp(a.silent_start)).days
        assert days == 21
        daily = rep.value_counts()
        assert daily[a.backfill_date] > 5 * daily.median()


def test_incremental_drop(tmp_path):
    run(tmp_path / "raw", tmp_path / "gt", "--start", "2023-03-01", "--end", "2023-03-31")
    before = load_encounters(tmp_path / "raw")
    fac_before = (tmp_path / "raw/facilities/facilities.csv").read_bytes()
    m = run(tmp_path / "raw", tmp_path / "gt", "--incremental-days", "14", seed="7")
    assert (m["start"], m["end"]) == ("2023-04-01", "2023-04-14")
    after = load_encounters(tmp_path / "raw")
    new = after[after.report_date >= "2023-04-01"]
    assert sorted(new.report_date.unique()) == [str(d.date()) for d in pd.date_range("2023-04-01", "2023-04-14")]
    assert (new.onset_date < "2023-04-01").any()  # late records for earlier weeks arrive in the drop
    assert not set(new.encounter_id) & set(before.encounter_id)
    assert (tmp_path / "raw/facilities/facilities.csv").read_bytes() == fac_before
    stock_days = sorted(p.name for p in (tmp_path / "raw/stock").iterdir())
    assert stock_days[-1] == "snapshot_date=2023-04-14"
    gt = pd.read_csv(tmp_path / "gt/outbreaks.csv")
    assert gt.outbreak_id.str.startswith("OB7-20230401-").sum() >= 3
    assert gt.outbreak_id.str.startswith("OB42-").sum() >= 30
    # re-running the same drop is idempotent
    run(tmp_path / "raw", tmp_path / "gt", "--start", "2023-04-01", "--incremental-days", "14", seed="7")
    again = load_encounters(tmp_path / "raw")
    assert len(again) == len(after)


def test_facilities_at_national_scale():
    rng = np.random.default_rng(0)
    districts = pd.DataFrame({
        "district_code": [f"BD{1000 + i}" for i in range(64)], "district_name": [f"D{i}" for i in range(64)],
        "lat": rng.uniform(21, 26, 64), "lon": rng.uniform(88, 92.5, 64),
        "population_2022": (rng.lognormal(14.6, 0.5, 64)).astype(int),
    })
    fac = F.build(districts, 42)
    assert len(fac) >= 600
    assert fac.facility_id.is_unique
    assert fac.district_code.nunique() == 64 and (fac.beds > 0).all()


def test_holiday_calendar():
    h = cal.holidays(np.datetime64("2024-01-01"), np.datetime64("2024-12-31"))
    days = {str(np.datetime64(int(d), "D")) for d in h}
    assert {"2024-02-21", "2024-03-26", "2024-12-16", "2024-04-10", "2024-06-17"} <= days
    assert cal.weekday(np.datetime64("2024-10-11").astype(np.int64)) == cal.FRIDAY
