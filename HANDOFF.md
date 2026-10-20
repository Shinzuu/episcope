# Handoff (2026-10-20 18:30)

Working from `BRIEF-SUBMIT.md` (untracked, written by shinzuu's assistant Lilith).

## Done
- Step 1 audit: `SUBMIT-AUDIT.md`. `make check` at start: gates 1, 3, 4 PASS (141 tests, 34 min); 2 and 5 PENDING.
- Fixes committed: ING-1 generator paths follow `EPISCOPE_DATA_DIR`; ING-3 Hive catalog (`pipeline/catalog.py`,
  `make catalog`, 24 tables registered); ENG-2 harness `scripts/scaling.sh` (not run yet).
- Group pack (priority change 18:06, amended 18:20): `share/EpiScope-group-pack/` with 00-README, 01 status,
  02 report draft. Video items removed per Change 18:20; step 3.2 cancelled.
- Report skeleton `report/` (IEEEtran vendored, latexmk builds; 4 pages; §3 and §4 written, others in boxes).

## Open
- Catalog test `test_catalog_registers_every_table` timed out at 300 s once (laptop slow); timeout now 900 s,
  not re-run.
- Scaling run SF 0.25/0.5/1 waits: three `depth/fill.py` jobs (another agent) hold ~4 GB, which would distort
  timings. Asked shinzuu.
- Then: evaluation + backtest on the final data, ING-6 streaming, ING-7 as-of reader, README (prerequisites,
  hidden-drop steps, architecture), final drill, then the ten report passes (report only now).
- Deadline: not found anywhere.
- Uncommitted on purpose: `video/TRANSCRIPT-shezan.md`, `video/VIDEO-PLAN.md` (cancelled by Change 18:20).
