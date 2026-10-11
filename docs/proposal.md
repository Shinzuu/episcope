# EpiScope: project proposal (M1)

CSE 4345 Big Data Analytics · Complex Engineering Problem · Group of four: Shezan, Rimjhim, Rafi, Samira

## 1. Problem restatement

The Ministry of Health (a teaching case) needs a prototype platform that turns routine health, climate and
logistics data into four outputs for each of Bangladesh's 64 districts:

1. early warning of unusual rises in dengue, diarrhoeal disease, influenza-like illness (ILI) and malaria;
2. 1–4 week forecasts of cases and hospital demand;
3. an allocation of a fixed national stock of supplies;
4. a seasonal preparedness calendar.

Users are not data scientists. The platform must explain itself, tolerate late and missing reports, never
expose a patient, and run on a 16 GB laptop while able to grow to national scale.

Real patient data cannot be used. We generate a synthetic encounter feed (at least 25 million records,
2021–2025) whose seasonality, climate response and population structure come from real public data:
HDX/BBS district boundaries and populations, and Open-Meteo weather. Dengue seasonality is checked against
OpenDengue, which shows national peaks in October, later than the monsoon itself.

## 2. Stakeholders and conflicts

| Conflict | Stakeholders pulling each way | How we will resolve it, with evidence |
|---|---|---|
| C1 Sensitivity vs specificity | District officers want early alerts; epidemiologists want few false alarms | Sweep the EARS C2 threshold and CUSUM parameters. Plot sensitivity against false alerts per district-year and median days to detection on the injected outbreaks. Choose the operating point at an agreed false-alert budget |
| C2 Granularity vs privacy | Vector-control units want upazila maps; the privacy regulator wants no small counts | Publish district-week by default. Suppress counts 1–4 everywhere, with complementary suppression so totals cannot reveal them. Upazila only where every cell is 5 or more |
| C3 Efficiency vs equity | Policymakers must defend "why district X got more"; districts each want a share | LP with equity floor α. Trace unmet weighted demand against α at 5 or more budget levels and recommend one α with its cost |
| C4 Freshness vs cost | Outbreak response wants hours; the laptop and budget want batch | Daily idempotent batch for forecasts and allocation. A Structured Streaming file-source path refreshes alerts within 5 minutes (ING-6) |
| C5 Accuracy vs interpretability | Hospital managers want accuracy with bands; officials want to understand the model | Negative-binomial GLM with lagged rainfall and temperature as the main model, compared against seasonal-naïve and one ML model in the backtest. Keep the GLM unless the alternative wins on WIS by a clear margin |

## 3. Planned architecture and tools

- **Lakehouse.** Spark 4.0.1 (PySpark, local mode) with Delta Lake 4.0.1, in bronze, silver and gold layers.
  - Bronze holds raw files plus ingestion metadata: source, load time, batch ID and checksum.
  - Silver holds clean, conformed, de-identified data. HMAC-SHA256 patient IDs use a salt kept in `.env`.
  - Gold holds epi-week marts partitioned by `epi_year` and `division`.

  We chose Delta for ACID MERGE, which makes ING-4 late-data upserts idempotent. Its time travel gives honest
  "what did we know on date t" backtests (ING-7).
- **Quality.** At least 12 rules with a quarantine table and reason codes. A per-load quality report is
  written to gold. Lineage comes from bronze batch IDs carried to every gold row.
- **Analytics.**
  - Detection: EARS C2 on seasonally adjusted counts, plus CUSUM as the second method. Farrington is ruled
    out because it needs about 5 years of history before its first test week.
  - Forecasting: per-district negative-binomial GLM, run with `applyInPandas`, backtested over 52 weeks with
    rolling origins against a seasonal-naïve baseline, reporting MAE, 80% interval coverage and WIS.
  - Allocation: PuLP 3.3.2 with the CBC solver.
  - Calendar: STL decomposition per division and disease.
- **Serving and dashboard.** Gold data goes to suppressed JSON exports of at most 10 MB. The dashboard uses
  D3.js v7 with TopoJSON and has five coordinated views plus a what-if panel built on a precomputed scenario
  grid.
- **Orchestration and reproducibility.** A Makefile DAG with retries, pinned `requirements.txt` and
  `mise.toml`, and one command, `make all`, from a clean clone.

## 4. Diseases and methods

All four diseases are detected. We forecast **dengue** (rain and temperature lag of 4–8 weeks) and
**diarrhoeal disease** (spikes after extreme rainfall), because both have strong climate drivers to test.

## 5. Roles (proposed, to be confirmed)

The brief defines three roles. Our group of four adds a fourth so that each member has a lead area to present.

| Member | Lead role | Main requirements |
|---|---|---|
| TBD | Data Engineer: generator, ingestion, lakehouse, incremental loads, quality, lineage | ING-1–6, DQ-1–4 |
| TBD | Analytics Engineer: detection, forecasting, demand conversion, optimiser, calendar | AN-1, 3, 4, 6, 7, 8, 9 |
| TBD | Visualisation Engineer: serving exports, suppression in outputs, D3 dashboard, usability test | VIS-1–7, PRV-2, PRV-3 |
| TBD | Platform & Evaluation Engineer: orchestration, tests, de-identification, evaluation harness, scaling study, report lead | ENG-1–4, PRV-1, PRV-4, AN-2, AN-5 |

Every member also commits outside their own role and can explain every component.

## 6. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | Generator too slow or unrealistic, blocking everything downstream | Medium | High | Vectorised numpy per district partition. Measure SF=0.25 on Day 4. Check seasonality against OpenDengue |
| 2 | SF=1 pipeline exceeds 45 minutes on 16 GB | Medium | High | Profile in the Spark UI. Broadcast the registry, prune partitions, compact files, tune AQE and salting |
| 3 | Hidden test drop fails (hard-coded dates or paths) | Medium | High | Discover files by glob and derive dates from the data. Rehearse with a fresh seed on a clean clone before `v1.0` |
| 4 | Reporting artefact causes false alerts | High | Medium | Count by onset date. Treat silent facility-weeks as missing, not zero |
| 5 | Small-cell leak through totals | Medium | High | Complementary suppression with a unit test that tries recovery by subtraction |
| 6 | Open-Meteo rate limit (about 8,400 weighted calls) | High | Low | Throttle, batch coordinates, cache once in bronze |
| 7 | Uneven commits hurting individual marks (R9) | Medium | High | Small daily commits by everyone, cross-role reviews, requirement IDs in every commit |
| 8 | Video over length with four presenters | Medium | Medium | Script the six chapters inside the individual segments. Rehearse with a timer |
