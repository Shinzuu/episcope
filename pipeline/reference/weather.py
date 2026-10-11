"""S3 weather: daily Open-Meteo ERA5 reanalysis at every district centroid, cached raw in bronze.

Implements S3 and ING-1 (each raw API response is stored byte-for-byte under data/bronze/weather/ with a
metadata sidecar: request params, district codes in response order, fetch time, SHA-256). Feeds
gold.weather_weekly and the weather-lagged forecast covariates (AN-4).
Source: Open-Meteo Historical Weather API, CC BY 4.0. Cite: Zippenfenig, P. (2023). Open-Meteo.com Weather
API. Zenodo. ERA5: Hersbach et al. (2020), Copernicus Climate Change Service.

Model choice: `models=era5` pinned so every year comes from the same reanalysis. ERA5-Land (finer grid)
was tested first but Open-Meteo returns null precipitation_sum for it over Bangladesh (checked 2026-10-11),
so it cannot be used. ERA5 is a 0.25 degree grid, so a few small neighbouring districts share a cell.

Rate limits (free tier: 5,000 weighted calls/hour, 10,000/day). One location over more than 14 days counts
as days/14 calls, and a multi-location request is weighted per location. Requests are batched several
centroids per call and split by calendar year; after every live request the client sleeps long enough to
stay under CALLS_PER_HOUR. 429s are retried (minute/hour limits) or abort cleanly (daily limit), and
because finished chunks are cached, a re-run resumes where it stopped and never re-fetches.
"""

import datetime as dt
import json
import os
import pathlib
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .common import BRONZE, REFERENCE, cached_ok, http_get, write_with_meta

API = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "open-meteo"
MODEL = "era5"
TIMEZONE = "Asia/Dhaka"
# API variable -> contract column (docs/contracts.md, weather_daily.parquet)
VARIABLES = {"precipitation_sum": "precip_mm", "temperature_2m_mean": "tmean_c",
             "temperature_2m_max": "tmax_c", "relative_humidity_2m_mean": "rh_pct"}
# Requested window: the task's reference period. Override with --start/--end or these env vars when the
# synthetic data (or a hidden drop) runs past it; new calendar-year chunks are fetched, old ones reused.
DEFAULT_START = os.environ.get("EPISCOPE_WEATHER_START", "2021-01-01")
DEFAULT_END = os.environ.get("EPISCOPE_WEATHER_END", "2025-12-31")
BATCH_SIZE = int(os.environ.get("EPISCOPE_OPENMETEO_BATCH", "4"))
CALLS_PER_HOUR = float(os.environ.get("EPISCOPE_OPENMETEO_CALLS_PER_HOUR", "4500"))  # cap is 5,000
CACHE_DIR = BRONZE / "weather" / f"source={SOURCE}" / f"model={MODEL}"
OUT = REFERENCE / "weather_daily.parquet"


def year_chunks(start, end):
    """Split [start, end] into calendar-year pieces: [(date, date), ...]."""
    s, e = dt.date.fromisoformat(str(start)), dt.date.fromisoformat(str(end))
    if e < s:
        raise ValueError(f"end {e} before start {s}")
    out = []
    while s <= e:
        ce = min(dt.date(s.year, 12, 31), e)
        out.append((s, ce))
        s = ce + dt.timedelta(days=1)
    return out


def batches(codes, size):
    codes = sorted(codes)
    return [codes[i:i + size] for i in range(0, len(codes), size)]


def call_weight(n_locations, start, end, n_vars=len(VARIABLES)):
    """Open-Meteo's weighting: per location, max(1, days/14) x max(1, variables/10)."""
    days = (end - start).days + 1
    return n_locations * max(1.0, days / 14) * max(1.0, n_vars / 10)


def cache_path(start, end, codes):
    return CACHE_DIR / f"chunk={start}_{end}" / f"batch={codes[0]}_{codes[-1]}.json"


def _on_429(r):
    text = r.text.lower()
    if "daily" in text:
        raise RuntimeError("Open-Meteo daily limit reached; cached chunks are kept, re-run tomorrow to resume")
    if "minutely" in text or "minute" in text:
        return 65
    return 600  # hourly limit: wait 10 min, http_get retries up to its try count


