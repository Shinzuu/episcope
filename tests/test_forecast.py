"""Tests for AN-4 (forecast), AN-5 (WIS, backtest), AN-6 (demand) and AN-9 (calendar), on small synthetic inputs."""

import datetime as dt
import os

import numpy as np
import pandas as pd
import pytest

from analytics.calendar import stl_calendar as C
from analytics.forecast import demand as D
from analytics.forecast import features as F
from analytics.forecast.backtest import backtest_series, choose_origins, summarise
from analytics.forecast.model import QUANTILES, forecast_series, qcol, seasonal_naive
from analytics.forecast.scoring import coverage, wis

START = pd.Timestamp("2021-01-04")   # a Monday; test data only
N_WEEKS = 3 * 52 + 20
DISTRICTS = {"BD1004": "BD10", "BD1006": "BD10", "BD2003": "BD20"}


def _synthetic(seed=7):
    """3 districts x 3 diseases weekly cases driven by lagged rain + seasonality, and weekly weather."""
    rng = np.random.default_rng(seed)
    weeks = pd.date_range(START, periods=N_WEEKS, freq="7D")
    doy = (weeks + pd.Timedelta(days=3)).dayofyear.values
    cases, weather = [], []
    for i, (d, div) in enumerate(DISTRICTS.items()):
        rain = np.clip(60 + 50 * np.cos(2 * np.pi * (doy - 200) / 365.25) + rng.normal(0, 12, N_WEEKS), 0, None)
        temp = 27 + 3 * np.cos(2 * np.pi * (doy - 170) / 365.25) + rng.normal(0, 0.5, N_WEEKS)
        rain_l6 = pd.Series(rain).shift(6).bfill().values
        iso = weeks.isocalendar()
        for y_, w_, p_, t_ in zip(iso["year"], iso["week"], rain, temp):
            weather.append({"district_code": d, "epi_year": int(y_), "epi_week": int(w_),
                            "precip_mm_sum": float(p_), "tmean_c_avg": float(t_)})
        mus = {
            "dengue": np.exp(2.0 + 0.3 * i + 0.9 * np.cos(2 * np.pi * (doy - 285) / 365.25) + 0.012 * (rain_l6 - 60)),
            "diarrhoea": np.exp(3.0 + 0.4 * np.cos(4 * np.pi * (doy - 100) / 365.25)),
            "malaria": np.exp(0.2 + 0.8 * np.cos(2 * np.pi * (doy - 200) / 365.25)) * (1 if div == "BD20" else 0.05),
        }
        for dz, mu in mus.items():
            y = rng.negative_binomial(5, 5 / (5 + mu))
            for k, ws in enumerate(weeks):
                cases.append({"district_code": d, "division_code": div, "disease": dz,
                              "epi_year": int(iso["year"].iloc[k]), "epi_week": int(iso["week"].iloc[k]),
                              "week_start": ws.date(), "cases": int(y[k]), "admissions": int(round(0.2 * y[k])),
                              "deaths": 0, "lab_confirmed": 0, "reporting_facilities": 10, "expected_facilities": 10})
    return pd.DataFrame(cases), pd.DataFrame(weather)


CASES, WEATHER = _synthetic()
END = pd.Timestamp(CASES["week_start"].max())


def _grid(district="BD2003", disease="dengue"):
    c = CASES[(CASES.district_code == district) & (CASES.disease == disease)]
    w = WEATHER[WEATHER.district_code == district]
    return F.weekly_grid(c, w, START, END)


# ---------- WIS (AN-5) ----------

def test_wis_hand_example():
    # y inside the 80% interval [5, 12], median 8: (0.5*|10-8| + 0.1*(12-5)) / 1.5 = 1.7 / 1.5
    assert wis(10, 8, [5], [12], [0.2])["wis"] == pytest.approx(1.7 / 1.5)
    # y above the interval: IS = 7 + (2/0.2)*(15-12) = 37; (0.5*7 + 0.1*37) / 1.5 = 4.8
    r = wis(15, 8, [5], [12], [0.2])
    assert r["wis"] == pytest.approx(4.8)
    assert r["dispersion"] + r["overprediction"] + r["underprediction"] == pytest.approx(r["wis"])
    assert r["overprediction"] == 0 and r["dispersion"] == pytest.approx(0.7 / 1.5)
    # K = 0: WIS is the absolute error of the median
    assert wis(3, 7, [], [], [])["wis"] == pytest.approx(4)
    # two intervals, y below both: alphas 0.2 [5,12] and 0.5 [6,10], median 8, y = 2
    is1 = 7 + 10 * 3
    is2 = 4 + 4 * 4
    expect = (0.5 * 6 + 0.1 * is1 + 0.25 * is2) / 2.5
    assert wis(2, 8, [5, 6], [12, 10], [0.2, 0.5])["wis"] == pytest.approx(expect)


