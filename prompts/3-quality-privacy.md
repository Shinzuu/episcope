<task>
Build two libraries the bronze→silver job will call:
- pipeline/quality/: data-quality rules, quarantine and the per-load report (DQ-1, DQ-2, DQ-3);
- pipeline/privacy/: de-identification and small-cell suppression (PRV-1, PRV-2, with PRV-4 helpers).

Don't build the bronze or silver jobs themselves.
</task>

<requirements>
1. **Rules.** At least 12, each a Spark column expression with a stable reason code (for example
   DQ_DUP_ENCOUNTER), a severity (quarantine or warn) and a requirement ID. Cover:
   - every injected defect in ANALYSIS.md §6 trap 4: duplicates, orphan facility, invalid age, invalid
     sex, onset > report, bad ICD-10;
   - schema/type checks, nulls, geography (district_code is one of the 64 pcodes);
   - S5/S6 sanity: negative stock, occupied beds over capacity.
2. **Splitting.** `apply_rules(df, rules, batch_id, source)` returns (clean_df, quarantine_df).
   - A quarantined row keeps its original columns plus reason_codes (an array, so a row is written once
     with every code), batch_id, source and quarantined_at.
   - Warn rows stay in the clean output and are counted.
   - Duplicates: keep the latest report_date and quarantine the rest.
3. **Report.** `dq_report(...)` returns a one-row DataFrame matching gold.dq_report in docs/contracts.md.
4. **Hashing.** HMAC-SHA256 of patient_id, built from Spark's sha2 with the inner/outer pad construction
   (research.md §9), with no Python UDF. The key is read from the environment variable EPISCOPE_HASH_SALT.
   Fail loudly if it is unset. Also add an age_band function.
5. **Suppression.** `suppress(df, count_col, group_cols, threshold=5)` turns counts of 1–4 into null with
   suppressed=true. It then applies complementary suppression, so that within each row and column total no
   single hidden cell can be recovered by subtraction.
6. **k-anonymity.** `k_anonymity(df, quasi_identifiers)` returns the minimum k, for PRV-4.
7. **Tests** in tests/test_quality.py and tests/test_privacy.py:
   - each rule has a failing row and a passing edge-case row (age 0, age 120);
   - applying twice is idempotent;
   - the hash is stable and keyed (a different salt gives a different hash);
   - no patient_id survives in the outputs;
   - a subtraction-recovery attempt on a suppressed table fails.
</requirements>

<decisions>
Your call: the module layout, the ICD-10 regex and the age validity range (default 0–120).
</decisions>
