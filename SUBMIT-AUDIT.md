# EpiScope submission audit (2026-10-20)

Every requirement in the brief (`CEP_EpiScope.pdf` §6.2, §8, §9) mapped to its file, the evidence that it
works, a status and the effort left. Status: **done** (code plus evidence), **partial** (code, evidence
missing or thin), **missing**. Owner is the lead from `docs/proposal.md` §5.

Starting point: 14 commits, all on 2026-10-11, all by shinzuu. The local lake was an SF=0.05 build made
before the real weather file existed (synthetic climate fallback).

## Must-run checks

| Check | Result | Evidence |
|---|---|---|
| `make check` (5 gates) at start | gate 1 PASS (141 tests, 34 min), gate 2 PENDING (ENG-2, ENG-3 not traced), gate 3 PASS, gate 4 PASS, gate 5 PENDING (no SF=1 run) | run on 2026-10-20 |
| Hidden-drop drill from a fresh clone, README steps only | PASS on 2026-10-11 at SF=0.05: +8,292 silver rows, 0 duplicates after re-run, export 9.64 MB, 22 min. Re-run planned on the final commit | `docs/experiments/hidden_drop_drill.json` |
| SF=1 ≤ 45 min | not measured | `docs/experiments/scaling.json` missing |
| Dashboard data ≤ 10 MB | 9.64 MB at SF=0.05 (export trims the weekly window to fit; see `serving/export.py`) | drill JSON |
| D3 v7 only | yes: `dashboard/vendor/d3.v7.9.0.min.js` with SRI hash, no Plotly/Chart.js/Vega anywhere | grep of the repo |
| Spark for heavy transforms | yes. Pandas only inside `applyInPandas` groups and on small gold outputs (demand, allocation, calendar) | grep `toPandas` |
| `AI_USAGE.md` present and honest | present; lists the scaffold, research and the six worker agents. Did not yet list the report and transcript drafting | `AI_USAGE.md` |
| No salt or key in the repo | `.env` git-ignored, `.env.example` holds a placeholder that the code refuses | `pipeline/privacy/deid.py`, gate 4 |

## Requirement map

| ID | Pri | Owner | File | Evidence | Status | Effort |
|---|---|---|---|---|---|---|
| ING-1 | M | Shezan | generator/, pipeline/reference/, pipeline/bronze/ingest.py | tests/test_generator.py, test_pipeline.py; checksum + batch ID + load time per row | done. Fix: generator wrote to `./data/raw` even when `EPISCOPE_DATA_DIR` pointed elsewhere | 15 min |
| ING-2 | M | Shezan | pipeline/silver, pipeline/gold | all transforms in Spark | done | – |
| ING-3 | M | Shezan | pipeline/silver, pipeline/gold | Delta partitioned by `epi_year`, justified in docstrings. **No catalog registration** | partial | 45 min |
| ING-4 | M | Shezan | pipeline/silver/build.py (MERGE), pipeline/gold/build.py (replaceWhere) | test_rerun_is_idempotent, test_late_records_update_their_onset_week, drill | done | – |
| ING-5 | S | Shezan | – | skipped by design (RAM; ANALYSIS.md §5) | missing (justified) | 3 h+, not planned |
| ING-6 | S | Shezan | – | proposal promises a Structured Streaming path; nothing built | missing | 1.5–2 h |
| ING-7 | C | Shezan | – | Delta history exists on every table, but no "as of date t" reader | missing | 45 min |
| DQ-1 | M | Shezan | pipeline/quality/rules.py | 14 encounter + 7 facility + 7 stock rules, tests/test_quality.py | done | – |
| DQ-2 | M | Shezan | pipeline/quality/engine.py, gold.dq_report, dashboard dq.json | tests | done | – |
| DQ-3 | M | Shezan | batch_ids on gold rows, bronze._ingest_log | test_lineage_gold_to_bronze | done | – |
| DQ-4 | S | Shezan | pipeline/silver/dedup.py | precision 0.998 on 441 flagged pairs (SF=0.05) | done; recall 0.11 must be stated honestly | – |
| PRV-1 | M | Samira | pipeline/privacy/deid.py | HMAC-SHA256 via `sha2`, tests/test_privacy.py | done | – |
| PRV-2 | M | Rimjhim | pipeline/privacy/suppression.py, serving/export.py | complementary suppression + audit, tests | done | – |
| PRV-3 | S | Rimjhim | – | no role-based view | missing | 2 h |
| PRV-4 | C | Samira | pipeline/privacy/kanon.py | k-anonymity check; DP discussion goes in the report §8 | partial (report text) | report |
| AN-1 | M | Rafi | analytics/detection | EARS C2 (window functions) + CUSUM (applyInPandas) | done | – |
| AN-2 | M | Samira | analytics/evaluation | code and tests exist; **no result file** in the repo | partial | one run |
| AN-3 | S | Rafi | analytics/detection/prepare.py | onset-week counting + completeness; test in test_detection.py | done; numbers need the evaluation run | one run |
| AN-4 | M | Rafi | analytics/forecast | NB-GLM per district via cogrouped applyInPandas | done | – |
| AN-5 | M | Samira | analytics/forecast/backtest.py | **no backtest result file** in the repo | partial | one run |
| AN-6 | M | Rafi | analytics/forecast/demand.py | assumptions in demand_assumptions.py | done | – |
| AN-7 | M | Rafi | analytics/optimise/lp.py | allocation_summary.json (SF=0.05, synthetic climate) | done; rerun on final data | one run |
| AN-8 | S | Rafi | analytics/optimise/curve.py | 5 budgets × 11 α; recommends α = 0 | done; the recommendation logic gives α = 0 at every budget, worth a look by Rafi | – |
| AN-9 | M | Rafi | analytics/calendar/stl_calendar.py | calendar_summary.json | done | – |
| AN-10 | C | Rafi | – | SEIR exists only inside the generator, no analytics module | missing | 2 h |
| VIS-1..7 | M/S/C | Rimjhim | dashboard/ | dashboard/tools/verify.mjs, tests/test_dashboard.py | done (VIS-7 as mobile layout) | – |
| VIS usability test | M (role) | Rimjhim | – | no record of a test with 2 non-CS users | missing | 1 h with two people |
| ENG-1 | M | Samira | Makefile | `make setup && make all` | done; README lacks prerequisites, hidden-drop how-to and an architecture summary (H1 asks for all three) | 30 min |
| ENG-2 | M | Samira | – | no scaling run at SF ∈ {0.25, 0.5, 1} | missing, **blocks gate 5** | harness 30 min + ~1.5 h of runs |
| ENG-3 | M | Samira | – | broadcast joins and AQE are in the code, but no before/after measurement or Spark UI screenshots | missing | 2 h |
| ENG-4 | S | Samira | tests/ (141 tests); Makefile | tests done; **no DAG with retries** (the proposal promised one) | partial | 1 h |

