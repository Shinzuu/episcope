<task>
Build the lakehouse jobs that join the finished components into one pipeline:
- pipeline/bronze/ (ING-1);
- pipeline/silver/ (ING-2, ING-4, DQ-1 to DQ-4, PRV-1);
- pipeline/gold/ (ING-3, DQ-3);
- serving/export.py (VIS-4, PRV-2).

Then make the full `make all` run green on SF=0.05 and write the hidden-drop drill.
</task>

<context>
These are already built, tested and committed. Read them; don't rewrite them:
- generator/: `make generate SF=… SEED=…`; `make drop` writes a 14-day incremental drop;
- pipeline/quality (apply_rules, dq_report, with_facility_lookup);
- pipeline/privacy (hmac_sha256, deidentify, suppress, audit_suppression);
- analytics/ (run_all, evaluation);
- dashboard/ (reads dashboard/data/*.json; serving/mock_data.py shows the exact shapes).

Analytics expects gold tables as Delta at `<EPISCOPE_LAKE_DIR>/gold/<name>`. Follow that, and the table
schemas in docs/contracts.md.

Another worker is still downloading real weather into data/reference/weather_daily.parquet. If it's
missing, the generator falls back to a synthetic climate. Your code must work either way.
</context>

<requirements>
1. **Bronze.** Ingest data/raw S4/S5/S6 and data/reference into Delta without modifying the data. Add
   source, load_time, batch_id and file checksum. Discover files by glob, and never re-ingest a file whose
   checksum is already loaded.
2. **Silver.**
   - Clean facilities first, then `with_facility_lookup`, then `apply_rules` on the encounters and stock.
   - Write quarantined rows (de-identified with `deidentify(keep_age=True)`) to silver.quarantine.
   - De-identify clean rows. Map ICD-10 to the disease buckets: dengue, diarrhoea, ili, malaria and other.
   - MERGE into silver.encounters on encounter_id, keeping the latest report_date. Re-running the same batch
     changes nothing (ING-4), and records up to 21 days late update their onset weeks.
   - DQ-4 (should-have): de-duplicate patients across facilities (same patient_key, compatible attributes,
     within 7 days). Measure its precision against the generator's injected duplicates.
3. **Gold.** Build cases_weekly, weather_weekly, stock_latest and dq_report per contracts.md, partitioned
   by epi_year. Carry batch_ids for lineage (DQ-3). On an incremental run, rebuild only the affected
   epi-weeks.
4. **Serving.** Export the dashboard JSON in the mock's exact shapes, from the real gold and analytics
   tables.
   - Apply `suppress` with complementary suppression across disease and time.
   - Null `stat` on suppressed alert weeks.
   - Cap history so the total stays under 10 MB.
   - Copy the evaluation trade-off files in, if they exist.
5. **Wiring.** Bronze, silver and gold each get a module entry point matching the Makefile targets
   (pipeline.bronze.ingest, pipeline.silver.build, pipeline.gold.build, serving.export). Test that
   `make all SF=0.05` runs end to end.
6. **Hidden-drop drill** (scripts/hidden_drop_drill.sh). Clone the repo to a temp dir, set up from the
   README, run a full build, then `make drop` with a new seed, then the incremental pipeline. Write
   docs/experiments/hidden_drop_drill.json with `{ok, summary, rows_added, duplicates_after_rerun}`. The
   drill must pass.
7. **Tests** in tests/test_pipeline.py on tiny data:
   - re-running is idempotent (no duplicates);
   - late records update the right epi-week;
   - lineage goes from gold back to bronze batch IDs;
   - no patient_id exists past bronze;
   - exports contain no counts from 1 to 4.
</requirements>

<decisions>
Already decided: Delta, the contract schemas, and ISO epi-week via `extract(YEAROFWEEK FROM …)`.
Your call: the partition keys beyond epi_year, with a one-line reason in the module docstring.
Ask me before: changing any committed module's public function signatures.
</decisions>
