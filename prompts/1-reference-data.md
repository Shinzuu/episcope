<task>
Build pipeline/reference/: code that downloads and prepares the real public data (S1–S3), writing the
reference files in docs/contracts.md "Reference data" plus the dashboard TopoJSON. Run it once, so the
files exist for the other workers.
</task>

<requirements>
1. **S1 boundaries.** Use the HDX COD-AB Bangladesh ADM2 boundaries, which have BBS pcodes. Check the URL
   in research.md §2. Produce districts.csv: centroids, area and division names, with exactly 64 districts
   in 8 divisions. Simplify the geometry with mapshaper (`npx mapshaper` is fine) or shapely to
   `dashboard/data/bd_districts.topo.json`, under 300 KB.
2. **S2 population.** Write district population_2022 and the age-sex bands. Prefer HDX's BBS-derived
   tables. If you scale them to the census-adjusted total, say so in a comment and in your report.
3. **S3 weather.** Pull daily precipitation_sum, temperature_2m_mean, temperature_2m_max and
   relative_humidity_2m_mean for each district centroid from Open-Meteo's archive API, 2021-01-01 to
   2025-12-31.
   - Respect the rate limits in research.md: batch several coordinates per call, throttle, and retry with
     backoff.
   - Cache the raw responses under data/bronze/weather/ with ingestion metadata (ING-1), then write
     weather_daily.parquet.
4. **Re-runs.** A second run skips anything already cached.
5. **Tests** in tests/test_reference.py:
   - 64 districts and 8 divisions;
   - no null centroids;
   - weather has no missing district-days;
   - the TopoJSON's district codes match districts.csv.
</requirements>

<decisions>
Your call: the HTTP library (requests is installed), the cache format, and whether to name a fallback
source in the report if HDX is down.
Ask me before: using any source not listed in the brief or research.md.
</decisions>
