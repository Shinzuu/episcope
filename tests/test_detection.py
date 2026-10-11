"""Tests for analytics/detection (AN-1, AN-3) and analytics/evaluation (AN-2).

The synthetic weekly table below stands in for gold.cases_weekly: 3 districts x 2 diseases x 3 ISO years, with
seasonality, two injected outbreaks (one fast, one slow) and one silent-then-backfill reporting artefact.
"""

import datetime as dt
import os
import pathlib
import re
import sys

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("EPISCOPE_DRIVER_MEMORY", "1g")
os.environ.setdefault("EPISCOPE_SPARK_MASTER", "local[2]")
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)   # applyInPandas workers need the venv
for _v in ("HADOOP_CONF_DIR", "HADOOP_HOME", "YARN_CONF_DIR"):
    os.environ.pop(_v, None)

from analytics.detection.methods import (  # noqa: E402
    ALERT_COLS, CUSUM_INPUT, cusum_frame, detect, ears_c2, naive_ears, score_inputs, to_alerts)
from analytics.detection.methods import baseline_z  # noqa: E402
from analytics.detection.prepare import densify, prepare  # noqa: E402
from analytics.evaluation import metrics, sweep  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---- synthetic gold.cases_weekly --------------------------------------------------------------------------

FIRST_MONDAY = dt.date.fromisocalendar(2022, 1, 1)
N_WEEKS = 156                       # three 52-week ISO years
EXPECTED_FACILITIES = 4
DISTRICTS = {"D1": "V1", "D2": "V1", "D3": "V2"}
LEVEL = {("D1", "dengue"): 10, ("D2", "dengue"): 18, ("D3", "dengue"): 8,
         ("D1", "diarrhoea"): 40, ("D2", "diarrhoea"): 60, ("D3", "diarrhoea"): 50}
SEASON = {"dengue": (1.3, 36), "diarrhoea": (0.5, 18)}   # (amplitude, peak ISO week)

FAST = {"id": "OB-FAST", "district": "D1", "disease": "dengue", "week": 52 + 18,
        "kernel": [1.5, 3.0, 2.5, 1.0]}                       # off-season gamma-like pulse
SLOW = {"id": "OB-SLOW", "district": "D2", "disease": "diarrhoea", "week": 104 + 38, "weeks": 8, "peak": 1.6}
ARTEFACT = {"district": "D3", "week": 52 + 29, "silent_weeks": 3, "share": 0.5}


def _week_start(i):
    return FIRST_MONDAY + dt.timedelta(weeks=i)


def build_synthetic(seed=7, artefact=True):
    """Return (onset_weekly, report_weekly, outbreaks). Both weekly frames follow the gold.cases_weekly contract.

    onset_weekly   counts by onset week (what gold holds). During the artefact the big facility is silent, so
                   reporting_facilities drops and its share of cases is absent.
    report_weekly  the same counts by report week, with missing reports as plain low counts and the backlog
                   dumped into the week the facility comes back: what a naive report-date detector sees.
    """
    rng = np.random.default_rng(seed)
    rows_on, rows_rep = [], []
    for (d, dz), lvl in LEVEL.items():
        amp, peak = SEASON[dz]
        woy = np.array([_week_start(i).isocalendar()[1] for i in range(N_WEEKS)])
        mu = lvl * np.exp(amp * np.cos(2 * np.pi * (woy - peak) / 52))
        mult = np.ones(N_WEEKS)
        if (d, dz) == (FAST["district"], FAST["disease"]):
            for j, kx in enumerate(FAST["kernel"]):
                mult[FAST["week"] + j] += kx
        if (d, dz) == (SLOW["district"], SLOW["disease"]):
            for j in range(SLOW["weeks"]):
                mult[SLOW["week"] + j] += SLOW["peak"] / (1 + np.exp(-(j - 3)))
        lam = mu * mult
        cases = rng.poisson(rng.gamma(30, lam / 30))          # negative binomial, size 30
        rep = np.full(N_WEEKS, EXPECTED_FACILITIES)
        on, by_report = cases.astype(float), cases.astype(float)
        if artefact and d == ARTEFACT["district"]:
            w0, n = ARTEFACT["week"], ARTEFACT["silent_weeks"]
            held = np.round(cases[w0:w0 + n] * ARTEFACT["share"])
            on, by_report = on.copy(), by_report.copy()
            on[w0:w0 + n] -= held
            by_report[w0:w0 + n] -= held
            by_report[w0 + n] += held.sum()                   # backfill lands in one report week
            rep[w0:w0 + n] = EXPECTED_FACILITIES - 1
        for i in range(N_WEEKS):
            ws = _week_start(i)
            y, wk, _ = ws.isocalendar()
            base = dict(district_code=d, division_code=DISTRICTS[d], disease=dz, epi_year=y, epi_week=wk,
                        week_start=ws, admissions=0, deaths=0, lab_confirmed=0,
                        expected_facilities=EXPECTED_FACILITIES, batch_ids=["b1"])
            rows_on.append({**base, "cases": int(on[i]), "reporting_facilities": int(rep[i])})
            rows_rep.append({**base, "cases": int(by_report[i]), "reporting_facilities": EXPECTED_FACILITIES})

    def ob(o, end_week):
        return {"outbreak_id": o["id"], "disease": o["disease"], "district_code": o["district"],
                "start_date": pd.Timestamp(_week_start(o["week"])), "end_date": pd.Timestamp(_week_start(end_week) + dt.timedelta(days=6)),
                "peak_multiplier": 1.0, "shape": "fast" if o is FAST else "slow"}

    outbreaks = pd.DataFrame([ob(FAST, FAST["week"] + len(FAST["kernel"]) - 1), ob(SLOW, SLOW["week"] + SLOW["weeks"] - 1)])
    return pd.DataFrame(rows_on), pd.DataFrame(rows_rep), outbreaks


