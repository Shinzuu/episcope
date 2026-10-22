# EpiScope CEP: requirements analysis

Source: `source/CEP_EpiScope.pdf` (12 pages, CSE 4345, instructor Rokan Uddin Faruqui, Spring 2026).
This file is our reading of what the brief requires, how each part is assessed, and the risks we planned for.
Technical detail for each method and dataset, checked against primary sources, is in `research.md`.

## 1. The problem in one paragraph

The Ministry of Health (a teaching case, not a real request) wants a prototype national platform. It turns
routine health, climate and logistics data into four outputs for every district:

1. early detection of unusual rises in dengue, diarrhoea, influenza-like illness and malaria;
2. 1–4 week forecasts of cases and hospital demand;
3. an allocation of a limited national stock of supplies and response teams;
4. a seasonal preparedness calendar for the next year.

Users are not data scientists. The system has to explain itself, cope with late and incomplete data, never
expose patients, run on modest hardware and scale nationally.

We build it on synthetic patient data (25 million or more encounters, 2021–2025, 64 districts) generated from
real public denominators, geography and weather. The stack is a Spark bronze/silver/gold lakehouse,
established surveillance and forecasting methods, a linear programme and a D3.js v7 dashboard. We hand over
exactly three items: the repository, the report and the video.

## 2. Hard facts from the brief

| Item | Value |
|---|---|
| Marks | 100: 70 group (R1–R7), 30 individual (R8 video segment 15, R9 contribution 15), +5 bonus for C items, capped at 100 |
| Duration | 14 calendar days. Day 0 and Day 14 are "announced in class", so the PDF gives no dates |
| Milestones | M1 proposal at the end of Day 3. M2 tag `checkpoint` at the end of Day 7 (formative, not graded). M3 tag `v1.0` at the end of Day 14 |
| Late penalty | 10% of the group mark per day for up to 3 days, then not graded |
| Compute | 16 GB laptop, Spark local mode or Docker, or a free tier (Colab, Databricks Free). Nothing paid |
| Must-run test | The instructor clones the repo, follows only the README, and runs a **hidden 14-day incremental drop** containing unannounced outbreaks and quality defects. The code must ingest it unmodified |
| Performance gate | At SF=1 the full pipeline finishes in **45 minutes or less** on a 16 GB laptop |
| Browser gate | The dashboard loads only aggregated gold output, **at most 10 MB in total** |
| Visualisation | D3.js v7 only for the required views. Plotly, Chart.js, Vega-Lite, Tableau and Power BI are banned |
| Processing | Spark for all heavy transforms. Pandas is allowed only for small reference data or final aggregates |
| AI tools | Allowed, but `AI_USAGE.md` is required, and code you cannot explain earns no credit |

## 3. How the work is assessed

| Criterion | Marks | What actually earns it |
|---|---:|---|
| R2 Data engineering | 16 | Idempotent incremental loads proven by tests, 12+ quality rules, lineage, privacy enforced by design, **hidden drop runs without changes** |
| R3 Analytics | 16 | Methods justified from the literature, clearly beating baselines in proper backtests, the artefact handled, an efficiency–equity recommendation |
| R8 Individual video | 15 | At least 4 minutes in your own lead area, explaining *why* and connecting to the other parts. Graded per person |
| R9 Individual contribution | 15 | Steady commits across roles, a share consistent with Git, a reflection |
| R4 Dashboard | 12 | Five linked views, uncertainty and suppression shown, fast, tested with users |
| R1 Problem analysis | 8 | Each conflict C1–C5 analysed with evidence, P1–P7 tied to specific design decisions, a realistic risk register |
| R7 Communication | 7 | Report plus video showing the real system working |
| R5 Performance | 6 | Scaling study, two or more optimisations quantified from the Spark UI |
| R6 Ethics | 5 | Concrete privacy, equity and misuse analysis, an honest policy brief |

R2 + R3 = 32 marks, and both depend on the generator and pipeline being right. The 30 individual marks
depend on each person really owning and explaining their own area, and on steady commits.