def fetch(districts, start=DEFAULT_START, end=DEFAULT_END, batch_size=BATCH_SIZE,
          calls_per_hour=CALLS_PER_HOUR, sleep=time.sleep):
    """Fetch every (year chunk, centroid batch) not already cached. Returns the list of cache paths.

    `districts` is a DataFrame with district_code, lat, lon.
    """
    coords = districts.set_index("district_code")[["lat", "lon"]]
    plan = [(cs, ce, b) for cs, ce in year_chunks(start, end) for b in batches(coords.index, batch_size)]
    paths, live = [], 0
    for i, (cs, ce, codes) in enumerate(plan, 1):
        path = cache_path(cs, ce, codes)
        sub = coords.loc[codes]
        params = {
            "latitude": ",".join(f"{v:.6f}" for v in sub.lat),
            "longitude": ",".join(f"{v:.6f}" for v in sub.lon),
            "start_date": cs.isoformat(), "end_date": ce.isoformat(),
            "daily": ",".join(VARIABLES), "timezone": TIMEZONE, "models": MODEL,
        }
        paths.append(path)
        if cached_ok(path, {"district_codes": list(codes), "params": params}):
            continue
        weight = call_weight(len(codes), cs, ce)
        t0 = time.monotonic()
        print(f"[{i}/{len(plan)}] {cs}..{ce} {codes[0]}..{codes[-1]} (~{weight:.0f} weighted calls)", flush=True)
        r = http_get(API, params=params, on_429=_on_429)
        parse(r.content, codes)  # validate before caching, so a bad body is never cached
        write_with_meta(path, r.content, source=SOURCE, url=API, params=params, district_codes=list(codes),
                        model=MODEL, chunk_start=cs.isoformat(), chunk_end=ce.isoformat(),
                        http_status=r.status_code)
        live += 1
        if i < len(plan):
            pause = weight * 3600 / calls_per_hour - (time.monotonic() - t0)
            if pause > 0:
                sleep(pause)
    print(f"weather: {len(plan)} chunks, {live} fetched, {len(plan) - live} cached", flush=True)
    return paths


def parse(raw, codes):
    """Raw response bytes -> long DataFrame. The API returns a list (one object per location, in request
    order) for several coordinates and a bare object for one, so rows are keyed by position."""
    body = json.loads(raw)
    objs = body if isinstance(body, list) else [body]
    if len(objs) != len(codes):
        raise ValueError(f"expected {len(codes)} locations, got {len(objs)}")
    frames = []
    for code, o in zip(codes, objs):
        d = o["daily"]
        f = pd.DataFrame({"date": pd.to_datetime(d["time"]).date})
        for api_name, col in VARIABLES.items():
            f[col] = pd.to_numeric(pd.Series(d[api_name], dtype="object"), errors="coerce").astype("float64")
        f.insert(0, "district_code", code)
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def build_parquet(paths, out=OUT):
    """Combine cached chunks into weather_daily.parquet and check it is complete (no missing district-day,
    no nulls)."""
    frames = []
    for p in paths:
        meta = json.loads(pathlib.Path(str(p) + ".meta.json").read_text())
        frames.append(parse(pathlib.Path(p).read_bytes(), meta["district_codes"]))
    df = (pd.concat(frames, ignore_index=True).drop_duplicates(["district_code", "date"])
          .sort_values(["district_code", "date"]).reset_index(drop=True))
    n_days = (df.date.max() - df.date.min()).days + 1
    expect = df.district_code.nunique() * n_days
    if len(df) != expect:
        raise ValueError(f"weather has {len(df)} district-days, expected {expect}")
    nulls = df[list(VARIABLES.values())].isna().sum()
    if nulls.any():
        raise ValueError(f"weather has null values: {nulls[nulls > 0].to_dict()}")
    schema = pa.schema([("district_code", pa.string()), ("date", pa.date32()), ("precip_mm", pa.float64()),
                        ("tmean_c", pa.float64()), ("tmax_c", pa.float64()), ("rh_pct", pa.float64())])
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, schema=schema, preserve_index=False), out)
    print(f"wrote {out} ({len(df)} rows, {df.date.min()}..{df.date.max()})", flush=True)
    return df