SCHEMA = ("district_code string, division_code string, disease string, epi_year int, epi_week int, "
          "week_start date, cases long, admissions long, deaths long, lab_confirmed long, "
          "reporting_facilities long, expected_facilities long, batch_ids array<string>")
COLS = [c.split()[0] for c in SCHEMA.split(", ")]


@pytest.fixture(scope="module")
def spark():
    from pipeline.spark import get_spark

    s = get_spark("test-detection")
    s.conf.set("spark.sql.shuffle.partitions", "4")
    yield s
    s.stop()


@pytest.fixture(scope="module")
def synthetic():
    return build_synthetic()


@pytest.fixture(scope="module")
def scored(spark, synthetic):
    onset, _, _ = synthetic
    df = spark.createDataFrame(onset[COLS], SCHEMA)
    return detect(df).toPandas()


def _alerts_at(pdf, district, weeks, disease=None):
    ws = {pd.Timestamp(_week_start(i)) for i in weeks}
    m = (pdf["district_code"] == district) & pd.to_datetime(pdf["week_start"]).isin(ws) & pdf["alert"]
    if disease:
        m &= pdf["disease"] == disease
    return pdf[m]


# ---- detection ---------------------------------------------------------------------------------------------

def test_alerts_follow_contract(spark, synthetic):
    onset, _, _ = synthetic
    alerts = to_alerts(detect(spark.createDataFrame(onset[COLS], SCHEMA)))
    assert alerts.columns == ALERT_COLS
    types = dict(alerts.dtypes)
    assert types["statistic"] == "double" and types["alert"] == "boolean" and types["epi_week"] == "int"
    pdf = alerts.toPandas()
    assert set(pdf["method"]) == {"ears_c2", "cusum"}
    assert len(pdf) == 2 * len(LEVEL) * N_WEEKS


def test_known_outbreaks_detected(scored, synthetic):
    _, _, outbreaks = synthetic
    for method in ("ears_c2", "cusum"):
        res = metrics.evaluate(scored[scored["method"] == method], outbreaks)
        per = res["per_outbreak"].set_index("outbreak_id")
        assert per.loc["OB-FAST", "detected"], f"{method} missed the fast outbreak"
        assert per.loc["OB-FAST", "days_to_detection"] <= 21     # within the 4-week pulse
    cus = metrics.evaluate(scored[scored["method"] == "cusum"], outbreaks)["per_outbreak"].set_index("outbreak_id")
    assert cus.loc["OB-SLOW", "detected"], "CUSUM missed the slow outbreak"