## 4. The four-member problem (raise this with the instructor first)

The brief is written for **3 students** throughout: "Working in a team of three", "all three members
committing", three roles, a contribution matrix with three columns. The class was told groups are 3–5, and our
group has four members. Four things break:

1. **The moderation formula penalises a four-person group.** πᵢ = clip(3σᵢ/100, 0.8, 1.1). With equal shares
   σ = 25%, 3 × 25 / 100 = 0.75, clipped to **0.8**. Every member loses 20% of the group mark even when
   everyone contributes equally. With five members, σ = 20% gives 0.6, also clipped to 0.8. The formula should
   be πᵢ = clip(N·σᵢ/100, …), where N is the group size. **Ask the instructor to confirm the formula uses N = 4
   for us. This is worth up to 14 marks each.**
2. **Roles.** Only three are defined: Data Engineer, Analytics Engineer and Visualisation Engineer. A fourth
   member needs a lead area of their own for R8. Proposal: **Platform & Evaluation Engineer**, covering
   orchestration and CI, the scalability experiment and Spark optimisations (ENG-2/3/4, R5), the privacy layer
   (PRV-1/2/4), the backtest and evaluation harness (AN-2/AN-5), and the report and policy brief lead. This
   takes load off the Data and Analytics roles, which carry the most requirements.
3. **Video length.** The video must be 15–20 minutes, and every member presents at least 4 minutes. With four
   members that is at least 16 minutes, leaving at most 4 minutes for the six required chapters outside the
   individual segments. Plan for the chapters to be **inside** the individual segments, and ask whether the
   20-minute cap is extended for four members.
4. **Contribution matrix.** Add a fourth column. Shares must total 100.

## 5. Requirement catalogue: counts and our recommended scope

23 Must, 9 Should and 4 Could requirements. "All M well" earns a solid pass. Higher grades need S items and
good reasons.

| Area | Must | Should | Could |
|---|---|---|---|
| Ingestion and storage | ING-1, 2, 3, 4 | ING-5 (HDFS/MinIO), ING-6 (streaming, under 5 min) | ING-7 (Delta time travel) |
| Quality and privacy | DQ-1, 2, 3; PRV-1, 2 | DQ-4 (patient de-duplication), PRV-3 (role views) | PRV-4 (k-anonymity, DP discussion) |
| Analytics | AN-1, 2, 4, 5, 6, 7, 9 | AN-3 (two methods plus the artefact), AN-8 (equity curve) | AN-10 (SEIR) |
| Visualisation | VIS-1, 2, 3, 4 | VIS-5 (what-if), VIS-6 (accessibility) | VIS-7 (mobile or animation) |
| Engineering | ENG-1, 2, 3 | ENG-4 (tests plus DAG with retries) | |

**Recommended target:** all M, plus every S except ING-5. ING-5 is the one with poor value for effort: a
Docker HDFS or MinIO setup on a 16 GB laptop eats RAM needed for Spark. Revisit it on Day 11 if there is time.

Two items are almost free given the design choices below:
- **ING-7:** with Delta Lake we get time travel at no extra cost. It earns bonus marks and makes AN-5's "what
  did we know on date t" backtest honest.
- **AN-3:** the rubric's top band asks for "artefact handled", so this one is effectively required.

## 6. Risks that are easy to miss (traps)

1. **Hidden test drop.** The pipeline must work on new dates it has never seen. Rules:
   - never hard-code dates, file names or row counts;
   - discover input files by glob;
   - derive the processing window from the data;
   - keep the generator's output schema exactly as specified, since it is the contract.

   Test this ourselves: generate a fresh 14-day drop with a different seed and run the README steps on a clean
   clone.
2. **Ground truth isolation.** `ground_truth_outbreaks` must never be read by detection code. Keep it in a
   separate path. Add a test that fails if `analytics/detection/` references it. Only `analytics/evaluation/`
   reads it.
3. **Idempotency with late data (ING-4).** Re-running a day must create no duplicates, and records up to 21
   days late must update the affected epi-weeks. Do this with Delta MERGE keyed on `encounter_id`, then rebuild
   only the gold partitions those weeks touch. The video must show a re-run with no duplicates and a quarantine
   example.
