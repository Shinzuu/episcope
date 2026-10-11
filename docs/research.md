# EpiScope: technical and factual research notes

Researched 2026-10-11 against primary sources (official docs, specs, source code, original papers, live first-party APIs). Requirement IDs refer to the CEP brief (`source/CEP_EpiScope.pdf`). **UNVERIFIED** marks anything I could not confirm at a primary source. Version numbers are as of the research date; re-check before you freeze `requirements.txt`.

---

## 0. Decision summary

| Topic | Recommendation | Why |
|---|---|---|
| Spark + table format | `pyspark==4.0.1`, `delta-spark==4.0.1`, Java 17, Python 3.11/3.12 | This is the exact documented Delta↔Spark pair. Spark 3.5 caps numpy below 2 (§6, §7). |
| Boundaries | HDX COD-AB v03 (BBS-sourced, P-codes, centroids) | geoBoundaries has no codes and uses pre-2018 spellings (§2.1) |
| Population | HDX `cod-ps-bgd` ADM2 CSV, joined on `ADM2_PCODE` | 5-year age bands by sex for all 64 districts in one CSV (§2.2) |
| Weather | Open-Meteo archive, throttled to about 1 location/40 s, cached in bronze | 64 locations × 5 years is about 8.4k weighted calls, over the 5k/hour cap (§2.3) |
| Detection | EARS C2 (main, scalable) + CUSUM on NB-GLM residuals (second method) | Farrington needs b years of history, and the data has only 5 (§3) |
| Forecast | Per-district NB2 GLM with weather lagged ≥4 weeks, run in `applyInPandas` | Interpretable. With lags ≥ horizon, no weather forecast is needed (§4) |
| Epi-week | ISO 8601 (`weekofyear` + `extract(YEAROFWEEK …)`) | Native in Spark and unambiguous. MMWR formula given if the team wants CDC weeks (§5) |
| Generator | numpy kernel, seeded per (district, year) key, run over a small task grid | Deterministic whatever the partitioning. Spark `rand()` is documented as non-deterministic (§8) |
| Hashing | HMAC-SHA256 built from native `sha2` + precomputed ipad/opad keys | Spark SQL has no HMAC function, and a Python UDF over 25M rows is slow (§9) |
| LP | `pulp==3.3.2` (bundled CBC) or HiGHS via `highspy` | PuLP 4.0 (2026-09-25) removed bundled CBC and `PULP_CBC_CMD` (§10) |
| Orchestration | Makefile entry point + Prefect 3 flow (retries) or a plain-Python DAG | Airflow-in-Docker needs 4–8 GB and has an Arch-specific memory bug (§14) |
| Object store | SeaweedFS (S3) or `apache/hadoop` HDFS, not MinIO | The MinIO community repo is archived and ships source only (§14) |

---

## 1. Bangladesh facts the brief asserts (Background §1, S7)

### 1.1 The 2023 dengue season
**Brief:** "more than 300,000 hospitalised cases and over 1,700 deaths".
- **Year-end DGHS figure: 321,179 hospitalisations and 1,705 deaths (CFR 0.53%).** This is widely reported as the DGHS total, but I could not reach the DGHS press release itself, so the exact primary URL is **UNVERIFIED**.
- WHO DON498 (21 Dec 2023), through November 2023: **308,167 cases and 1,598 deaths (CFR 0.52%)**, against 62,382 cases and 281 deaths in 2022. https://www.who.int/emergencies/disease-outbreak-news/item/2023-DON498
- WHO DON481 (to 7 Aug 2023): 69,483 confirmed cases and 327 deaths. Cases rose from May. 63% of cases and 62% of deaths fell in July. DENV-2 became dominant (51.5% of June samples). Dhaka city had 52.8% of cases and 78.9% of deaths. All 64 districts were affected. https://www.who.int/emergencies/disease-outbreak-news/item/2023-DON481
- Independent check: OpenDengue V1.3 (WHO-sourced row `WHO-ALL-2025-Y01-00`) sums **2023 = 321,138** across monthly rows. I verified this from the CSV myself.
- WHO Bangladesh issued 12 sitreps (28 Aug – 13 Nov 2023). https://www.who.int/bangladesh/emergencies/dengue-update-2023

**Verdict:** the brief's claim is consistent with the sources. Cite WHO DON498 for the November figure and DGHS for the year-end total.