def test_artefact_not_flagged_but_naive_detector_is(spark, synthetic, scored):
    onset, by_report, _ = synthetic
    w0, n = ARTEFACT["week"], ARTEFACT["silent_weeks"]
    window = range(w0, w0 + n + 4)
    district = ARTEFACT["district"]

    # Ours: silent facility-weeks are missing, not low counts ...
    silent = scored[(scored["district_code"] == district)
                    & pd.to_datetime(scored["week_start"]).isin([pd.Timestamp(_week_start(i)) for i in range(w0, w0 + n)])]
    assert silent["statistic"].isna().all(), "silent weeks must be missing, not low counts"
    # ... and the artefact adds no alert: every alert around it also fires on the same data without the artefact.
    clean_onset, _, _ = build_synthetic(artefact=False)
    clean = detect(spark.createDataFrame(clean_onset[COLS], SCHEMA)).toPandas()

    def keys(pdf):
        return set(map(tuple, _alerts_at(pdf, district, window)[["disease", "method", "week_start"]].astype(str).values))

    assert keys(scored) <= keys(clean), keys(scored) - keys(clean)

    # Naive: report-week counts, zeros as zeros, no seasonal adjustment -> fires on the backfill week.
    naive_in = prepare(spark.createDataFrame(by_report[COLS], SCHEMA), use_completeness=False, use_seasonal=False)
    naive = naive_ears(naive_in).toPandas()
    assert not _alerts_at(naive, district, [w0 + n]).empty, "naive detector should flag the backfill"


def test_seasonal_adjustment_cuts_false_alerts(spark, synthetic, scored):
    onset, _, outbreaks = synthetic
    raw = ears_c2(score_inputs(prepare(spark.createDataFrame(onset[COLS], SCHEMA), use_seasonal=False))).toPandas()
    ours = scored[scored["method"] == "ears_c2"]
    f_raw = metrics.evaluate(raw, outbreaks)["false_alerts"]
    f_ours = metrics.evaluate(ours, outbreaks)["false_alerts"]
    assert f_ours < f_raw, (f_ours, f_raw)


def test_missing_reports_are_missing_not_zero(spark):
    ws = [_week_start(i) for i in range(3)]
    rows = [
        # week 0: both diseases reported
        ("D1", "V1", "dengue", 2022, 1, ws[0], 5, 0, 0, 0, 4, 4, []),
        ("D1", "V1", "diarrhoea", 2022, 1, ws[0], 9, 0, 0, 0, 4, 4, []),
        # week 1: only diarrhoea row -> dengue is a true zero
        ("D1", "V1", "diarrhoea", 2022, 2, ws[1], 8, 0, 0, 0, 4, 4, []),
        # week 2: no rows at all for D1 -> missing
        ("D2", "V1", "dengue", 2022, 3, ws[2], 1, 0, 0, 0, 4, 4, []),
    ]
    d = densify(spark.createDataFrame(rows, SCHEMA)).toPandas().set_index(["district_code", "disease", "week_start"])
    assert d.loc[("D1", "dengue", ws[1]), "cases"] == 0
    assert np.isnan(d.loc[("D1", "dengue", ws[2]), "cases"])
    assert d.loc[("D1", "dengue", ws[2]), "completeness"] == 0


def test_cusum_accumulates_and_resets():
    z = np.r_[np.zeros(20), np.full(6, 1.5), np.zeros(4), [np.nan], np.full(6, 1.5)]
    ws = [_week_start(i) for i in range(len(z))]
    pdf = pd.DataFrame({"district_code": "D", "disease": "x", "week_start": ws, "epi_year": 2022,
                        "epi_week": [w.isocalendar()[1] for w in ws], "cases": 50.0, "cusum_z": z})
    out = cusum_frame(pdf, k=0.5, h=2.5)
    # S grows by z - k = 1 per week: 1, 2, 3 -> alarm on the 3rd excess week, reset, then 1, 2, 3 again.
    assert out["alert"].to_numpy().nonzero()[0].tolist() == [22, 25, 33, 36]
    assert out["statistic"][22] == pytest.approx(3.0) and out["statistic"][23] == pytest.approx(1.0)
    assert np.isnan(out["statistic"][30])                    # missing week: no statistic, S carried
    gated = cusum_frame(pdf.assign(cases=0.0), k=0.5, h=2.5)
    assert not gated["alert"].any()                          # limit54-style gate blocks near-empty series


def test_baseline_z_matches_hand_computation(spark):
    """EARS z for one week, recomputed by hand from the module formula."""
    y = [10.0, 12.0, 8.0, 11.0, 9.0, 10.0, 14.0, 0.0, 0.0, 20.0]       # weeks 0..6 baseline for week 9
    s = [1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 1.0, 1.0, 2.0]
    rows = [("D", "x", _week_start(i), yi, si) for i, (yi, si) in enumerate(zip(y, s))]
    df = spark.createDataFrame(rows, "district_code string, disease string, week_start date, adj_cases double, "
                                     "season_index double")
    z = baseline_z(df, 3, 9, "z").orderBy("week_start").toPandas()["z"].iloc[9]
    yb, sb = np.array(y[:7]), np.array(s[:7])
    m = yb.sum() / sb.sum()
    phi = max(1.0, ((yb - sb * m) ** 2 / (sb * m)).sum() / 6)
    e = 2.0 * m
    assert z == pytest.approx((20.0 - e) / max(np.sqrt(phi * e), 1.0))