4. **Built-in defects to catch.** All of these are required in our own generator:
   - about 2% duplicated `encounter_id`;
   - about 0.5% orphan `facility_id`;
   - about 1% invalid age or sex;
   - `onset_date > report_date` cases;
   - bad ICD-10 codes;
   - weekend dips.

   The quality rules (DQ-1, 12 or more) should map to these one-to-one, plus schema, null and geography checks.
5. **Complementary suppression (PRV-2).** Hiding counts of 1–4 is not enough. A district total minus the
   visible cells must not reveal a hidden cell. Where a row or column has exactly one suppressed cell, a
   second cell must also be suppressed. This has to hold in every export, including CSV downloads.
6. **Salt outside the repo (PRV-1).** Use HMAC-SHA256 with the key in `.env`, and provide `.env.example`. A
   salt committed to Git, even once in history, breaks the requirement.
7. **No line-level data in the browser (VIS-4)**, and no data files over 50 MB in the repo. The data is
   generated, never committed. Add `.gitignore` entries for `data/`, `lake/` and `spark-warehouse/`.
8. **Requirement IDs everywhere.** Commits, issues and README sections cite IDs (for example
   `ING-4: merge late records into silver`). The README needs an ID → file → report-section table.
9. **The reporting artefact.** A facility goes silent for three weeks, then back-fills. A naive detector
   flags the back-fill week as an outbreak. Two defences, both reportable for AN-3:
   - count by `onset_date`, not `report_date`;
   - mark facility-weeks with zero reports as missing, not zero.
10. **"Results in the video must match v1.0."** Record the video only after tagging. Speed-ups are allowed
    only with timestamps visible on screen.
11. **Usability check.** Test with at least 2 non-CS users. Easy to forget, and it feeds R4 and report §7.
12. **Equity floor in the LP.** As written, xᵈʳ ≥ α·pᵈ·Σ x. The floor scales with what is allocated, so it
    forces a share to districts with no forecast need. That is the efficiency cost of equity the curve should
    show. With α = 0 the LP is pure efficiency, and as α → 1 it approaches population-proportional allocation.
    Feasibility, transport capacity and the formulation are checked in `research.md` §10.

## 7. How the parts depend on each other (P7)

```
public data (S1–S3) ─┐
generator (S4–S6) ───┼─> bronze ─> silver (DQ, de-id) ─> gold marts ─┬─> detection ─┐
                     │                                               ├─> forecast ──┼─> demand ─> optimiser ─┐
                     │                                               └─> STL calendar                         │
                     └──────────────────────────── serving (suppressed JSON ≤ 10 MB) <───────────────────────┘
                                                                     └─> D3 dashboard
```

The generator is the critical path. Nothing downstream can be tested until it produces S4–S6 at SF=0.25. The
Gantt in the brief runs it D1–D4, with bronze/silver from D3, detection from D5, dashboard from D4, and the
optimiser D8–D11. The dashboard can start on Day 4 against a hand-written fake gold JSON with the agreed
schema.

## 8. Feasibility on our hardware

This laptop has 15 GB RAM, 8 threads, Java 11 and Docker, and PySpark is not installed.

- **Spark version and Java.** Pin `pyspark==4.0.1` + `delta-spark==4.0.1` (research.md §6–7). Spark 4 needs
  **Java 17**; this laptop has Java 11, so install JDK 17 (or run in Docker). Spark 3.5 would need numpy < 2.
  Pin `pulp==3.3.2`: PuLP 4.0.0 (2026-09-25) dropped the bundled CBC solver and changed its API.
- **Data size.** 25M rows is about 4 GB as CSV and roughly 0.6 GB as Parquet, which is about 13,700
  encounters per day nationwide. This size is fine for local-mode Spark. Two parts carry the 45-minute risk:
  - the generator itself, if written row by row in Python;
  - the per-district model fitting.

  Generate with vectorised numpy, one partition per district and year, written straight to Parquet, or with
  Spark's own random functions. Measure SF=0.25 on Day 4.
