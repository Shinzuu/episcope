"""Reference inputs for the generator (S1-S3 products, docs/contracts.md) and the test fixture.

Implements ING-1: the synthetic encounters are driven by real denominators (districts.csv,
population_age_sex.csv) and real weather (weather_daily.parquet).

If weather_daily.parquet is missing (it is produced by pipeline/reference/), a synthetic monsoon climate is
used for the loaded districts and a warning is printed. `python -m generator.reference --fixture DIR` writes
an 8-district fixture (real P-codes, approximate figures, synthetic weather) used by the tests.
"""

import argparse
import pathlib
import sys
import zlib

import numpy as np
import pandas as pd

from generator import config as C

FIXTURE_DISTRICTS = [
    # code, name, division_code, division_name, lat, lon, population, area_km2 (approximate)
    ("BD3026", "Dhaka", "BD30", "Dhaka", 23.79, 90.25, 18_100_000, 1470),
    ("BD2015", "Chattogram", "BD20", "Chattogram", 22.50, 91.90, 9_200_000, 5280),
    ("BD2003", "Bandarban", "BD20", "Chattogram", 21.80, 92.37, 500_000, 4590),
    ("BD2084", "Rangamati", "BD20", "Chattogram", 22.83, 92.28, 680_000, 5640),
    ("BD2022", "Cox's Bazar", "BD20", "Chattogram", 21.48, 92.06, 2_890_000, 2330),
    ("BD6091", "Sylhet", "BD60", "Sylhet", 24.90, 91.87, 4_000_000, 3490),
    ("BD4047", "Khulna", "BD40", "Khulna", 22.60, 89.45, 2_600_000, 4390),
    ("BD5081", "Rajshahi", "BD50", "Rajshahi", 24.45, 88.60, 2_900_000, 2410),
]
AGE_SHARES = np.array([0.090, 0.190, 0.175, 0.300, 0.175, 0.070])


def crc(text):
    return zlib.crc32(str(text).encode())


def norm_name(name):
    return "".join(ch for ch in str(name).lower() if ch.isalpha())


def matches(code, name, spec):
    codes, names = spec
    return code in codes or norm_name(name) in names


# ----------------------------------------------------------------------------------------------- weather

def synthetic_weather(districts, first, last):
    """Plausible daily monsoon weather per district: wet Jun-Sep, hot Apr-Aug, heavy-tailed rain."""
    days = np.arange(np.datetime64(first, "D"), np.datetime64(last, "D") + 1)
    doy = (days - days.astype("datetime64[Y]")).astype(int) + 1
    monsoon = np.exp(2.5 * (np.cos(2 * np.pi * (doy - 200) / 365.25) - 1))
    frames = []
    for row in districts.itertuples():
        rng = np.random.default_rng([C.STRUCT_SEED, C.STREAM_STRUCT, crc(row.district_code), 99])
        east = 1 + 0.3 * np.clip((row.lon - 90.0) / 2.0, -1, 1)
        wet = rng.random(days.size) < (0.05 + 0.75 * monsoon)
        amount = rng.gamma(0.8, (4 + 22 * monsoon) * east / 0.8)
        precip = np.where(wet, amount, 0.0)
        noise = np.zeros(days.size)
        eps = rng.normal(0, 0.45, days.size)
        for i in range(1, days.size):
            noise[i] = 0.9 * noise[i - 1] + eps[i]
        tmean = 25 - 6 * np.cos(2 * np.pi * (doy - 20) / 365.25) - 0.5 * (row.lat - 23.0) + noise
        tmax = tmean + 4 + 2 * (1 - monsoon) + rng.normal(0, 0.6, days.size)
        rh = np.clip(65 + 22 * monsoon + rng.normal(0, 4, days.size), 30, 100)
        frames.append(pd.DataFrame({
            "district_code": row.district_code, "date": days,
            "precip_mm": precip.round(1), "tmean_c": tmean.round(1), "tmax_c": tmax.round(1),
            "rh_pct": rh.round(1),
        }))
    return pd.concat(frames, ignore_index=True)