def test_detection_never_references_ground_truth():
    pattern = re.compile(r"ground[\s_-]?truth|outbreaks\.csv", re.I)
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "analytics" / "detection").rglob("*.py")
                 if pattern.search(p.read_text())]
    assert not offenders, f"detection code references the evaluation labels: {offenders}"


# ---- evaluation --------------------------------------------------------------------------------------------

def test_metrics_hand_built_example(tmp_path):
    weeks = [dt.date(2024, 1, 1) + dt.timedelta(weeks=i) for i in range(10)]
    fire = {("A", dt.date(2024, 1, 1)), ("A", dt.date(2024, 1, 22)), ("A", dt.date(2024, 1, 29)),
            ("B", dt.date(2024, 1, 8)), ("B", dt.date(2024, 2, 26)), ("B", dt.date(2024, 3, 4))}
    alerts = pd.DataFrame([{"district_code": d, "disease": "dengue", "week_start": w, "method": "m",
                            "alert": (d, w) in fire} for d in "AB" for w in weeks])
    gt = tmp_path / "outbreaks.csv"
    gt.write_text(
        "outbreak_id,disease,district_code,start_date,end_date,peak_multiplier,shape\n"
        "O1,dengue,A,2024-01-17,2024-01-31,3,fast\n"     # hit by A 01-22: detected 01-28, 11 days
        "O2,dengue,B,2024-02-12,2024-02-14,2,fast\n"     # window to 02-21, no alert: missed
        "O3,dengue,ALL,2024-03-01,2024-03-03,2,nationwide\n"  # hit by B 02-26: detected 03-03, 2 days
        "O4,malaria,A,2024-01-10,2024-01-20,2,fast\n"    # disease not in the data: out of scope
    )
    m = metrics.evaluate(alerts, metrics.load_outbreaks(gt))
    assert m["n_outbreaks"] == 3 and m["detected"] == 2
    assert m["sensitivity"] == pytest.approx(2 / 3)
    assert m["alert_episodes"] == 4 and m["false_alerts"] == 2       # A{01-01} and B{01-08}
    assert m["district_years"] == pytest.approx(2 * 10 / (365.2425 / 7))
    assert m["false_alerts_per_district_year"] == pytest.approx(2 / (2 * 10 / (365.2425 / 7)))
    assert m["ppv"] == pytest.approx(0.5)
    assert m["median_days_to_detection"] == pytest.approx(6.5)          # median of 11 and 2
    assert m["detected_within_7d"] == pytest.approx(1 / 3)
    assert m["detected_within_14d"] == pytest.approx(2 / 3)
    per = m["per_outbreak"].set_index("outbreak_id")
    assert per.loc["O1", "days_to_detection"] == 11 and per.loc["O3", "days_to_detection"] == 2
    assert m["by_shape"]["nationwide"]["detected"] == 1


def test_sweep_curve_and_recommendation(spark, synthetic):
    onset, _, outbreaks = synthetic
    inputs = score_inputs(prepare(spark.createDataFrame(onset[COLS], SCHEMA))).cache()
    prepared_pd = inputs.select("district_code", "disease", *CUSUM_INPUT, "gate").toPandas()
    ears_pd = ears_c2(inputs).toPandas()
    from analytics.evaluation.run import evaluate_frames

    curve, report = evaluate_frames(prepared_pd, ears_pd, outbreaks, budget=1.0)
    assert set(curve["method"]) == {"ears_c2", "cusum"}
    assert len(curve) == len(sweep.EARS_GRID) + len(sweep.CUSUM_GRID)
    e = curve[curve["method"] == "ears_c2"].sort_values("param")
    assert e["sensitivity"].is_monotonic_decreasing                       # higher threshold, fewer detections
    for method, r in report["recommended"].items():
        assert r["budget_met"] and r["false_alerts_per_district_year"] <= 1.0
        assert r["sensitivity"] == curve[(curve["method"] == method)
                                         & (curve["false_alerts_per_district_year"] <= 1.0)]["sensitivity"].max()
    assert report["curve"] and "Sensitivity" in report["definitions"]
