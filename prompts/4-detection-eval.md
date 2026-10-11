<task>
Build analytics/detection/ (AN-1, AN-3) and analytics/evaluation/ (AN-2): outbreak detection per district
and disease, at scale in Spark, and its evaluation against ground truth.
</task>

<requirements>
1. **Input.** gold.cases_weekly (docs/contracts.md). Until the real table exists, test on a small synthetic
   weekly DataFrame built in tests: 3 districts × 2 diseases × 3 years, with seasonality, two injected
   outbreaks and one silent-then-backfill artefact.
2. **Methods.** Two methods, in Spark with window functions or grouped applyInPandas:
   - EARS C2 on seasonally adjusted counts. research.md §3 explains why raw EARS fires at every season
     onset.
   - CUSUM.

   Output gold.alerts rows per the contract.
3. **Artefact handling (AN-3).** A facility-week with no reports counts as missing, not zero, using
   reporting_facilities / expected_facilities. Counts go by onset week. Show that the naive detector alerts
   on the artefact and ours does not.
4. **Evaluation (AN-2).** It is the only code allowed to read ground_truth/outbreaks.csv, and detection
   never reads it. Compute sensitivity, false alerts per district-year and median days to detection. Sweep
   the threshold to produce the C1 trade-off curve as data (CSV/JSON) for the report and the dashboard.
   Recommend an operating point at a stated false-alert budget.
5. **Tests** in tests/test_detection.py:
   - known outbreaks are detected;
   - the artefact is not flagged;
   - detection code never references ground_truth (grep-based test);
   - the metrics are correct on a hand-built example.
</requirements>

<decisions>
Already decided: EARS C2 is primary and CUSUM secondary; Farrington is excluded (research.md §3).
Your call: the seasonal adjustment method (STL residual or a seasonal baseline), CUSUM's k and h defaults,
and the sweep grid.
</decisions>