## Handover items (§8)

| Item | Status |
|---|---|
| H1 repo: README with video link, prerequisites, one command, hidden-drop how-to, architecture summary, traceability | partial: traceability done; prerequisites, drop how-to and architecture missing |
| H1 `docker-compose.yml` | not present; `mise.toml` + `requirements.txt` pin the environment. Say so in the README |
| H1 commit history from all members across two weeks | **at risk**: every commit is shinzuu's, all on one day. R9 is 15 marks per person |
| H1 tag `v1.0` | not tagged (outbound, waits for shinzuu) |
| H2 report (IEEE conference, ≤ 15 pages + appendices A policy brief, B contribution matrix, C reflections) | missing |
| H3 video (15–20 min, each member ≥ 4 min, six chapters) | missing; no script |
| Report PDF in `docs/`, video link at the top of README | missing |

## Deadline

No date in the brief, the proposal, the analysis or the repository. The brief says Day 0 and Day 14 are
"announced in class". Still unknown.

## Top gaps (in order)

1. ENG-2: no scaling run, so gate 5 (SF=1 ≤ 45 min) is unproven.
2. No report and no video script (H2, H3).
3. AN-2 and AN-5 have code but no committed results, so the report has no detection or backtest numbers.
4. ENG-3: no before/after Spark evidence (Samira).
5. R9 risk: one author, one day of commits.
6. ING-3 catalog registration missing; ING-6 streaming promised in the proposal but not built.
7. README misses prerequisites, the hidden-drop steps and the architecture summary.
8. Usability test with two non-CS users not done (Rimjhim); PRV-3 missing.

## Paused 2026-10-20 18:06

Paused for the group pack (BRIEF-SUBMIT.md, last section). Done before the pause: ING-1 generator path fix
(with test), ING-3 catalog step `pipeline/catalog.py` + `make catalog` (manual run registered 24 tables; its
test timed out at 300 s on this laptop, timeout raised, re-run pending), `scripts/scaling.sh` written, not run.
Resume at: run scaling.sh 0.25 0.5 1, then evaluation + backtest, then ING-6, ING-7, README.

## Group pack built 2026-10-20 (BRIEF-SUBMIT.md "group package first", amended by "Change 18:20")

`share/EpiScope-group-pack/`: `00-README.md`, `01-Status-and-tasks.pdf`, `02-Report-draft.pdf`
(built by `share/src/build.sh`). Change 18:20 moved the video out: 03-Video-plan, 04-Transcript-Shezan and
05-Transcript-template are not in the pack, and step 3.2 (transcript, video plan) is cancelled; a separate
agent builds slides and a script in ~/Projects/episcope-video. The README carries "Slides and script: <links
coming>". The drafts written before the change, `video/TRANSCRIPT-shezan.md` (654 words) and
`video/VIDEO-PLAN.md`, are left uncommitted in `video/` for shinzuu to pass on or delete. The ten review passes
now cover the report only.

## Progress after the pack (2026-10-20 19:30)

| ID | New status | Evidence |
|---|---|---|
| ING-3 | done | `pipeline/catalog.py`, `make catalog` (27 tables at SF=0.25); test passes |
| ING-6 | done, latency to measure | `pipeline/stream.py`, `make stream`; test passes (new file reaches gold and alerts) |
| ING-7 | done | `pipeline/gold/asof.py`, `make asof DATE=...`; test reproduces gold before a 21-day-late record |
| ENG-1 | done | README: prerequisites, hidden-drop steps, architecture |
| AN-2 | results committed | `docs/experiments/detection_tradeoff.json` (SF=0.25): EARS C2 sensitivity 0.97, 1.27 false alerts per district-year, median 8 days; CUSUM 0.91, 1.49, 9 days |
| AN-5 | results committed | `docs/experiments/backtest_metrics.csv` (SF=0.25): relative WIS 0.83 overall; MAE dengue 40.1 vs 43.6, diarrhoea 19.0 vs 26.3 |
| AN-7 | still degenerate | SF=0.25 with real weather: national net need 0 for beds, IV saline, NS1 kits and ORS; only antimalarials (8.8) need allocating. For Rafi |
| ENG-2 | running | `scripts/scaling.sh 0.25 0.5 1` started 19:30 after the thesis jobs ended |
| ENG-3, ENG-4 | left for Samira | decision 2026-10-20 (R9 is per person) |

Tests: `tests/test_pipeline.py` + `tests/test_generator.py` 23 passed (9 min 49 s).