### 1.2 Seasonality
| Disease | Primary-source finding | Generator implication |
|---|---|---|
| Dengue | DON481 says cases historically "coincide with the rainy season from May to September". **But OpenDengue monthly national counts (computed by me) peak later:** 2019 Aug; 2021 Aug; **2022 Oct**; 2023 Aug; **2024 Oct**. Pooled 2015–2024 monthly shares: Jul 10.2%, Aug 24.8%, Sep 21.1%, Oct 20.4%, Nov 16.4%. | Peak Aug–Oct with a long post-monsoon tail into November. Do not stop the season in September. |
| Cholera/AWD | Two annual epidemics, pre-monsoon and post-monsoon (Emch et al. 2010, https://doi.org/10.1186/1476-069x-9-2). Nationwide 22-site surveillance 2014–2018: 6.2% of diarrhoea patients were V. cholerae O1 positive (Khan et al. 2020, https://doi.org/10.1093/cid/ciz1075). | Bimodal: about Mar–May and Sep–Nov, plus post-flood spikes driven by extreme rainfall. |
| Influenza/ILI | "A distinct influenza peak during the rainy season (May–September)", 12-hospital sentinel network (Zaman et al. 2009, https://doi.org/10.1371/journal.pone.0008452) | Unimodal May–Sep. |
| Malaria | Endemic in 13 of 64 districts. >85% of cases in May–Oct in the CHT study area (Ahmed et al. 2013, https://doi.org/10.1371/journal.pone.0069713). The often-quoted figure that the CHT plus Cox's Bazar carry >90% of cases comes from the same literature (exact NMEP primary **UNVERIFIED**). | Concentrate cases in Bandarban, Rangamati, Khagrachhari and Cox's Bazar. Near zero elsewhere, and those near-zero districts create the small cells you must suppress (§9). |

**Pitfall that contradicts the brief's framing:** "Dengue follows the monsoon" is now too simple. Since 2022 the peak has moved to October–November. A preparedness calendar built only on May–Sep will pre-position too early.

### 1.3 Districts, divisions, codes
HDX COD-AB v03 (source BBS, compiled by OCHA ROAP, reviewed 1 Jun 2025): **8 divisions, 64 districts, 507 ADM3 units (495 upazilas + 12 city corporations)**. https://data.humdata.org/dataset/cod-ab-bgd (CKAN API: `https://data.humdata.org/api/3/action/package_show?id=cod-ab-bgd`)

The P-code pattern is `BD` + 2-digit division + 2-digit district. The digits match BBS geocodes (Dhaka zila 26, Chattogram 15). That they are exactly the BBS GEO codes is inferred from the BBS-sourced dataset, not from a BBS code list, so treat it as **UNVERIFIED**. The current official spellings below are what the HDX xlsx sheet `bgd_admin2` contains, as I extracted it:

- **Barishal BD10:** Barguna 1004, Barishal 1006, Bhola 1009, Jhalokati 1042, Patuakhali 1078, Pirojpur 1079
- **Chattogram BD20:** Bandarban 2003, Brahmanbaria 2012, Chandpur 2013, Chattogram 2015, Cumilla 2019, Cox's Bazar 2022, Feni 2030, Khagrachhari 2046, Lakshmipur 2051, Noakhali 2075, Rangamati 2084
- **Dhaka BD30:** Dhaka 3026, Faridpur 3029, Gazipur 3033, Gopalganj 3035, Kishoreganj 3048, Madaripur 3054, Manikganj 3056, Munshiganj 3059, Narayanganj 3067, Narsingdi 3068, Rajbari 3082, Shariatpur 3086, Tangail 3093
- **Khulna BD40:** Bagerhat 4001, Chuadanga 4018, Jashore 4041, Jhenaidah 4044, Khulna 4047, Kushtia 4050, Magura 4055, Meherpur 4057, Narail 4065, Satkhira 4087
- **Mymensingh BD45:** Jamalpur 4539, Mymensingh 4561, Netrakona 4572, Sherpur 4589
- **Rajshahi BD50:** Bogura 5010, Joypurhat 5038, Naogaon 5064, Natore 5069, Chapainababganj 5070, Pabna 5076, Rajshahi 5081, Sirajganj 5088
- **Rangpur BD55:** Dinajpur 5527, Gaibandha 5532, Kurigram 5549, Lalmonirhat 5552, Nilphamari 5573, Panchagarh 5577, Rangpur 5585, Thakurgaon 5594
- **Sylhet BD60:** Habiganj 6036, Moulvibazar 6058, Sunamganj 6090, Sylhet 6091

**Pitfall:** older files spell names differently. geoBoundaries and the HDX population CSV use Barisal, Chittagong, Comilla, Jessore, Bogra, Nawabganj, Brahamanbaria and Maulvibazar. **Join on P-code, never on name.** Keep a `district_alias` reference table in silver.

---

## 2. Public data (S1–S3, S7; ING-1)

### 2.1 Boundaries (S1)
**geoBoundaries gbOpen BGD** (API `https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM2/`):
- ADM2: **64 units**. Licence **CC BY 3.0 IGO**. Source "BBS, OCHA ROAP", build 12 Dec 2023.
- Full GeoJSON is 46.5 MB: https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/BGD/ADM2/geoBoundaries-BGD-ADM2.geojson
- Simplified GeoJSON is 1.69 MB: `…/geoBoundaries-BGD-ADM2_simplified.geojson`. A TopoJSON is at `…/geoBoundaries-BGD-ADM2.topojson`.
- ADM3: **544 units**, which does not match HDX's 507. https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM3/
- Fields: `shapeName`, `shapeISO` (empty), `shapeID`, `shapeGroup`, `shapeType`. **There are no BBS codes**, and the names are old spellings.

**HDX COD-AB** (licence CC BY-IGO). Resources:
- GeoJSON zip: `https://data.humdata.org/dataset/401d3fae-4262-48c9-891f-461fd776d49b/resource/cec2abe3-d8b7-4025-9362-9f7e780f2a07/download/bgd_admin_boundaries.geojson.zip`
- Shapefile zip: `…/resource/05d85fe2-40c0-4b57-b1b5-be9c36a592fb/download/bgd_admin_boundaries.shp.zip`
- XLSX: `…/resource/5f7e534d-9b06-442e-a706-083789bff2c9/download/bgd_admin_boundaries.xlsx`
- Fields: `adm2_name`, `adm2_pcode`, `adm1_name`, `adm1_pcode`, `area_sqkm`, **`center_lat`, `center_lon`**, `version`, `valid_on`.

**Recommendation:** use HDX COD-AB v03 as the master geography. Its centroids can drive the Open-Meteo queries directly, and its P-codes link to the population table. Note in the report which version you used. Both sources are attribution licences, so credit "BBS / OCHA ROAP" on the dashboard.

### 2.2 Population (S2)
- **HDX `cod-ps-bgd`** ("Bangladesh – Subnational Population Statistics", reference year 2022, sources UNFPA and US Census Bureau, methodology "Downloaded from BBS", CC BY-IGO). ADM2 CSV: `https://data.humdata.org/dataset/fdf0606c-8a3b-421a-b3e8-903301e5b2ff/resource/43bfa9fd-f571-4973-9f31-91093e1e6142/download/bgd_admpop_adm2_2022.csv`. Contents (verified):
  - 64 rows, columns `ADM2_PCODE`, `F_TL`/`M_TL`/`T_TL`, then `{F,M,T}_00_04 … _75_79, _80Plus` (17 bands)
  - Total **165,650,475**
  - Dhaka 18,113,283 and Bandarban 497,397
  - ADM3 and ADM1 CSVs exist alongside it.
- **Pitfall:** the final adjusted Census 2022 total reported by BBS is 169,828,911 (Census 2022 National Report Vol. I, Nov 2023, as reported by the press; primary PDF **UNVERIFIED**). The HDX table is about 2.5% lower, presumably preliminary-based. Say in the report which you use. Incidence per 100,000 shifts by that factor.
- **BBS District Series reports** (PDF per district, e.g. `https://pcs.icms.gov.bd/Data/PHC2022/DistrictSeries/Report/Bandarban.pdf`) contain the age-sex tables, but only as PDFs, and the host did not resolve from here. **UNVERIFIED** that it is reachable. Fall back to HDX.
- **WorldPop** (CC BY 4.0): REST `https://hub.worldpop.org/rest/data/age_structures/aswpgp?iso3=BGD` lists 100 m age/sex GeoTIFFs for 2000–2020, e.g. `https://data.worldpop.org/GIS/AgeSex_structures/Global_2000_2020/2020/BGD/bgd_f_20_2020.tif`. Newer 2024 releases are listed as `G2_UC_Age_2024_100m` / `G2_CN_Age_2024_100m`. You would need zonal statistics (rasterio) to get district totals: about 36 rasters × ADM2 polygons. Use WorldPop only if you need upazila-level denominators the census table lacks.

### 2.3 Weather: Open-Meteo historical archive (S3)
- Endpoint: `https://archive-api.open-meteo.com/v1/archive`. Required parameters: `latitude`, `longitude`, `start_date`, `end_date` (ISO dates). https://open-meteo.com/en/docs/historical-weather-api
- **I tested daily variables live:** `precipitation_sum`, `temperature_2m_mean`, `temperature_2m_max/min`, `relative_humidity_2m_mean`, `relative_humidity_2m_max` all return data for Bangladesh. Use `timezone=Asia/Dhaka` (UTC+6).
- **Multiple coordinates per call are supported** as comma-separated lists, e.g. `latitude=23.79,22.45&longitude=90.25,91.82`. The JSON response is an array, one object per location. The first object omitted `location_id` in my test and the second had `location_id: 1`, so key by array index.
- Models: ERA5 (0.25°, ~25 km), ERA5-Land (0.1°, ~11 km), both with a **5-day delay**. ECMWF IFS (9 km, 2017+) has no delay. **Pin `models=era5_land` (or `era5`)** so that 2021 and 2025 come from the same model. The default `best_match` blends sources.
- **Free tier (terms):** <10,000 calls/day, 5,000/hour, 600/minute; 300,000/month; non-commercial only. Public-institution education counts as non-commercial. Licence **CC BY 4.0** with attribution. https://open-meteo.com/en/terms and https://open-meteo.com/en/pricing
- **Weighting:** "Requests for data covering more than 10 weather variables or extending over a period of more than 2 weeks for a single location are considered multiple API calls." For example, 4 weeks counts as 3.0. https://open-meteo.com/en/pricing
  - **Back-of-envelope:** one location × 1,826 days (2021–2025) ≈ 130 weighted calls. × 64 districts ≈ **8,350 calls**. That is under the daily cap but **over the hourly cap if fired in a burst**. A multi-location request is still weighted per location.
  - **Recommendation:** fetch one location per request with about 0.7 s between requests per year-chunk, or about 40 s per 5-year location (about 45 min in total). Retry on HTTP 429 with backoff. Write raw JSON to `bronze/weather/source=open-meteo/…` with a checksum, and **never re-fetch in `make all`** if the cache exists.
- Citation required: "Zippenfenig, P. (2023). Open-Meteo.com Weather API. Zenodo."

### 2.4 OpenDengue (S7)
- Version 1.3, **CC BY 4.0**. Downloads: `https://github.com/OpenDengue/master-repo/raw/main/data/releases/V1.3/{National,Spatial,Temporal}_extract_V1_3.zip`. Cite Clarke et al. 2024 (https://doi.org/10.1038/s41597-024-03120-7) plus figshare https://doi.org/10.6084/m9.figshare.24259573. https://opendengue.org/data.html
- **Bangladesh coverage (verified from the CSV):**
  - Annual national rows for 1980–2010
  - **Monthly national (`S_res=Admin0`, `T_res=Month`) from 2008-01 to 2025-03**
  - **No sub-national rows.** The Spatial extract has the same 234 rows.
- Columns: `adm_0_name`, `adm_1_name`, `adm_2_name`, `ISO_A0`, `FAO_GAUL_code`, `calendar_start_date`, `calendar_end_date`, `Year`, `dengue_total`, `case_definition_standardised`, `S_res`, `T_res`, `UUID`.
- **Use:** validate only the *national monthly shape* of synthetic dengue (correlate synthetic national monthly totals with OpenDengue 2021–2024). You cannot validate the district split with it.

---

## 3. Surveillance methods (AN-1, AN-2, AN-3)

### 3.1 EARS C1/C2/C3
Sources: Hutwagner et al. 2003, J Urban Health 80(Suppl 1), https://doi.org/10.1007/pl00022319 (full text not retrievable, metadata only). Zhu et al. 2005, MMWR Suppl, https://www.cdc.gov/mmwr/preview/mmwrhtml/su5401a20.htm. R `surveillance` 1.26.1 `earsC.R`, https://github.com/cran/surveillance/blob/master/R/earsC.R (CRAN mirror).

Let ȳ and s be the mean and SD of the baseline window, and B=7.
- **C1:** baseline t−7…t−1 (no guard band). C1(t) = (y_t − ȳ₁)/s₁. Alarm if C1 > 3 (EARS). The R default uses α=0.001, so the threshold is z = qnorm(0.999) = 3.09.
- **C2:** baseline **t−9…t−3** (2-period guard band, so an outbreak's first days do not contaminate the baseline). C2(t) = (y_t − ȳ₂)/s₂. Alarm if > 3.
  - Zhu 2005 describes this as "a gap of g = 3 days". The R code uses lags (B+2):3, i.e. t−3…t−9. Follow the code and say so.
- **C3:** C3(t) = Σ_{i=t−2}^{t} max(0, C2(i) − 1). Alarm if > 2. In R: α=0.025, z≈1.96.
- R's `minSigma` floors s, which avoids division by zero in sparse series. You need this for malaria outside the CHT. Use something like minSigma=1, or 0.5×√ȳ.
- **Pitfalls:**
  1. EARS was designed for *daily* counts with no seasonal adjustment. Zhu notes the "mean µ does not adjust for seasonality". On weekly data a 7-week baseline lags the seasonal upswing and fires every season onset. Either run on daily onset-date counts per district×disease, or on residuals after removing the seasonal expectation.
  2. Day-of-week effects (Bangladesh weekend Fri–Sat, §8.3) inflate s. Use 7-day sums, or deseasonalise first.
- **Spark implementation (AN-1) using window functions:**
```python
from pyspark.sql import Window, functions as F
w = Window.partitionBy("district_pcode","disease").orderBy("onset_date")
b = w.rowsBetween(-9, -3)                      # C2 baseline
df = (daily.withColumn("mu", F.avg("n").over(b))
           .withColumn("sd", F.greatest(F.stddev_samp("n").over(b), F.lit(1.0)))
           .withColumn("c2", (F.col("n")-F.col("mu"))/F.col("sd"))
           .withColumn("c3", F.sum(F.greatest(F.col("c2")-1, F.lit(0))).over(w.rowsBetween(-2,0)))
           .withColumn("alarm_c2", F.col("c2") > F.lit(thr)))
```
  This needs a dense calendar (cross-join district × disease × all dates, left-join counts, fill 0). Otherwise `rowsBetween` counts rows, not days.

### 3.2 CUSUM
Page 1954, Biometrika 41:100, https://doi.org/10.1093/biomet/41.1-2.100. One-sided upper CUSUM on standardised residuals z_t = (y_t − μ̂_t)/σ̂_t:

S_t = max(0, S_{t−1} + z_t − k). Alarm when S_t > h, then reset S_t = 0.

Common design: k = 0.5 (tuned to detect a 1σ shift) and h = 4–5. Calibrate h on outbreak-free synthetic years to a target false-alert rate rather than quoting textbook ARL values. Fricker, Hegler & Dunfee 2008 found CUSUM on model-based prediction residuals "performed significantly better than the EARS' methods across all of the scenarios" (Stat Med 27:3407, https://doi.org/10.1002/sim.3197).
- **Recommendation for AN-3:** use EARS C2 as the simple method. Use CUSUM on NB-GLM residuals (μ̂ from the §4 forecast model's one-step-ahead mean) as the second. Reset after an alarm. CUSUM is sequential, so implement it per group in `applyInPandas`, not with window functions.

### 3.3 Farrington and Farrington Flexible (Noufaily 2013)
Farrington et al. 1996, JRSS A 159(3):547, https://doi.org/10.2307/2983331. Noufaily et al. 2013, Stat Med 32(7):1206, https://doi.org/10.1002/sim.5595.

Defaults from `farringtonFlexible.R` and the help page (https://github.com/cran/surveillance/blob/master/man/farringtonFlexible.Rd):

| Parameter | Original (1996) | Improved (Noufaily) |
|---|---|---|
| `b` (years back) | 5 | 5 |
| `w` (half-window, weeks) | 3 | 3 |
| `noPeriods` | 1 | **10** (seasonal factor, uses all past weeks) |
| `weightsThreshold` (Anscombe residual reweighting) | 1 | **2.58** |
| `pThresholdTrend` | 0.05 | **1** (keep trend only if the other criteria are met) |
| `pastWeeksNotIncluded` | = w | **26** |
| `thresholdMethod` | "delta" (2/3-power transform) | **"nbPlugin"** (NB quantile) or "muan" |
| `alpha` | 0.05 (2-sided originally) | one-sided upper quantile |
| `limit54` | no alarm if <5 cases in the last 4 weeks | same |

The model is a quasi-Poisson GLM, log link, with optional trend. Past outbreaks are down-weighted by Anscombe residuals. The threshold is the upper (1−α) quantile of the predictive NB.

- **Pitfall that contradicts the brief's method menu:** with b=5 you need **5 full years of history before the first evaluable week**, and the synthetic data covers only 2021–2025. Even b=3 leaves just 2024–2025 to evaluate, and seasonal reference windows are thin.
- **Recommendation:** do not make Farrington the main method. If the team implements it, use b=3, noPeriods=10, pastWeeksNotIncluded=26, and evaluate on 2024–2025 only. Optionally generate 2016–2020 "warm-up history" (it would not count toward the 25M rows) and say why.
- `limit54` is a useful rule for every method: it suppresses alarms in near-empty series.

### 3.4 Evaluating detection (AN-2, conflict C1)
The CDC framework (Buehler et al. 2004, MMWR RR-53(5), https://www.cdc.gov/mmwr/preview/mmwrhtml/rr5305a1.htm) evaluates detection systems on **timeliness**, **sensitivity** and **predictive value positive / negative**.

Operational definitions to state in the report:
- **Event-level sensitivity** = outbreaks with ≥1 alarm within [start, end + 7 d] ÷ injected outbreaks (per district×disease, from `ground_truth_outbreaks`).
- **False alerts per district-year** = alarm-days (or alarm episodes, with consecutive alarms merged) outside any outbreak window ± buffer ÷ (64 × years). Count episodes, not days, or C3's persistence inflates the number.
- **Timeliness** = median (first alarm date − outbreak start). Missed outbreaks are censored at the end. Report the median and the proportion detected within 7 and 14 days.
- **Trade-off curve:** sweep the threshold (C2: 1.5–4.0 in 0.25 steps; CUSUM h: 2–8). Plot sensitivity against false alerts per district-year (an AMOC/ROC-style curve), annotate median delay, and pick the operating point, e.g. ≤2 false alerts per district-year at maximum sensitivity.
- **Leakage guard:** the detector code must never read `ground_truth_outbreaks`, which the brief requires. Put the file outside the pipeline's read paths and load it only in `analytics/evaluation/`.

### 3.5 Reporting delay and nowcasting
- Detection on **report_date** counts reacts to delivery artefacts. Detection on **onset_date** counts under-counts the most recent 21 days (right truncation).
- Nowcasting methods: Höhle & an der Heiden 2014, Biometrics, https://doi.org/10.1111/biom.12194 (`surveillance::nowcast`); McGough et al. 2020, NobBS (https://doi.org/10.32614/cran.package.nobbs).
- **Minimal approach that fits the project:** estimate the empirical delay CDF F(d) from the last N weeks of silver (reporting triangle). Nowcast N̂_t = N_t^{obs} / F(T − t) for the last 21 days, capped when F < 0.3. Run detection on the nowcast, and show the uncertainty band.
- **Artefact defence (AN-3):** compute facility reporting completeness. Flag a backfill when a facility submits more than 50% of a batch with delay >14 days after ≥14 silent days. Exclude flagged records from report-date detectors, or route detection through the onset-date nowcast.

---

## 4. Forecasting and evaluation (AN-4, AN-5, AN-6)

### 4.1 Weighted interval score (WIS)
Bracher, Ray, Gneiting & Reich 2021, PLoS Comput Biol 17(2):e1008618, https://doi.org/10.1371/journal.pcbi.1008618 (formulas checked against the PMC full text).

**Interval score** for a central (1−α) PI [l, u], where l and u are the α/2 and 1−α/2 quantiles:

IS_α(F, y) = (u − l) + (2/α)(l − y)·1(y < l) + (2/α)(y − u)·1(y > u)

**WIS** for K intervals plus the median m:

WIS_{α_{0:K}}(F, y) = 1/(K + ½) × ( w₀·|y − m| + Σ_{k=1}^{K} w_k·IS_{α_k}(F, y) )

Use w_k = α_k/2 and w₀ = ½. With this weighting WIS ≈ CRPS, and with K=0 it is the absolute error. The COVID-19 Forecast Hub used K=11: α = 0.02, 0.05, 0.1, 0.2, …, 0.9.
```python
def wis(y, med, lo, hi, alphas):          # lo/hi arrays aligned with alphas
    s = 0.5*abs(y-med)
    for a, l, u in zip(alphas, lo, hi):
        s += (a/2)*((u-l) + (2/a)*max(l-y,0) + (2/a)*max(y-u,0))
    return s/(len(alphas)+0.5)
```
Report WIS decomposed into dispersion, over-prediction and under-prediction, and the **relative WIS** against seasonal naive.

### 4.2 80% PI coverage (AN-5)
Coverage = share of (district, week, horizon) where q₀.₁ ≤ y ≤ q₀.₉. The target is 0.80. Report it per horizon h = 1…4. A well-calibrated model is close to 0.8 at every h, and under-coverage usually grows with h.

### 4.3 Rolling-origin backtest
Hyndman & Athanasopoulos, FPP3 §5.10, "evaluation on a rolling forecasting origin", modified for multi-step errors. https://otexts.com/fpp3/tscv.html
- Origins T = each of the last 52 weeks. Fit on data ≤ T **as known at T** (Delta `timestampAsOf`, §7, honours reporting delay: ING-7 plus AN-5). Forecast T+1…T+4.
- Expanding window by default. The number of tasks is 64 districts × 2 diseases × 52 origins = **6,656 fits**, which suits `applyInPandas` grouped by (district, disease, origin).

### 4.4 Seasonal-naive baseline
FPP3: ŷ_{T+h|T} = y_{T+h−m(k+1)}, with k = ⌊(h−1)/m⌋. https://otexts.com/fpp3/simple-methods.html. For weekly data m = 52, so for h ≤ 4 the forecast is ŷ = y_{T+h−52}.
- **Pitfall:** 53-week years. ISO 2020 and 2026 have 53 weeks, MMWR 2020 and 2025 have 53 (§5). Map week 53 to the same-week-of-last-year by date (t−364 days) rather than by week number.
- Baseline intervals: use empirical quantiles of the historical seasonal-naive errors at each h, so that WIS and coverage are comparable.

### 4.5 Model options inside `applyInPandas`
**Primary: negative-binomial (NB2) GLM per district×disease** (statsmodels 0.15.0, latest; 0.14.x if you are on numpy<2):

log μ_t = β₀ + Fourier(week, K=2–3) + β_r·rain_{t−ℓ_r} + β_T·temp_{t−ℓ_T} + β_H·RH_{t−ℓ_H} + β_y·log(y_{t−h}+1) + offset(log pop)

Var(y) = μ + αμ².
- Use `statsmodels.discrete.discrete_model.NegativeBinomial(loglike_method='nb2')`, which **estimates α**. Docs: "nb2: Variance equal to μ + αμ²". https://www.statsmodels.org/stable/generated/statsmodels.discrete.discrete_model.NegativeBinomial.html
- **Pitfall:** `sm.GLM(..., family=NegativeBinomial())` **fixes α = 1** ("The default value is 1 … assumed to be nonstochastic"). https://www.statsmodels.org/stable/generated/statsmodels.genmod.families.family.NegativeBinomial.html. Either estimate α first, or use the discrete model.
- PI quantiles: scipy `nbinom.ppf(q, n=1/α, p=1/(1+αμ))` (https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.nbinom.html). This plug-in interval ignores parameter uncertainty, so expect some under-coverage at h=4. Report it honestly, or inflate α slightly.
- **Key design insight:** if every weather lag ℓ ≥ 4 weeks, then 1–4-week forecasts use only *observed* weather. You need no weather forecast, and Open-Meteo's 5-day archive delay does not matter.

**Climate-lag evidence (synthetic driver and model lags):**
- Bangladesh 2000–2022, monthly Poisson: mean temperature IRR 1.26; rainfall lag-1 month IRR 1.08 and lag-2 months IRR 1.17. That is about 4–9 weeks, consistent with the brief's 4–8 weeks. Hasan et al. 2024, J Med Entomol, https://doi.org/10.1093/jme/tjae001
- India 2010–2017 weekly, DLNM: precipitation risk highest at **8–15 weeks** lag; temperature at 0–3 weeks. Kakarla et al. 2019, Epidemiol Infect, https://doi.org/10.1017/s0950268819000608
- **Recommendation:** rainfall lag 6–8 weeks and temperature lag 4–6 weeks, selected by backtest WIS from {4, 6, 8}. AWD: an extreme-rain indicator (e.g. weekly precip > 95th percentile) at lag 1–2 weeks. That lag is shorter than h=4, so for h > lag set the covariate from the latest observed week, or drop it.

**Prophet** (1.5.0, Stan backend): default `interval_width` is 0.80, which handily matches AN-5. https://facebook.github.io/prophet/docs/uncertainty_intervals.html
- It is too slow for 6,656 backtest fits (each Stan fit takes seconds; **UNVERIFIED** timing).
- It assumes Gaussian noise, which fits low counts poorly.
- It adds a heavy dependency.
- Use it only as an optional comparator on a subset.

**LightGBM** (4.7.0): `objective='quantile'` with `alpha` (default 0.9). https://lightgbm.readthedocs.io/en/stable/Parameters.html
- Best as **one global model across districts**, trained with lag features, one model per quantile.
- Not a per-district fit, and harder to explain (conflict C5).
- Good "accuracy vs interpretability" foil.

**`applyInPandas` notes:** it "requires a full shuffle. All the data of a group will be loaded into memory" and `maxRecordsPerBatch` does not apply to groups. https://spark.apache.org/docs/4.0.2/api/python/reference/pyspark.sql/api/pyspark.sql.GroupedData.applyInPandas.html
- Groups here are about 261 weekly rows, so memory is trivial.
- Set `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` before creating the session, so statsmodels does not oversubscribe the cores Spark already uses.
- Catch convergence failures and fall back to seasonal naive with a flag column.

### 4.6 Demand conversion (AN-6)
Beds_t = Σ_{lag} cases_{t−lag} × P(admit) × LOS-overlap. Propagate the forecast quantiles through monotone conversions: apply them to q₀.₁, q₀.₅ and q₀.₉ directly. State every conversion rate as an assumption, e.g. dengue admission share and LOS. The literature values are **UNVERIFIED** here and must be chosen and cited by the team.

---

## 5. Epi-week (ING-3 partitioning, AN-9)

- **CDC MMWR week:** "The first day of any MMWR week is Sunday … MMWR week #1 … is the first week of the year that has at least four days in the calendar year". It ranges 1–53, and 29–31 Dec can fall in week 1. https://ndc.services.cdc.gov/wp-content/uploads/MMWR_Week_overview.pdf
- **ISO 8601:** weeks start Monday, and week 1 is "the first week with more than 3 days". Spark `weekofyear` implements this. https://spark.apache.org/docs/4.0.2/api/python/reference/pyspark.sql/api/pyspark.sql.functions.weekofyear.html. Spark SQL `extract(YEAROFWEEK FROM d)` gives the ISO week-year (e.g. 2005-01-02 → 2004). https://spark.apache.org/docs/4.0.2/api/sql/index.html
- WHO / DGHS convention for Bangladesh: **UNVERIFIED**. I found no primary source stating which week definition DGHS/IEDCR uses.
- Differences I verified by computation:
  - 2023-01-01 (Sun) is MMWR 2023-W1 but ISO 2022-W52.
  - 2025-12-31 is MMWR 2025-W53 but ISO 2026-W1.
  - In 2021–2025, MMWR 2025 has 53 weeks. In ISO, 2020 and 2026 have 53.
- **Recommendation:** use ISO, because it is native, testable and documented. Always store **both** `epi_year` (ISO week-year, *not* `year(date)`) and `epi_week`, and add `week_start_date` (a Monday) as the join key for the dashboard. If the team prefers MMWR, use this formula (it matches the CDC rule on edge dates):
```python
ws   = F.date_sub("d", F.dayofweek("d") - 1)                # Sunday start (dayofweek: Sun=1)
yr   = F.year(F.date_add(ws, 3))                             # year of the week's Wednesday
jan4 = F.make_date(yr, F.lit(1), F.lit(4))
w1   = F.date_sub(jan4, F.dayofweek(jan4) - 1)
mmwr_week = (F.datediff(ws, w1) / 7).cast("int") + 1
```
- **Pitfall:** partitioning by `year(onset_date)` while aggregating by ISO week splits week 1/52 across partitions. Partition by `epi_year`.

---

## 6. Spark specifics (ING-2, ING-3, ING-6, ENG-2, ENG-3)

### 6.1 Version pinning
| Spark | Java | Python | numpy (pip extra) | pandas | Latest patch |
|---|---|---|---|---|---|
| 3.5.x | 8/11/17 | 3.8+ | **<2** | ≥1.0.5 | 3.5.9 (2026-07-16) |
| 4.0.x | **17/21** | 3.9+ | ≥1.21 | ≥2.0.0 | 4.0.4 (2026-07-15) |
| 4.1.x | 17/21 | 3.10+ | ≥1.21 | ≥2.2.0 | 4.1.3 |
| 4.2.0 | 17/21 | 3.10+ | ≥1.21 | ≥2.2.0 | 4.2.0 (2026-07-14) |

Sources: https://spark.apache.org/docs/3.5.8/, https://spark.apache.org/docs/4.0.2/, and PyPI metadata (https://pypi.org/project/pyspark/).

**Recommendation: `pyspark==4.0.1` + `delta-spark==4.0.1`** (Delta 4.0.x is built for Spark 4.0.1, §7), on OpenJDK 17 and Python 3.11 or 3.12.
- Pin `pandas` to 2.2.x/2.3.x. pandas 3.0 is out (3.0.6), but its compatibility with PySpark 4.0 is **UNVERIFIED**.
- Pin `pyarrow` and `numpy` to versions you smoke-test, and commit a lock file.
- Spark 4.0 bundles Hadoop 3.4.1, so `hadoop-aws` must be 3.4.1 for s3a (Spark `pom.xml` at v4.0.2). Spark 3.5 bundles Hadoop 3.3.4.

### 6.2 `applyInPandas`
See §4.5. Signature: `df.groupBy(keys).applyInPandas(func, schema="district string, …")`, where `func(pdf) -> pdf`. Use it for CUSUM, the NB-GLM fits, STL and nowcasting. Enable `spark.sql.execution.arrow.pyspark.enabled=true`. https://spark.apache.org/docs/4.0.2/api/python/tutorial/sql/arrow_pandas.html

### 6.3 Window functions
`Window.partitionBy(...).orderBy(...).rowsBetween(start, end)` (row-based) or `rangeBetween` (value-based; needs a numeric order key such as `datediff` days). https://spark.apache.org/docs/4.0.2/api/python/reference/pyspark.sql/api/pyspark.sql.Window.html
- Uses: EARS baselines (§3.1), and dedupe with `row_number()` over (encounter_id ORDER BY load_time DESC) for the about 2% resubmissions.
- Cross-facility patient dedupe (DQ-4): partition by hashed patient_id, order by onset date, `lag` within 7 days.

### 6.4 Structured Streaming, file source (ING-6)
Docs: https://spark.apache.org/docs/4.0.2/streaming/apis-on-dataframes-and-datasets.html
- **File source options:** `maxFilesPerTrigger` or `maxBytesPerTrigger` (not both), `latestFirst`, `fileNameOnly`, `maxFileAge`, and `cleanSource` = archive|delete|off (archive needs `sourceArchiveDir`).
- **Schema inference is off for streaming file sources.** Supply the schema (good: it enforces the schema contract).
- **Triggers:**
  - default micro-batch
  - `processingTime='1 minute'` (fixed interval)
  - `availableNow=True` (process everything present in possibly multiple batches, then stop). This replaces the deprecated `once`.
  - continuous (experimental)
- **Recommendation:**
  - The live demo uses `trigger(processingTime="1 minute")` + `foreachBatch(merge_into_silver_and_recompute_alerts)`, which comfortably meets the "within 5 minutes" requirement.
  - The hidden test drop and `make all` use the same query with `availableNow=True`, so one code path serves both.
  - Use a checkpoint per query. `spark.sql.shuffle.partitions` cannot change between restarts from the same checkpoint.

### 6.5 AQE, skew join and salting (ENG-3)
https://spark.apache.org/docs/4.0.2/sql-performance-tuning.html
- `spark.sql.adaptive.enabled=true` is the default (since 3.2). Coalescing post-shuffle partitions uses `advisoryPartitionSizeInBytes` (64 MB).
- `spark.sql.adaptive.skewJoin.enabled=true`. A partition counts as skewed if it is larger than `skewedPartitionFactor` (5.0) × median **and** larger than `skewedPartitionThresholdInBytes` (**256 MB**). It applies only to **sort-merge joins**.
- **Pitfall:** at SF=1 no partition will reach 256 MB, so AQE skew handling will never fire and the "before/after" will show nothing. For the experiment, lower it to 16–32 MB and set advisory to 8–16 MB, or show salting instead.
- Dhaka skew: Dhaka district is 11% of the population (18.1M of 165.7M) and 52.8% of early-2023 dengue cases (DON481). That skews joins and windows keyed on district.
  - **Salting:** add `salt = pmod(hash(encounter_id), N)` to the large side, explode the small side × N, and join on (district, salt).
  - Aggregations are already map-side partially aggregated, so salting rarely helps a `groupBy`. Demonstrate it on a join, or on `applyInPandas` at facility level where Dhaka's groups are largest.

### 6.6 Broadcast joins
`spark.sql.autoBroadcastJoinThreshold` defaults to 10 MB. The `BROADCAST` hint overrides it. The 600-row facility registry auto-broadcasts already, so the "before" run must set the threshold to −1 to show a sort-merge join. Show the Spark UI SQL tab (BroadcastHashJoin vs SortMergeJoin, plus shuffle bytes).

### 6.7 `partitionBy` and partition pruning
- Silver encounters: `partitionBy("epi_year", "division_pcode")` gives about 5 × 8 = 40 directories, at roughly 100–600 MB each. That is justified because queries filter by time range and region.
- **Avoid** district × day partitions (64 × 1,826 ≈ 117k directories, small-file disaster).
- Gold marts are small: partition by `epi_year`, or not at all.
- Verify pruning with `df.explain()`: look for `PartitionFilters: [...]`.
- Dynamic partition pruning (`spark.sql.optimizer.dynamicPartitionPruning.enabled`) is on by default.
- For late-data MERGE, add the partition columns to the merge condition so only the touched epi-years are rewritten (§7).

### 6.8 Catalog in local mode (ING-3)
- Without Hive support the catalog is in-memory, so table registrations vanish when the session ends.
- `enableHiveSupport()` creates an embedded Derby `metastore_db` in the CWD and a warehouse at `spark.sql.warehouse.dir` (default `$PWD/spark-warehouse`). https://spark.apache.org/docs/4.0.2/sql-data-sources-hive-tables.html
- **Pitfall:** embedded Derby allows **one process** at a time. A streaming job and a batch job in two processes will fail to boot the metastore (standard Derby behaviour; **UNVERIFIED** at a Spark doc).
  - Run everything in one long-lived session, or
  - use path-based Delta tables, and re-register them idempotently at start (`CREATE TABLE IF NOT EXISTS silver.encounters USING DELTA LOCATION '...'`).
- Pin `spark.sql.warehouse.dir` to an absolute path, so that runs from different directories do not create several warehouses.

### 6.9 Memory for a 16 GB laptop
- In local mode the executors run inside the driver JVM.
- `spark.driver.memory` defaults to 1g. It must be set before the JVM starts: `--driver-memory`, `spark-defaults.conf`, or the session builder *before the first* `getOrCreate()` in a fresh Python process.
- Unified memory = `spark.memory.fraction` (0.6) × (heap − 300 MB). https://spark.apache.org/docs/4.0.2/configuration.html and https://spark.apache.org/docs/4.0.2/tuning.html
- **Suggested:**

| Setting | Value |
|---|---|
| `master` | `local[*]` (or `local[N-1]`) |
| `spark.driver.memory` | `6g` (leaves about 6 GB for Python workers, the OS and a browser) |
| `spark.sql.shuffle.partitions` | 16–32, not 200; AQE coalesces anyway |
| `spark.local.dir` | an SSD path with ≥20 GB free |
| `spark.sql.adaptive.coalescePartitions.parallelismFirst` | `false` (fewer tiny files) |
| `spark.driver.maxResultSize` | 1g default; never `collect()` line-level data |

### 6.10 Is 25M rows / 5 years at SF=1 feasible in ≤45 min? (ENG-2)
Back-of-envelope only. **UNVERIFIED until measured**, and the team must measure it.
- Data size: 25M rows × about 130 bytes is about 3.3 GB as CSV. As Parquet/snappy it is about 0.6–1 GB.
- Generation with vectorised numpy (counts first, then `np.repeat` to rows, written via pyarrow CSV/Parquet): about 3–8 min.
- Bronze ingest (CSV parse on 4–8 cores at tens of MB/s per core): about 2–4 min.
- Silver: native HMAC, 12+ DQ rules as column expressions, dedupe window, broadcast join: about 4–8 min.
- Gold weekly marts: 64 × 4 diseases × about 261 weeks ≈ 67k rows, about 1–2 min.
- Detection on daily or weekly aggregates: under 1 min.
- Backtest: 6,656 NB fits × about 50 ms ÷ 4 cores ≈ 1.5–3 min.
- **Total ≈ 15–25 min**, so it is feasible.
- What breaks it:
  - row-wise Python UDFs (HMAC per row)
  - Python loops in the generator
  - 200 shuffle partitions × fine `partitionBy`
  - gzip CSV (not splittable)
  - re-fetching weather inside the timed run
  - Delta MERGE over the whole table instead of pruned partitions

---

## 7. Delta Lake (ING-4, ING-7)

**Compatibility.** The docs table (https://docs.delta.io/latest/releases.html) gives:

| Delta | Spark |
|---|---|
| 4.3.x / 4.2.x / 4.1.x | 4.0.x, 4.1.x |
| 4.0.x | 4.0.x |
| 3.3.x … 3.0.x | 3.5.x |
| 2.4.x | 3.4.x |

GitHub releases (https://github.com/delta-io/delta/releases) are newer: **Delta 4.4.x supports Spark 4.2.0/4.1.0/4.0.1** with per-Spark artifacts `delta-spark_4.2_2.13`, `delta-spark_4.1_2.13` and `delta-spark_4.0_2.13`. The plain `delta-spark_2.13` defaults to the newest Spark.

PyPI pins:

| delta-spark | pyspark pin |
|---|---|
| 4.4.x | ≥4.0.1, ≤4.2.0 |
| 4.2.x–4.3.x | ≥4.0.1, ≤4.1.1 (4.1.3 is rejected by pip) |
| 4.0.x | ≥4.0.0 |
| 3.3.x | ≥3.5.3, <3.6.0 |

**Pitfall:** with Delta ≥4.1, `configure_spark_with_delta_pip` and a mismatched default artifact can pull a JAR for the wrong Spark minor. The simple, documented pair is **pyspark 4.0.1 + delta-spark 4.0.1**, or 3.5.x + 3.3.2.

**Session setup** (https://docs.delta.io/latest/quick-start.html):
```python
from delta import configure_spark_with_delta_pip
builder = (SparkSession.builder.appName("episcope")
  .config("spark.sql.extensions","io.delta.sql.DeltaSparkSessionExtension")
  .config("spark.sql.catalog.spark_catalog","org.apache.spark.sql.delta.catalog.DeltaCatalog"))
spark = configure_spark_with_delta_pip(builder).getOrCreate()
```

**Idempotent MERGE for late data (ING-4)** (https://docs.delta.io/latest/delta-update.html):
```sql
MERGE INTO silver.encounters t
USING (SELECT * FROM batch_dedup) s          -- one row per encounter_id (latest load)
ON  t.encounter_id = s.encounter_id
AND t.epi_year IN (2025, 2026)               -- prune: only years touched by this batch
WHEN MATCHED AND s.record_hash <> t.record_hash THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```
- "A merge operation can fail if multiple rows of the source dataset match … the same rows of the target". Deduplicate the source first, which also removes the about 2% resubmitted encounters.
- With a deterministic `record_hash`, re-running a day changes nothing. That gives idempotency, which the video should demonstrate as zero rows updated.
- Recompute gold only for the epi-weeks touched: `SELECT DISTINCT epi_year, epi_week FROM batch`, then a `replaceWhere` overwrite of those gold partitions.
- In streaming `foreachBatch`, writes can be made idempotent with `txnAppId` + `txnVersion` (= batchId). If you delete the checkpoint you must change `txnAppId`. https://docs.delta.io/latest/delta-streaming.html

**Time travel (ING-7)** (https://docs.delta.io/latest/delta-batch.html):
- Use `spark.read.format("delta").option("versionAsOf", v)` or `.option("timestampAsOf", "2025-06-01")`, or SQL `VERSION AS OF` / `TIMESTAMP AS OF`.
- Defaults: `delta.logRetentionDuration` = 30 days and `delta.deletedFileRetentionDuration` = 7 days. **Pitfall:** the backtest's "as known on date t" needs history back 52 weeks. Commits happen in wall-clock time, not simulated time, so the timestamps won't match simulated dates anyway.
- **Recommendation:** replay the 2025 loads day by day in simulated order, and record a `(sim_date → delta_version)` map in a gold `load_log` table. Use `versionAsOf` from the map. Set log retention ≥ 365 days and never VACUUM during the project.
- A cheaper alternative for the backtest is to filter silver on `report_date ≤ t` (bitemporal), which reproduces the same "what we knew" without time travel. Use time travel to demonstrate ING-7.

**Iceberg (brief comparison):** Iceberg 1.11/1.12 ships runtime JARs per Spark minor (`iceberg-spark-runtime-4.1_2.13:1.12.0`; Maven latest for 4.0 is 1.11.0). Time travel uses `TIMESTAMP AS OF` / `VERSION AS OF <snapshot-id|branch|tag>`. https://iceberg.apache.org/docs/latest/spark-getting-started/ and https://iceberg.apache.org/docs/latest/spark-queries/
- Iceberg needs an explicit catalog config (hadoop/hive/rest).
- Delta has the simpler pip path and is the one cited in the brief (Armbrust et al. 2020, https://doi.org/10.14778/3415478.3415560).
- **Choose Delta.**

---

## 8. Generator realism (S4–S6, ING-1)

### 8.1 Negative binomial in numpy
`Generator.negative_binomial(n, p)` counts failures before n successes, with n > 0 real-valued allowed, and P(N) = Γ(N+n)/(N!Γ(n)) pⁿ(1−p)ᴺ. https://numpy.org/doc/stable/reference/random/generated/numpy.random.Generator.negative_binomial.html
- To get mean μ and NB2 dispersion α (Var = μ + αμ²): **n = 1/α (size), p = n/(n+μ) = 1/(1+αμ)**.
- Equivalent Poisson–gamma: λ ~ Gamma(shape=n, scale=μ/n), then y ~ Poisson(λ).
- Choose α per disease: about 0.1–0.3 for large city dengue, higher for sparse malaria. Check that the synthetic weekly variance/mean ratio exceeds 1.
- Expected intensity per district d, disease k, day t:

λ_{d,k,t} = pop_d × base_k × exp(A_{d,k}·seasonal_k(doy) + β_k·climate_{d,t−ℓ}) × outbreak_{d,k,t} × dow_t × holiday_t

### 8.2 Deterministic seeding at scale
- numpy's documented pattern: "combining the ID and the root seed integer in a list", e.g. `default_rng([worker_id, root_seed])`, gives reproducible independent streams. https://numpy.org/doc/stable/reference/random/parallel.html
- Spark `rand(seed)`: "The function is non-deterministic in general case". Results depend on partitioning and task order. https://spark.apache.org/docs/4.0.2/api/python/reference/pyspark.sql/api/pyspark.sql.functions.rand.html
- **Recommendation:** generate with a **numpy kernel per (district, year)** using `rng = default_rng([SEED, district_idx, year, STREAM_ID])`, with separate STREAM_IDs for counts, delays and attributes. This makes output identical whatever the parallelism. Drive the 64 × 5 = 320 tasks with `multiprocessing` or a Spark `mapInPandas` over the task grid (either is fine; say why).
- Encounter IDs must be deterministic too: `f"E{year}{district}{seq:09d}"`. Sort within each file.
- SF scales `base_k` linearly. At SF=1, aim for 25M+ rows **including** background ICD codes.

### 8.3 Reporting delay and weekend effects
- Delay: discretised NB or lognormal truncated to 0–21 days, right-skewed. For example NB(mean 3, size 1.5) gives P(0) ≈ 0.19, P(>14) ≈ 0.6% and P(>21) ≈ 0.04% (computed). Draw it and renormalise after truncation.
- **Bangladesh's weekly holiday is Friday and Saturday** (government two-day weekend since 2009; press source https://www.dhakatribune.com/bangladesh/362231/26-public-holidays-approved-for-2025; Cabinet Division primary **UNVERIFIED**). It is **not** Saturday–Sunday. Move a share (about 60–70%) of Fri/Sat report dates to the next Sunday.
- Holidays: 2025 had 26 public holidays, including 5-day Eid-ul-Fitr and 6-day Eid-ul-Azha blocks (same source). Eid moves about 11 days earlier each year. The `holidays` Python package has a `BD` calendar (**UNVERIFIED** coverage of Eid dates); otherwise hard-code from Cabinet Division lists.
- Keep onset seasonality on onset_date, and the weekday dip on report_date only.

### 8.4 Injected outbreaks and artefacts
- Multiply λ by 1 + A·g(t−t₀), where g is a shape kernel:
  - gamma-shaped pulse for short outbreaks (duration 2–6 weeks)
  - logistic ramp for the **slow-growing** one (doubling about 3–4 weeks)
  - SEIR curve for the **nationwide novel respiratory surge** (J-codes): seeded in Dhaka, with district start delays proportional to distance from Dhaka
- Sizes: A from 0.5× to 5× baseline. At least 30 events across diseases and districts, including some in small districts (hard to detect) and some overlapping seasonal peaks (hard to separate).
- Write `ground_truth_outbreaks.csv` (event_id, district_pcode, disease, start, end, shape, peak_multiplier, type) **outside** the pipeline input tree.
- **Silent-then-backfill artefact:** choose a high-volume facility F and weeks w..w+2. Hold back all of F's reports for that period, then emit them in week w+3 with report_date = w+3 (delays up to 21 days). Report-date detectors see a dip, then a spike. Record it as `type='artefact'` in a separate ground-truth file so AN-3 can show it was *not* alerted on.
- Quality defects at the brief's rates:
  - about 2% duplicate `encounter_id`
  - about 0.5% orphan `facility_id`
  - about 1% invalid age/sex
  - some `onset_date > report_date`
  - invalid ICD-10 codes

---

## 9. Privacy (PRV-1 … PRV-4)

### 9.1 Salted hashing (PRV-1)
- Use HMAC-SHA256 (RFC 2104, https://www.rfc-editor.org/rfc/rfc2104): HMAC(K, m) = H((K′⊕opad) ‖ H((K′⊕ipad) ‖ m)), where K′ is the key padded to 64 bytes.
- Python `hmac` (https://docs.python.org/3/library/hmac.html) is the reference. **Spark SQL has no HMAC function** (only `sha2`, `xxhash64`, `aes_encrypt`; checked the 4.0.2 and 4.1.1 function lists), and a row-wise Python UDF is slow.
- **Native construction.** Precompute `kipad = K′⊕0x36…` and `kopad = K′⊕0x5c…` in Python from the secret, then:
```python
inner = F.unhex(F.sha2(F.concat(F.lit(kipad), F.col("patient_id").cast("binary")), 256))
F.sha2(F.concat(F.lit(kopad), inner), 256).alias("patient_key")
```
  Unit-test it against `hmac.new(K, m, sha256).hexdigest()` (ENG-4).
- Load the key from `EPISCOPE_HMAC_KEY` (`.env`, git-ignored, with `.env.example` committed). Never log it.
- Why HMAC rather than sha256(salt‖id): the national-ID space is small enough to enumerate, so the secret key is the only protection. Rotating the key breaks linkage over time, so document a key-rotation policy.
- Dates of birth become age bands in silver: 5-year bands, matching the population table.

### 9.2 Small-cell suppression with complementary suppression (PRV-2)
Source: Hundepool et al., *Handbook on Statistical Disclosure Control*, 2nd ed. (Aug 2025), Ch. 4–5. https://ue.poznan.pl/wp-content/uploads/2025/12/KS-Handbook-on-Statistical-Disclosure-Control.pdf (1st-ed. ESSnet copy: https://research.cbs.nl/casc/SDC_Handbook.pdf).
- "To reach the desired protection for risky cells, it is necessary to suppress additional non-risky cells, which is called secondary suppression or complementary suppression."
- Post-tabular methods mean "each table must be individually protected" against comparison with other outputs (disclosure by differencing).
- **Table redesign** (aggregating geography or categories, thresholds) keeps tables additive.
- Alternatives: cell key method and rounding (Ch. 5.4–5.5).

**Algorithm for EpiScope exports:**
1. **Primary:** mark every cell with 1 ≤ n ≤ 4. Zeros stay published (state this policy).
2. **Secondary:** for every additive line (row/column total, district→division→nation, disease subtotals, *and* the time dimension, i.e. weekly vs monthly vs cumulative), if exactly one cell in the line is suppressed, also suppress the smallest non-zero unsuppressed cell in that line. Repeat until no line has exactly one suppression.
   - This heuristic stops exact recovery by subtraction. It does not guarantee a protection interval against LP attacks; τ-ARGUS-style optimal suppression does. Say so in the report.
3. **Derived values:** **suppress the rate whenever its count is suppressed.** Rate × public population gives back the count. The same goes for percentages and the cumulative series.
4. Prefer **table redesign** first: malaria outside the CHT at district-week level will be almost all 1–4. Publish it at division-month level instead.
5. Forecast medians and PI bounds below 5: display "<5" (conservative reading of "no count 1–4 appears in any output").
6. Test: an automated check that scans every exported JSON for integers in [1, 4] and for single-suppression lines.

### 9.3 k-anonymity (PRV-4)
Sweeney 2002, IJUFKS 10(5):557, https://doi.org/10.1142/S0218488502001648. A release is k-anonymous if each record is indistinguishable from at least k−1 others on the quasi-identifiers.
- Quasi-identifiers here: age band, sex, district/upazila, onset week, ICD-10 group, facility.
- Check: `groupBy(QIs).count()` and require min ≥ k (k=5, consistent with PRV-2). Generalise (age 10-year bands, district→division, week→month) or suppress records until it passes. Report the share of records suppressed.
- Note the known weakness: homogeneity attacks. If every record in a class shares the diagnosis, k-anonymity leaks it. Mention l-diversity as the remedy.

### 9.4 Differential privacy (PRV-4 discussion)
Dwork, McSherry, Nissim & Smith 2006, https://doi.org/10.1007/11681878_14. The Laplace mechanism adds Lap(Δf/ε) noise. For counting queries Δ=1, so the noise SD is √2/ε. ε=1 gives SD ≈ 1.4 per cell.

Points for the written discussion:
- the budget composes over all published cells and weeks
- noisy counts can be negative, so post-process them
- DP and fixed small-cell suppression differ in kind
- DP suits the *public* release, while ministry users see suppressed exact counts

### 9.5 Bangladesh data-protection law
- **Personal Data Protection Ordinance, 2025**: approved by the Council of Advisers 9 Oct 2025, gazetted **6 Nov 2025** together with the National Data Governance Ordinance. It names health data among the sensitive categories, and creates data-subject rights (access, correct, delete, restrict automated decisions). https://en.prothomalo.com/bangladesh/government/teeopu4dfv and https://www.tbsnews.net/bangladesh/govt-issues-gazettes-2-landmark-ordinances-data-protection-governance-1281356 (press reports; primary gazette **UNVERIFIED**)
- **Personal Data Protection (Amendment) Ordinance, 2026 (Ord. No. 23 of 2026)**, 5 Feb 2026: data-residency changes. https://digitalpolicyalert.org/change/18757-personal-data-protection-amendment-ordinance-2026-ordinance-no-23-of-2026
- Reported as replaced by the **Personal Data Protection Act, 2026 (Act 63 of 2026, April 2026)**, enforced by a National Data Management Authority. https://securiti.ai/bangladesh-personal-data-protection-act-overview/ (secondary; **UNVERIFIED** against bdlaws.minlaw.gov.bd)
- Cite the law as "PDPO 2025, enacted as the PDP Act 2026 (to be confirmed at bdlaws.minlaw.gov.bd)". Map PRV-1…4 to its principles (minimisation, purpose limitation, pseudonymisation, sensitive-data protection).

---

## 10. Optimisation (AN-7, AN-8; brief §6.3)

### 10.1 Solver and PuLP pinning
**PuLP 4.0.0 (2026-09-25) is breaking:**
- "CBC is not shipped inside the PuLP package … `PULP_CBC_CMD` … removed"
- variables use `prob.add_variable`, and `solve()` returns `LpSolveStats`
- it requires Python ≥3.12

Source: https://pypi.org/project/pulp/4.0.0/ and https://coin-or.github.io/pulp/guides/how_to_migrate_to_v4.html. Nearly every tutorial uses the 3.x API.

**Pin `pulp==3.3.2`.** Its wheel (16.4 MB) still bundles CBC as `PULP_CBC_CMD`. HiGHS is available via the extra `pulp[highs]` (highspy 1.13) as `pulp.HiGHS()`. An alternative is SciPy `linprog(method="highs")` or `highspy` directly.

### 10.2 The formulation in PuLP (3.x API)
```python
import pulp
m = pulp.LpProblem("allocation", pulp.LpMinimize)
x = pulp.LpVariable.dicts("x", (D, R), lowBound=0)
u = pulp.LpVariable.dicts("u", (D, R), lowBound=0)
m += pulp.lpSum(w[d] * u[d][r] / scale[r] for d in D for r in R)        # scale: see issue 2
for d in D:
    for r in R:
        m += u[d][r] >= yhat[d][r] - s[d][r] - x[d][r]                 # unmet demand
        m += x[d][r] >= alpha * p[d] * pulp.lpSum(x[e][r] for e in D)   # equity floor
m += pulp.lpSum(c[r] * x[d][r] for d in D for r in R) <= B              # budget
for k in K:
    m += pulp.lpSum(v[r] * x[d][r] for d in div[k] for r in R) <= T[k] # transport
m.solve(pulp.PULP_CBC_CMD(msg=False))     # or pulp.HiGHS(msg=False)
```

### 10.3 Checking the formulation
1. **Feasibility of the equity floor:** summing x_{d,r} ≥ α p_d Σ_{d′} x_{d′,r} over d gives X_r ≥ α (Σ_d p_d) X_r = α X_r, which holds for any α ≤ 1 because Σp_d = 1. x = 0, u = max(0, ŷ−s) is always feasible. **So the model is feasible for every α ∈ [0, 1)**, and at α=1 it forces x_{d,r} = p_d X_r exactly (pure population-proportional allocation).
   - The floor is *relative*. It constrains shares, not amounts, so it never forces spending. A budget increase raises every district's floor.
2. **Unit mixing:** the objective sums w_d·u_{d,r} over resources measured in beds, kits, litres of saline and teams. One NS1 kit then weighs the same as one bed. Normalise, e.g. by u_{d,r}/ŷ_{d,r} (share of need unmet), by unit cost c_r, or with explicit resource weights.
3. **No national stock constraint:** the brief's client asks to allocate "a limited national stock", but the LP only has a budget. Add Σ_d x_{d,r} ≤ S_r^{nat} if stock, not money, is the binding limit.
4. **Over-allocation:** x is unbounded above relative to need. Under the floor, low-need districts receive supplies beyond ŷ−s (waste). Optionally cap x_{d,r} ≤ max(ŷ_{d,r} − s_{d,r}, α p_d X_r), or report the "excess" as a cost of equity.
5. **Coupling with transport:** the floor forces every division to receive at least α·p_div·X_r. A division with small T_k then caps the *national* X_r ≤ T_k/(α p_div v_r), so a high α can silently throttle supply everywhere. Report the shadow prices (duals) of T_k.
6. **Integrality:** rapid-response teams are integers, which makes the problem a MILP. CBC and HiGHS both handle it at this size (64 × |R|).
7. **ŷ choice:** the brief says to use an upper prediction quantile, e.g. q₀.₉. Show the sensitivity of the plan to q₀.₅ vs q₀.₉.

**What α means for policy (C3):** α is the share of each resource's national allocation reserved for distribution by population.
- α=0: pure efficiency (stock goes where weighted unmet demand is largest).
- α→1: pure population-proportional allocation (the AN-7 baseline).
- α=0.3: "at least 30% of every shipment is shared by population; the rest follows risk".

### 10.4 Efficiency–equity curve (AN-8)
- Grid: B ∈ {0.25, 0.5, 0.75, 1.0, 1.5} × B_ref (≥5 levels as AN-8 requires) × α ∈ {0, 0.1, …, 0.9}. That is 50 solves of under a second each. Precompute it all; this is also the VIS-5 scenario grid.
- Efficiency axis: total weighted unmet demand, or the % of need met.
- Equity axis: minimum district coverage ratio min_d (s+x)/ŷ, or the Gini of per-capita allocation.
- Plot one curve per budget. Recommend the "knee": the α beyond which unmet demand rises steeply. An alternative is the ε-constraint method (minimise unmet subject to min coverage ≥ ε, sweeping ε).

---

## 11. Seasonal decomposition for the preparedness calendar (AN-9)

STL: Cleveland, Cleveland, McRae & Terpenning 1990, J. Official Statistics 6(1):3–73 (no DOI; **UNVERIFIED** page range). statsmodels `STL(endog, period, seasonal=7, trend=None, low_pass=None, robust=False, …)`:
- `seasonal` must be odd and "should normally be >= 7".
- `trend` defaults to the smallest odd integer > 1.5·period/(1−1.5/seasonal).
- `robust=True` down-weights outliers (outbreaks).

https://www.statsmodels.org/stable/generated/statsmodels.tsa.seasonal.STL.html

**Recipe:**
1. Weekly counts per division × disease group, 2021–2025 (5 seasons). Apply log1p, because seasonality is multiplicative. Use `STL(period=52, seasonal=13, robust=True)`. Map ISO week 53 into 52.
2. Seasonal index S_w = mean of the seasonal component per epi-week. Spread is the min/max across years.
3. **Onset week** w_on = first week after the trough where S_w rises above 25% of (max − min) above the minimum. **Peak** w_pk = argmax S_w.
4. **Pre-position by** week w_on − L_supply, where L_supply = procurement + transport lead time (assume 2–4 weeks, state it). **Start prevention campaign** at w_on − L_campaign: for dengue larval control about 4–6 weeks, matching the 4–8-week climate lag; for malaria, before May.
5. Show onset ±1 SD across years as a band in the heatmap view (VIS-2 view 3).

Caveat for the report: on synthetic data the calendar recovers the generator's own seasonality, which is circular. Validate dengue against the OpenDengue national monthly pattern: peaks Aug–Oct, with October in 2022/2024 (§1.2).

---

## 12. SEIR (AN-10, optional)

Minimal calibration:
- Model: dS/dt = −βSI/N, dE/dt = βSI/N − σE, dI/dt = σE − γI, dR/dt = γI. Integrate with `scipy.integrate.solve_ivp` (https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html).
- Observed weekly cases ≈ ρ × weekly σE (new infections × reporting fraction).
- Fix σ = 1/latent period and γ = 1/infectious period as stated assumptions. For a COVID-like surge, about 1/5 and 1/7 per day (**UNVERIFIED**, cite your chosen source).
- Fit β, ρ and I₀ by `scipy.optimize.least_squares` on √cases (https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html), or by an NB log-likelihood with `minimize`, using the growth phase plus peak of the injected surge.
- Report R₀ = β/γ. What-if scenarios multiply β by (1 − effect) from week t_int (NPIs) and compare peak height, timing and bed demand.
- Validation: the generator knows the true parameters, so report recovery error. If you generate the surge with SEIR, say so; recovery then tests the fitting, not the realism.

---

## 13. D3.js v7 dashboard (VIS-1 … VIS-7)

**Versions (npm, verified):**

| Package | Version | Licence |
|---|---|---|
| d3 | 7.9.0 | ISC |
| d3-geo | 3.1.1 | ISC |
| d3-brush | 3.0.0 | ISC |
| d3-zoom | 3.0.0 | ISC |
| d3-sankey | 0.12.3 | BSD-3; last release 2019, depends on d3-array 1–2 and d3-shape ^1.2, so load its UMD build separately (it works alongside v7) |
| topojson-client | 3.1.0 | ISC |
| topojson-server | 3.0.1 | ISC |
| topojson-simplify | 3.0.3 | ISC |
| mapshaper | 0.7.83 | MPL-2.0 |

Vendor the files into `dashboard/vendor/`, so the dashboard runs offline with `python -m http.server`.

**Projection:**
- Bangladesh spans about 20.6–26.6°N. Mercator area scale is sec²φ: 1.14 at 20.6° and 1.25 at 26.6°, about 9% relative distortion. For a choropleth use **equal-area**: `d3.geoConicEqualArea().parallels([21.5, 25.5]).rotate([-90.3, 0]).fitExtent([[10,10],[w-10,h-10]], geojson)`. `fitExtent` sets scale and translate to fit. https://d3js.org/d3-geo/projection and https://d3js.org/d3-geo/conic
- **Winding pitfall:** d3 uses spherical polygons, and "the exterior ring for polygons smaller than a hemisphere must be clockwise". https://d3js.org/d3-geo. RFC 7946 GeoJSON is counter-clockwise, so an unrewound file fills the whole globe. Rewind with mapshaper (it outputs d3-compatible TopoJSON) or `turf.rewind({reverse:true})`.

**Simplification** (mapshaper reference, https://mapshaper.org/docs/reference.html):
```
mapshaper bgd_admin2.geojson -simplify 5% weighted keep-shapes \
  -filter-fields adm2_pcode,adm2_name,adm1_pcode \
  -o format=topojson quantization=1e5 dashboard/data/bgd_adm2.topo.json
```
- `-simplify <pct>` keeps that % of removable points. Weighted Visvalingam is the default. `keep-shapes` prevents small polygons (islands such as Bhola and Hatiya) from disappearing.
- TopoJSON `quantization=` sets the grid. Shared arcs make district borders consistent.
- Expected output is a few hundred KB for ADM2 (**UNVERIFIED**, measure it). Client side: `topojson.feature(topo, topo.objects.<name>)` and `topojson.mesh` for borders.

**Brush and zoom:** use `d3.brushX()` on the context strip of the time series (focus+context). On `"brush end"`, broadcast `[t0, t1]` to the other views. https://d3js.org/d3-brush. Use `d3.zoom` for the map only.

**Coordinated views (VIS-3):** keep a single state object `{district, disease, t0, t1, threshold, budget, alpha}` and a `d3.dispatch("change")` bus. Every view subscribes and re-renders from state. This avoids view-to-view spaghetti and lets the VIS-5 sliders swap in a precomputed scenario file.

**10 MB budget (VIS-4):**
- Weekly district × disease series: 64 × 4 × about 261 weeks ≈ 67k points. Fields: observed, median, lo80, hi80, threshold, alert.
- **Ship columnar JSON** (one array per field per series, integers only). That is about 2–3 MB, against about 6–8 MB as row objects.
- Add the TopoJSON (<0.5 MB), the scenario grid (50 allocations × 64 × |R|, about 0.5 MB), the DQ report and the calendar.
- Total should be about 4–5 MB.
- Suppressed cells are `null` plus a `suppressed` bitmask, never 0. Add a freshness timestamp in a `meta.json`.

**Accessibility (VIS-6):** use ColorBrewer/viridis-type colour-blind-safe sequential palettes (`d3.interpolateViridis`, `d3.schemeBlues`), hatching for suppressed cells, and always draw the 80% band.

---

## 14. Orchestration and storage (ENG-1, ENG-4, ING-5)

**Orchestration choice:**
| Option | Laptop cost | Retries / DAG | Verdict |
|---|---|---|---|
| Makefile | none | file-target DAG, no retries | **Entry point for `make all`** (ENG-1) |
| Prefect 3 (3.8.8) | one Python package, ephemeral local API | `@task(retries=…, retry_delay_seconds=…)`, `@flow(retries=…)` (https://docs.prefect.io/v3/develop/write-tasks) | **Use it for ENG-4 "orchestrated DAG with retries"**, called from make |
| Airflow 3 (3.3.2) via Docker Compose | Docs: "allocate at least 4GB memory for the Docker Engine (ideally 8GB)" | full scheduler | **Avoid.** The same page warns that on **Fedora, ArchLinux, RHEL, Rocky** a containerd change makes Airflow in Compose consume "100% memory". https://airflow.apache.org/docs/apache-airflow/stable/howto/docker-compose/index.html |

**Storage (ING-5):**
- **MinIO:** the `minio/minio` GitHub repo is **archived** (verified via the GitHub API). Its README says "THIS REPOSITORY IS NO LONGER MAINTAINED … distributed as source code only". https://github.com/minio/minio. Docker Hub images stopped in Oct 2025 (press reports, e.g. https://madewithlove.com/blog/thanks-for-all-the-buckets/). **This contradicts the brief's suggestion of MinIO.** Pinning an old quay.io image is possible but unpatched.
- **SeaweedFS** (Apache-2.0, actively maintained, `chrislusf/seaweedfs` images updated 2026-10-10): `weed server -s3` gives a single-container S3 endpoint. https://github.com/seaweedfs/seaweedfs
- **HDFS:** `apache/hadoop` official images (3.4.1, 3.4.2, 3.4.3, 3.5.0) come with an included docker-compose (namenode, datanode, resourcemanager, nodemanager). https://hub.docker.com/r/apache/hadoop. Match the image to Spark's bundled Hadoop (3.4.1 for Spark 4.0).
- Delta on S3-compatible storage: single-cluster mode needs `hadoop-aws` "for the version of Hadoop that Apache Spark is compiled for". Concurrent writes from multiple drivers carry caveats, which are fine for a single local driver. https://docs.delta.io/latest/delta-storage.html
- **Recommendation:** develop on the local FS. Add a `docker-compose.yml` profile `storage` with SeaweedFS S3 for ING-5, plus `s3a://` paths via env vars, so the same code runs on both. HDFS costs 2–3 JVM containers (about 2–3 GB RAM), which is too heavy alongside Spark on 16 GB.

---

## 15. Contradictions and risks vs the brief

1. **Dengue timing.** Recent peaks are Sep–Nov, not "monsoon" May–Sep (OpenDengue 2022, 2024).
2. **Farrington Flexible** with b=5 cannot be evaluated on 2021–2025 data (§3.3).
3. **EARS** is unadjusted for seasonality and built for daily data. On weekly data it alarms at every season onset (§3.1).
4. **The §6.3 LP is always feasible.** The equity floor is relative. The objective mixes units. There is no national-stock constraint. Floor × transport capacity can throttle supply (§10.3).
5. **MinIO** is no longer distributed as binaries or images (§14).
6. **PuLP 4.0** removed bundled CBC. Pin 3.3.2 (§10.1).
7. **Open-Meteo** 5-year pulls exceed the hourly free quota if not throttled (§2.3).
8. **geoBoundaries** has no BBS codes and old names. HDX population totals 165.65M vs the census-adjusted 169.83M (§2.1–2.2).
9. **AQE skew join** will not trigger at this data size with default thresholds (§6.5).
10. **Bangladesh weekend is Fri–Sat.** "Fewer reports on weekends" must use Fri/Sat (§8.3).
