# Data contracts between components

Every component codes against these interfaces, so the components can be built in parallel. To change a
contract, update this file first in its own commit and tell the team.

## Reference data (produced by `pipeline/reference/`, from S1–S3)

| File | Columns |
|---|---|
| `data/reference/districts.csv` | district_code (HDX ADM2 pcode), district_name, division_code, division_name, lat, lon (centroid), population_2022, area_km2 |
| `data/reference/population_age_sex.csv` | district_code, age_band (0-4, 5-14, 15-24, 25-44, 45-64, 65+), sex (M/F), population |
| `data/reference/weather_daily.parquet` | district_code, date, precip_mm, tmean_c, tmax_c, rh_pct (2021-01-01 to 2025-12-31 at least) |
| `dashboard/data/bd_districts.topo.json` | TopoJSON object `districts`; each feature has `properties.district_code` and `district_name`, simplified to under 300 KB |

## Raw synthetic sources (produced by `generator/`)

See `generator/schema.json`. Ground truth is written only to `ground_truth/outbreaks.csv` and `ground_truth/artefacts.csv` (facility_id, district_code, silent_start, silent_end, backfill_date, type). `data/raw/_manifest.json` holds run metadata (underscore prefix, so Spark ignores it). Encounter IDs embed the seed (`E42-BD3026-20230115-B000012`), so drops with different seeds never collide.

## Epidemiological week

The ISO week is the canonical epi-week. In Spark this is `weekofyear(onset_date)` with
`epi_year = extract(YEAROFWEEK FROM onset_date)`. We use the ISO week rather than MMWR because its
week-year boundary is unambiguous and Spark supports it natively. The report states this choice.

## Gold marts (produced by `pipeline/gold/`, Delta, partitioned by epi_year)

| Table | Grain | Columns |
|---|---|---|
| `gold.cases_weekly` | district × disease × epi_year × epi_week | district_code, division_code, disease (dengue, diarrhoea, ili, malaria), epi_year, epi_week, week_start (date), cases, admissions, deaths, lab_confirmed, reporting_facilities, expected_facilities, batch_ids (array) |
| `gold.weather_weekly` | district × week | district_code, epi_year, epi_week, precip_mm_sum, tmean_c_avg, rh_pct_avg |
| `gold.stock_latest` | district × resource | district_code, resource (beds, iv_saline, ns1_kits, ors, antimalarials), stock, as_of |
| `gold.dq_report` | load | batch_id, source, rows_in, rows_clean, rows_quarantined, rule_pass_rates (map), loaded_at |

## Analytics outputs (produced by `analytics/`)

| Table | Columns |
|---|---|
| `gold.alerts` | district_code, disease, epi_year, epi_week, method (ears_c2, cusum), statistic, threshold, alert (bool) |
| `gold.forecasts` | district_code, disease, origin_week_start, horizon (1-4), target_week_start, mean, q10, q50, q90 |
| `gold.demand` | district_code, resource, horizon_weeks, demand_mean, demand_q90 |
| `gold.allocation` | scenario_id, budget, alpha, district_code, resource, allocation, unmet |
| `gold.calendar` | division_code, disease, epi_week, seasonal_index, action (preposition, campaign, none) |

## Serving (produced by `serving/`, read by `dashboard/`): `dashboard/data/*.json`, at most 10 MB in total

| File | Shape |
|---|---|
| `meta.json` | `{generated_at, data_through, diseases[], suppression_threshold: 5}` |
| `weekly.json` | `[{d: district_code, dz: disease, w: week_start, c: cases or null if suppressed, s: true if suppressed, inc: per100k}]` |
| `alerts.json` | `[{d, dz, w, method, stat, thr, alert}]` |
| `forecasts.json` | `[{d, dz, origin, h, target, mean, q10, q50, q90}]` |
| `allocation.json` | `{scenarios: [{id, budget, alpha}], rows: [{id, d, r, alloc, unmet, demand, stock}]}` |
| `calendar.json` | `[{div, dz, w, idx, action}]` |
| `dq.json` | `[{batch_id, source, rows_in, rows_clean, rows_quarantined, rules: {code: pass_rate}}]` |

Any count from 1 to 4 is shown as `null` with `s: true` (PRV-2). Complementary suppression ensures no hidden
cell can be recovered by subtraction.
