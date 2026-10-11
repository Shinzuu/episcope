"""Tests for pipeline/reference (S1-S3, ING-1).

The file checks run against the built reference data (`python -m pipeline.reference.build`) and skip if it
has not been built yet. The unit tests need no network.
"""

import datetime as dt
import json

import pandas as pd
import pytest

from pipeline.reference import boundaries, weather
from pipeline.reference.common import REFERENCE, cached_ok, write_with_meta

DISTRICTS = REFERENCE / "districts.csv"
AGE_SEX = REFERENCE / "population_age_sex.csv"
WEATHER = REFERENCE / "weather_daily.parquet"


def need(path):
    if not path.exists():
        pytest.skip(f"{path.name} not built; run python -m pipeline.reference.build")


@pytest.fixture(scope="module")
def districts():
    need(DISTRICTS)
    return pd.read_csv(DISTRICTS)


# --- built files -------------------------------------------------------------------------------------

def test_64_districts_in_8_divisions(districts):
    assert len(districts) == 64
    assert districts.district_code.is_unique
    assert districts.division_code.nunique() == 8
    assert districts.groupby("division_code").division_name.nunique().eq(1).all()


def test_districts_columns_match_contract(districts):
    assert list(districts.columns) == ["district_code", "district_name", "division_code", "division_name",
                                       "lat", "lon", "population_2022", "area_km2"]
    assert districts.district_code.str.fullmatch(r"BD\d{4}").all()
    assert (districts.district_code.str[:4] == districts.division_code).all()


def test_no_null_centroids_and_inside_bangladesh(districts):
    assert districts[["lat", "lon"]].notna().all().all()
    assert districts.lat.between(20.5, 26.7).all() and districts.lon.between(88.0, 92.7).all()


def test_population_positive_and_age_sex_consistent(districts):
    assert (districts.population_2022 > 0).all()
    need(AGE_SEX)
    a = pd.read_csv(AGE_SEX)
    assert set(a.age_band) == {"0-4", "5-14", "15-24", "25-44", "45-64", "65+"}
    assert set(a.sex) == {"M", "F"}
    assert len(a) == 64 * 6 * 2
    sums = a.groupby("district_code").population.sum()
    pd.testing.assert_series_equal(sums.sort_index(), districts.set_index("district_code").population_2022
                                   .sort_index(), check_names=False)


def test_weather_has_no_missing_district_days(districts):
    need(WEATHER)
    w = pd.read_parquet(WEATHER)
    assert list(w.columns) == ["district_code", "date", "precip_mm", "tmean_c", "tmax_c", "rh_pct"]
    assert set(w.district_code) == set(districts.district_code)
    dates = pd.to_datetime(w.date)
    n_days = (dates.max() - dates.min()).days + 1
    assert len(w) == 64 * n_days
    assert not w.duplicated(["district_code", "date"]).any()
    assert w[["precip_mm", "tmean_c", "tmax_c", "rh_pct"]].notna().all().all()
    assert dates.min() <= pd.Timestamp("2021-01-01") and dates.max() >= pd.Timestamp("2025-12-31")
    assert (w.precip_mm >= 0).all() and w.rh_pct.between(0, 100).all() and (w.tmax_c >= w.tmean_c).all()


def test_topojson_codes_match_districts(districts):
    need(boundaries.TOPO_OUT)
    assert boundaries.TOPO_OUT.stat().st_size < 300_000
    topo = json.loads(boundaries.TOPO_OUT.read_text())
    geoms = topo["objects"]["districts"]["geometries"]
    assert sorted(g["properties"]["district_code"] for g in geoms) == sorted(districts.district_code)
    names = {g["properties"]["district_code"]: g["properties"]["district_name"] for g in geoms}
    assert names == dict(zip(districts.district_code, districts.district_name))


# --- unit tests, offline -----------------------------------------------------------------------------

def test_year_chunks_cover_window_without_gaps():
    ch = weather.year_chunks("2021-03-05", "2023-02-01")
    assert ch[0][0] == dt.date(2021, 3, 5) and ch[-1][1] == dt.date(2023, 2, 1)
    for (_, e), (s, _) in zip(ch, ch[1:]):
        assert s == e + dt.timedelta(days=1)


def test_call_weight_follows_open_meteo_rule():
    assert weather.call_weight(1, dt.date(2021, 1, 1), dt.date(2021, 1, 7)) == 1
    assert weather.call_weight(4, dt.date(2021, 1, 1), dt.date(2021, 1, 28)) == 8


def test_parse_single_and_multi_location():
    daily = {"time": ["2021-01-01", "2021-01-02"], "precipitation_sum": [0.0, 1.5],
             "temperature_2m_mean": [18.0, 19.0], "temperature_2m_max": [24.0, 25.0],
             "relative_humidity_2m_mean": [70, 71]}
    one = weather.parse(json.dumps({"daily": daily}).encode(), ["BD1004"])
    two = weather.parse(json.dumps([{"daily": daily}, {"location_id": 1, "daily": daily}]).encode(),
                        ["BD1004", "BD1006"])
    assert len(one) == 2 and len(two) == 4
    assert list(two.district_code.unique()) == ["BD1004", "BD1006"]
    with pytest.raises(ValueError):
        weather.parse(json.dumps({"daily": daily}).encode(), ["BD1004", "BD1006"])


def test_cache_skip_and_checksum(tmp_path):
    p = tmp_path / "x.json"
    write_with_meta(p, b"[1]", district_codes=["BD1004"])
    assert cached_ok(p, {"district_codes": ["BD1004"]})
    assert not cached_ok(p, {"district_codes": ["BD1006"]})
    p.write_bytes(b"[2]")  # tampered: checksum no longer matches, so it would be re-fetched
    assert not cached_ok(p)


def test_fetch_skips_cached_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "CACHE_DIR", tmp_path)
    d = pd.DataFrame({"district_code": ["BD1004", "BD1006"], "lat": [22.1, 22.8], "lon": [90.1, 90.4]})
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        n = len(params["latitude"].split(","))
        days = pd.date_range(params["start_date"], params["end_date"]).strftime("%Y-%m-%d").tolist()
        daily = {"time": days, **{v: [1.0] * len(days) for v in weather.VARIABLES}}
        body = [{"daily": daily}] * n

        class R:
            status_code, content = 200, json.dumps(body).encode()
        return R()

    monkeypatch.setattr(weather, "http_get", fake_get)
    paths = weather.fetch(d, "2021-12-30", "2022-01-02", batch_size=2, sleep=lambda s: None)
    assert len(calls) == 2  # two year chunks, one batch each
    weather.fetch(d, "2021-12-30", "2022-01-02", batch_size=2, sleep=lambda s: None)
    assert len(calls) == 2  # second run: everything cached
    df = weather.build_parquet(paths, out=tmp_path / "w.parquet")
    assert len(df) == 2 * 4
