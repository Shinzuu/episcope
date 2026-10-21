# Final numbers (for the report, slides and script)

Every headline number, the value used, and the file it comes from. Final clean SF=1 build: seed 42, real
weather, commit 1acb5e1 (allocation capped at need), 2026-10-21 (`docs/experiments/sf1_final_run.json`), unless a row says otherwise.

| Topic | Number | Source |
|---|---|---|
| Encounter rows at SF=1 | 34,136,930 | `docs/experiments/sf1_final_run.json` (`rows`), `gold.dq_report` |
| Quarantined at SF=1 | 1,367,440 (4.0%) | `docs/experiments/sf1_final_run.json` |
| SF=1 run time, final code | 25.0 min (generate 0.6, bronze 4.3, silver 11.0, gold 3.3, analytics 4.9, serve 0.9) | `docs/experiments/sf1_final_run.json` |
| SF=1 timing run (gate 5) | 28.3 min, PASS (limit 45) | `docs/experiments/scaling.json` (commit f1fc38d) |
| SF=0.25 / 0.5 run time | 15.4 / 20.4 min | `docs/experiments/scaling.json` |
| Silver optimisation | 325 s to 214 s Spark time at SF=0.25, outputs identical | `docs/experiments/silver_optimisation.json` |
| Backtest optimisation | 325 s to 165 s at SF=0.25 (1 task to 64), scores identical | `docs/experiments/backtest_optimisation.json` |
| EARS C2 (threshold 4.0) | 33 of 35 outbreaks, sensitivity 0.94, 1.89 false alerts per district-year, median 8 days, PPV 0.15, 91% within 14 days | `docs/experiments/detection_tradeoff.json` |
| CUSUM (k 0.5, h 6.5) | 32 of 35, sensitivity 0.91, 1.82 false alerts per district-year, median 9.5 days, PPV 0.25 | `docs/experiments/detection_tradeoff.json` |
| Backtest, overall | relative WIS 0.84; MAE 113 vs 133; 80% coverage 0.79 vs 0.77 | `docs/experiments/backtest_metrics.csv` |
| Backtest by disease | dengue relative WIS 0.89, diarrhoea 0.72 | `docs/experiments/backtest_metrics.csv` |
| Allocation, half of B_ref | LP 67.8% of need met vs 34.4% population split; recommended alpha 0 | `docs/experiments/allocation_summary.json` |
| Equity floor (capped at need) | no scenario sends more than need (excess cost 0); at half of B_ref alpha 0.1-0.9 costs 2.5 points (65.3%), alpha 1 costs 1.5; at 0.25 and 0.75 of B_ref under 1 point | `docs/experiments/allocation_curve.csv` |
| B_ref (cost of all need) | 7.78 million BDT | `docs/experiments/allocation_summary.json` |
| National net need | IV saline 43,558; ORS 513,721; antimalarials 691; NS1 and beds 0 | `docs/experiments/allocation_summary.json` |
| Allocation before/after fix (SF=0.25) | top-5 share 1.00 to 0.22; Gini 0.98 to 0.27 | `docs/experiments/allocation_diagnosis.md` |
| SEIR | R0 2.04 (generator 1.8), R2 0.91; 40% contact cut at detection: peak -87%, cases -63%; two weeks late: -80%, -53% | `docs/experiments/seir_scenarios.json` |
| DQ-4 at SF=1 | 8,499 pairs flagged, 8,493 true (precision 0.999), recall in window 0.11 | `docs/experiments/dq4_patient_dedup.json` |
| ING-6 latency at SF=1 | 60 s trigger: 301.5 s; 30 s trigger (default): 219.2 s | `docs/experiments/stream_latency.json` |
| Hidden-drop drill | PASS on the final code: seed 4675, +8,888 rows, 0 duplicates, 9.76 MB, 1,717 s | `docs/experiments/hidden_drop_drill.json` |
| Dashboard export | under 10 MB; district bundles at most 0.74 MB each | `make serve` output |
| Persona walkthroughs (simulated, AI agents) | round 1: 0 of 15 tasks done; round 2: 13 of 15; 48 problems, 26 fixed, 20 partly, 2 not | `docs/usability/` |