def test_coverage_counts_bounds_inclusive():
    assert coverage([1, 5, 9, 12], [1, 4, 4, 4], [3, 5, 8, 20]) == pytest.approx(0.75)


# ---------- model (AN-4) ----------

def test_forecast_shape_and_quantiles_ordered():
    fc = forecast_series(_grid(), [END])
    assert list(fc["horizon"]) == [1, 2, 3, 4]
    assert (fc["origin_week_start"] == END).all()
    assert list(fc["target_week_start"]) == [END + pd.Timedelta(weeks=h) for h in range(1, 5)]
    q = fc[[qcol(x) for x in QUANTILES]].values
    assert (np.diff(q, axis=1) >= 0).all()
    assert (q >= 0).all() and fc["mean"].gt(0).all()
    assert (fc["method"] == "nb2").all()


def test_features_use_only_past_data():
    g = _grid()
    for h in (1, 4):
        X = F.design(g, h)
        target = END + pd.Timedelta(weeks=h)
        assert not X.loc[target, ["rain", "temp", "level"]].isna().any()   # computable at the origin
        assert X.loc[target + pd.Timedelta(weeks=1):, "level"].isna().all()  # beyond h it would need future cases
        # level for the h-ahead target only sees cases up to the origin
        assert X.loc[END + pd.Timedelta(weeks=h), "level"] == pytest.approx(np.log1p(g["cases"].loc[:END].tail(4).mean()))


def test_seasonal_naive_same_week_last_year():
    g = _grid()
    t = len(g.loc[:END]) - 1
    method, point, q = seasonal_naive(g["cases"], t, 2)
    assert method == "seasonal_naive"
    assert point == g["cases"].loc[END + pd.Timedelta(weeks=2) - pd.Timedelta(days=364)]
    assert (np.diff(q) >= 0).all()


def test_backtest_runs_and_scores():
    g = _grid()
    origins = choose_origins(START, END, 8)
    assert len(origins) == 8 and max(origins) < END
    bt = backtest_series(g, origins, refit_every=4)
    assert len(bt) == 8 * 4 - (1 + 2 + 3)          # targets past the last week are not scored
    m = summarise(bt.assign(district_code="BD2003", disease="dengue"))
    allrow = m[(m.disease == "all")].iloc[0]
    assert 0 <= allrow["coverage80_model"] <= 1
    assert np.isfinite(allrow["wis_model"]) and np.isfinite(allrow["relative_wis"])


def test_all_zero_series_gives_zero_forecast():
    c = CASES[(CASES.district_code == "BD1004") & (CASES.disease == "dengue")].assign(cases=0)
    g = F.weekly_grid(c, WEATHER[WEATHER.district_code == "BD1004"], START, END)
    fc = forecast_series(g, [END])
    assert (fc["mean"] == 0).all() and (fc[qcol(0.9)] == 0).all()


# ---------- demand (AN-6) ----------

def test_demand_conversion():
    fc = pd.DataFrame([
        {"district_code": "X", "disease": "dengue", "horizon": h, "mean": 100.0, "q10": 60.0, "q50": 95.0, "q90": 150.0}
        for h in (1, 2, 3, 4)] + [
        {"district_code": "X", "disease": "diarrhoea", "horizon": h, "mean": 200.0, "q10": 150.0, "q50": 195.0, "q90": 260.0}
        for h in (1, 2, 3, 4)])
    shares = {"dengue": 0.2, "diarrhoea": 0.1, "malaria": 0.1}
    dem = D.compute_demand(fc, shares)
    assert len(dem) == 5 * 4 and set(dem["resource"]) == set(D.A.RESOURCES)
    beds = dem[(dem.resource == "beds") & (dem.horizon_weeks == 1)].iloc[0]
    exp = 150 * 0.2 * D.A.LOS_DAYS["dengue"] / 7 + 260 * 0.1 * D.A.LOS_DAYS["diarrhoea"] / 7
    assert beds["demand_q90"] == pytest.approx(exp)
    ns1 = dem[(dem.resource == "ns1_kits") & (dem.horizon_weeks == 2)].iloc[0]
    assert ns1["demand_q90"] == pytest.approx(150 * D.A.CONVERSIONS[("dengue", "ns1_kits")][1])
    assert (dem.loc[dem.resource == "antimalarials", "demand_q90"] == 0).all()   # no malaria forecast given
    plan = D.planning_demand(dem).set_index("resource")["demand"]
    assert plan["beds"] == pytest.approx(exp)                                      # busiest week
    assert plan["ns1_kits"] == pytest.approx(4 * 150 * 1.5)                        # 4-week sum


def test_admission_shares_from_data():
    s = D.admission_shares(CASES)
    assert s["dengue"] == pytest.approx(CASES.query("disease=='dengue'").admissions.sum()
                                        / CASES.query("disease=='dengue'").cases.sum())


# ---------- calendar (AN-9) ----------

def _circ_before(a, b):
    """True if week a comes before week b within half a year (circular)."""
    return 0 < (b - a) % 52 < 26