class Weather:
    """Daily weather lookup with day-of-year climatology for dates outside the file's range."""

    def __init__(self, df, codes):
        df = df.copy()
        df["day"] = pd.to_datetime(df["date"]).values.astype("datetime64[D]").astype(np.int64)
        df["doy"] = pd.to_datetime(df["date"]).dt.dayofyear.clip(upper=365)
        self.series = {}
        national = self._climatology(df)
        for code in codes:
            sub = df[df.district_code == code].sort_values("day")
            if sub.empty:
                self.series[code] = (0, np.zeros((0, 3)), national)
                continue
            lo = int(sub.day.iloc[0])
            full = np.full((int(sub.day.iloc[-1]) - lo + 1, 3), np.nan)
            full[sub.day.values - lo] = sub[["precip_mm", "tmean_c", "rh_pct"]].values
            clim = self._climatology(sub)
            gaps = np.isnan(full)
            if gaps.any():
                doys = _doy(np.arange(lo, lo + len(full)))
                full[gaps] = clim[doys - 1][gaps]
            self.series[code] = (lo, full, clim)

    @staticmethod
    def _climatology(df):
        g = df.groupby("doy")[["precip_mm", "tmean_c", "rh_pct"]].mean().reindex(range(1, 366))
        g = g.interpolate(limit_direction="both")
        vals = g.values
        # circular 15-day smoothing
        pad = np.vstack([vals[-7:], vals, vals[:7]])
        kernel = np.ones(15) / 15
        sm = np.column_stack([np.convolve(pad[:, j], kernel, mode="valid") for j in range(3)])
        return np.vstack([sm, sm[-1:]])  # 366 rows

    def get(self, code, lo, hi):
        """(precip, tmean, rh) arrays for int days lo..hi inclusive."""
        start, full, clim = self.series[code]
        days = np.arange(lo, hi + 1)
        out = clim[_doy(days) - 1].copy()
        idx = days - start
        ok = (idx >= 0) & (idx < len(full))
        out[ok] = full[idx[ok]]
        return out[:, 0], out[:, 1], out[:, 2]


def _doy(days):
    d = np.asarray(days).astype("datetime64[D]")
    return (d - d.astype("datetime64[Y]")).astype(int) + 1


# ------------------------------------------------------------------------------------------------ loading

def load(ref_dir):
    """Return (districts DataFrame, age-sex DataFrame, weather DataFrame, notes list)."""
    ref = pathlib.Path(ref_dir)
    notes = []
    dpath, ppath, wpath = ref / "districts.csv", ref / "population_age_sex.csv", ref / "weather_daily.parquet"
    if not dpath.exists():
        raise FileNotFoundError(f"{dpath} not found. Build reference data first (make reference), or pass "
                                f"--reference tests/fixtures/reference (create it with "
                                f"python -m generator.reference --fixture tests/fixtures/reference)")
    districts = pd.read_csv(dpath, dtype={"district_code": str, "division_code": str})
    districts = districts.sort_values("district_code").reset_index(drop=True)
    if ppath.exists():
        pop = pd.read_csv(ppath, dtype={"district_code": str})
    else:
        notes.append(f"{ppath} missing: using a standard age-sex pyramid")
        pop = standard_pyramid(districts)
    if wpath.exists():
        weather = pd.read_parquet(wpath)
    else:
        notes.append(f"{wpath} missing: using a synthetic monsoon climate (replace when S3 lands)")
        weather = synthetic_weather(districts, "2021-01-01", "2025-12-31")
    return districts, pop, weather, notes


def standard_pyramid(districts):
    rows = []
    for row in districts.itertuples():
        for sex, share in (("F", 0.505), ("M", 0.495)):
            for band, a in zip(C.AGE_BANDS, AGE_SHARES):
                rows.append((row.district_code, band, sex, int(round(row.population_2022 * share * a))))
    return pd.DataFrame(rows, columns=["district_code", "age_band", "sex", "population"])


def write_fixture(out_dir, first="2021-01-01", last="2025-12-31"):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    districts = pd.DataFrame(FIXTURE_DISTRICTS, columns=[
        "district_code", "district_name", "division_code", "division_name", "lat", "lon",
        "population_2022", "area_km2"])
    districts.to_csv(out / "districts.csv", index=False)
    standard_pyramid(districts).to_csv(out / "population_age_sex.csv", index=False)
    synthetic_weather(districts, first, last).to_parquet(out / "weather_daily.parquet", index=False)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Write the 8-district generator test fixture")
    p.add_argument("--fixture", required=True, help="output directory, e.g. tests/fixtures/reference")
    a = p.parse_args(argv)
    print(f"fixture written to {write_fixture(a.fixture)}", file=sys.stderr)


if __name__ == "__main__":
    main()
