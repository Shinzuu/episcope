"""Build all reference data from public sources S1-S3: `python -m pipeline.reference.build`.

Implements S1, S2, S3 and ING-1 (raw downloads cached in bronze with ingestion metadata); writes the files
listed under "Reference data" in docs/contracts.md:

    data/reference/districts.csv            S1 geography + S2 totals, 64 rows
    data/reference/population_age_sex.csv   S2, 64 x 6 bands x 2 sexes
    data/reference/weather_daily.parquet    S3, every district-day in the window
    dashboard/data/bd_districts.topo.json   S1 simplified for the map (VIS-1)

Re-runs are cheap: downloads and weather chunks already in bronze (checksum verified) are skipped, the
TopoJSON is rebuilt only if missing or out of step with districts.csv, and the small derived CSV/parquet
files are regenerated deterministically from bronze. Uses pandas, not Spark: these are small reference
tables (the brief allows pandas for that).
"""

import argparse

from . import boundaries, population, weather
from .common import REFERENCE

COLUMNS = ["district_code", "district_name", "division_code", "division_name", "lat", "lon",
           "population_2022", "area_km2"]


def build_districts():
    geo = boundaries.read_adm2(boundaries.fetch())
    dist = boundaries.districts_frame(geo)
    raw = population.read_raw(population.fetch())
    tot = population.district_totals(raw)
    missing = set(dist.district_code) ^ set(tot.district_code)
    if missing:
        raise ValueError(f"P-codes not in both boundaries and population: {sorted(missing)}")
    dist = dist.merge(tot, on="district_code", how="inner")[COLUMNS]
    REFERENCE.mkdir(parents=True, exist_ok=True)
    dist.to_csv(REFERENCE / "districts.csv", index=False)
    population.age_sex(raw).to_csv(REFERENCE / "population_age_sex.csv", index=False)
    print(f"wrote districts.csv ({len(dist)} districts, {dist.division_code.nunique()} divisions, "
          f"population {dist.population_2022.sum():,}) and population_age_sex.csv", flush=True)
    return geo, dist


def build_topo(geo, dist, force=False):
    out = boundaries.TOPO_OUT
    if not force and out.exists():
        try:
            if sorted(boundaries.topo_codes(out)) == sorted(dist.district_code):
                print(f"cached  {out.name}", flush=True)
                return out
        except (ValueError, KeyError):
            pass
    out = boundaries.build_topojson(geo)
    print(f"wrote {out} ({out.stat().st_size / 1000:.0f} KB)", flush=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default=weather.DEFAULT_START, help="weather start date (ISO)")
    ap.add_argument("--end", default=weather.DEFAULT_END, help="weather end date (ISO)")
    ap.add_argument("--batch-size", type=int, default=weather.BATCH_SIZE, help="centroids per API call")
    ap.add_argument("--skip-weather", action="store_true", help="only boundaries, population, TopoJSON")
    ap.add_argument("--force-topo", action="store_true", help="rebuild the TopoJSON even if present")
    a = ap.parse_args(argv)
    geo, dist = build_districts()
    build_topo(geo, dist, force=a.force_topo)
    if not a.skip_weather:
        paths = weather.fetch(dist, a.start, a.end, a.batch_size)
        weather.build_parquet(paths)


if __name__ == "__main__":
    main()