- **Docker for HDFS/MinIO** costs 1–2 GB of RAM. That is a reason to skip ING-5, or run it only for one
  experiment.

## 9. Corrections from the primary-source research (research.md)

These change design choices; each is cited in `research.md`.

| Finding | Consequence |
|---|---|
| 2023 dengue: 321,179 hospitalised, 1,705 deaths (DGHS year-end); brief's figures hold | Use these in the report introduction |
| OpenDengue shows national dengue peaks in **October** (2022, 2024), later than "follows the monsoon" | Generator seasonality and the preparedness calendar must peak in Sep–Nov, not Jun–Aug |
| Farrington needs about 5 years of history before its first test week; we only have 2021–2025 | Use **EARS C2 as primary, CUSUM as the second method** (AN-1, AN-3) |
| EARS has no seasonal adjustment and was built for daily counts | Remove seasonality first (e.g. residuals from an STL or seasonal baseline), or it fires at every season onset |
| geoBoundaries lacks official district codes; HDX COD-AB has BBS codes and centroids | Use HDX for S1 and as the join key for S2 and S3 |
| Open-Meteo counts every 2 weeks of data as one call; 64 districts × 5 years ≈ 8,400 calls, over the 5,000/hour cap | Throttle and batch coordinates; cache in bronze once |
| MinIO's repo is archived (source-only) | If we do ING-5, use SeaweedFS (S3 API) or Apache Hadoop images |
| Spark has no built-in HMAC | Build HMAC-SHA256 from `sha2` on the column (fast) rather than a Python UDF over 25M rows |
| AQE skew handling will not trigger at this data size with default thresholds | For ENG-3 evidence, lower the thresholds or demonstrate salting on the Dhaka skew |
| Bangladesh's weekend is **Friday–Saturday** | The generator's weekend reporting dip falls on Fri/Sat |
| The brief's LP is always feasible; α in [0, 1) moves from pure efficiency to population-proportional | It mixes beds and kits in one objective and has no national stock cap; say so and normalise per resource in the report |
| 25M rows under 45 min in local mode: about 15–25 min estimated | Measure at SF=0.25 on Day 4 |

## 10. Questions to ask the instructor

1. Does the moderation factor use N = 4 for a four-member group, so that equal shares give π = 1?
2. With four members presenting at least 4 minutes each, may the video run over 20 minutes?
3. Is a fourth role (Platform & Evaluation Engineer) acceptable, or should four members split the three roles?
4. What are the exact Day 0 and Day 14 dates, and the deadline time?
5. Which GitHub account should the private repository be shared with?

## 11. Proposed 14-day plan for four people

| Days | Data Engineer | Analytics Engineer | Visualisation Engineer | Platform & Evaluation |
|---|---|---|---|---|
| D1–D3 | Generator schema (commit by M1), public data pulls (S1–S3) | Choose diseases and methods, write the detection and forecast spec | Wireframe the 5 views, gold JSON contract, TopoJSON map | Repo, Makefile/DAG skeleton, CI, `.env` salt, proposal and risk register lead |
| D4–D7 | Generator at SF=0.25, bronze → silver → gold, DQ rules, quarantine | EARS C2 (on de-seasonalised counts) and CUSUM in Spark, artefact handling | First view reading real gold (M2) | Delta MERGE idempotency tests, de-identification, suppression module |
| D8–D11 | Incremental loads, lineage, late data, hidden-drop rehearsal | Forecast models plus backtest, demand conversion, LP, STL calendar | Linked views, what-if grid, accessibility | Evaluation harness (AN-2, AN-5), scaling runs SF ∈ {0.25, 0.5, 1}, Spark optimisations with UI screenshots |
| D12–D14 | Code freeze, clean-clone test | Results tables for the report | Usability test with 2 non-CS users | Report assembly, policy brief, tag `v1.0`, then the video |

Everyone commits in someone else's area every few days, which R9 rewards and the brief requires.