def test_calendar_dengue_prepositions_before_peak():
    weekly = CASES[CASES.disease == "dengue"].groupby("week_start", as_index=False)["cases"].sum()
    rows, s = C.calendar_for_series(weekly, "dengue")
    assert len(rows) == 52 and rows["epi_week"].tolist() == list(range(1, 53))
    assert (rows["action"] == "preposition").sum() == 1 and (rows["action"] == "campaign").sum() == 1
    prep = int(rows.loc[rows.action == "preposition", "epi_week"].iloc[0])
    assert prep == s["preposition_by_week"]
    assert _circ_before(prep, s["peak_week"])
    assert _circ_before(s["campaign_by_week"], prep)
    assert 36 <= s["peak_week"] <= 46                 # synthetic dengue peaks around ISO week 40-41


def test_calendar_short_history_fallback():
    weekly = CASES[CASES.disease == "dengue"].groupby("week_start", as_index=False)["cases"].sum().head(70)
    rows, s = C.calendar_for_series(weekly, "dengue")
    assert len(rows) == 52 and np.isfinite(rows["seasonal_index"]).all()


def test_calendar_sparse_series_gets_no_actions():
    weeks = pd.date_range("2021-01-04", periods=5 * 52, freq="7D")
    cases = np.where((weeks.isocalendar().week.values % 9) == 0, 1, 0)   # a stray case every ninth week
    rows, s = C.calendar_for_series(pd.DataFrame({"week_start": weeks, "cases": cases}), "malaria")
    assert s["mean_weekly_cases"] < C.MIN_MEAN_WEEKLY_CASES and s["no_seasonality"]
    assert (rows["action"] == "none").all() and s["preposition_by_week"] is None


def test_calendar_check_finds_onset_of_each_year():
    from analytics.calendar import check as K

    rows = []
    for yr, shift in ((2021, 0), (2022, 3)):
        for wk in range(1, 53):
            n = 50 if 20 + shift <= wk <= 35 + shift else 2      # season starts in week 20, then week 23
            rows.append(("BD2003", "dengue", yr, wk, n))
    cases = pd.DataFrame(rows, columns=["district_code", "disease", "epi_year", "epi_week", "cases"])
    cal = pd.DataFrame([{"division_code": "BD20", "disease": "dengue", "onset_week": 20, "no_seasonality": False}])
    got = {r["year"]: r["diff_weeks"] for r in K.yearly_onsets(cases, cal)}
    assert abs(got[2021]) <= 1 and abs(got[2022] - 3) <= 1
    assert K.summarise(K.yearly_onsets(cases, cal))["dengue"]["cells"] == 2


# ---------- Spark (applyInPandas) ----------

@pytest.fixture(scope="module")
def spark():
    os.environ.setdefault("EPISCOPE_DRIVER_MEMORY", "1g")
    os.environ.setdefault("EPISCOPE_SPARK_MASTER", "local[2]")
    for k in ("HADOOP_CONF_DIR", "HADOOP_HOME", "YARN_CONF_DIR"):
        os.environ.pop(k, None)
    from analytics.forecast.io import limit_threads

    limit_threads()
    from pipeline.spark import get_spark

    s = get_spark("test-forecast", memory=os.environ["EPISCOPE_DRIVER_MEMORY"])
    yield s
    s.stop()


def test_spark_forecasts_contract(spark):
    from analytics.forecast.forecast import GOLD_COLS, build_forecasts

    fc = build_forecasts(spark, spark.createDataFrame(CASES), spark.createDataFrame(WEATHER)).toPandas()
    assert set(GOLD_COLS) <= set(fc.columns)
    assert len(fc) == len(DISTRICTS) * 3 * 4
    assert sorted(fc["horizon"].unique()) == [1, 2, 3, 4]
    assert (fc["origin_week_start"] == END.date()).all()
    assert ((fc["q10"] <= fc["q50"]) & (fc["q50"] <= fc["q90"])).all()
    gap = pd.to_datetime(fc["target_week_start"]) - pd.to_datetime(fc["origin_week_start"])
    assert (gap == pd.to_timedelta(7 * fc["horizon"], unit="D")).all()


def test_spark_calendar_contract(spark):
    cal = C.build_calendar(spark.createDataFrame(CASES)).toPandas()
    assert len(cal) == 2 * 3 * 52                     # 2 divisions x 3 diseases x 52 weeks
    assert set(cal["action"]) <= {"preposition", "campaign", "none"}
    per = cal.groupby(["division_code", "disease"])["action"].apply(lambda a: (a == "preposition").sum())
    weekly = CASES.groupby(["division_code", "disease", "week_start"])["cases"].sum()
    sparse = weekly.groupby(["division_code", "disease"]).mean() < C.MIN_MEAN_WEEKLY_CASES   # low-signal rule
    assert sparse.any() and (per == (~sparse).astype(int)).all()
