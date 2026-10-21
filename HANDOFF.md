# Handoff (2026-10-21 11:55)

Working from `BRIEF-SUBMIT.md` (untracked, written by shinzuu's assistant Lilith). House rule since 19:35: every
heavy job runs as `flock ~/.local/state/lilith/heavy.lock <command>`; at most one browser, closed after use.

## Done
- Audit `SUBMIT-AUDIT.md` (with progress tables). Group pack in `share/EpiScope-group-pack/` (00 README with
  slide/script links, 01 status, 02 report draft); names: Tahsin Ahmed (Rafi) in formal places.
- Committed: ING-1 path fix, ING-3 catalog, ING-6 `pipeline/stream.py`, ING-7 `pipeline/gold/asof.py`, README
  (ENG-1), scaling harness (ENG-2, bc fixed), SF=0.25 results (detection, backtest, allocation, calendar).
- Tests: test_pipeline + test_generator 23 passed after ING-3/6/7.
- Main checkout lake = SF=0.25 seed 42 with real weather (built 18:22-19:09, complete).

## Power loss 19:27
- Killed: `scripts/scaling.sh` in SF=0.25 silver (its timings were empty anyway: `bc` missing). Workspace
  /var/tmp/episcope-scale-sf0.25.* deleted; log kept as /var/tmp/episcope-runs/scaling-killed-1927.log.
- Nothing else was running. Derby `.lck` files in lake/_catalog are normal (Spark never shuts Derby down).

## 19:36 second stop
- Restarted scaling was stopped by the power guard (battery 12%, `power-guard.state` paused=1): Spark
  context shut down in SF=0.25 bronze. Workspace deleted. A waiter (background) starts scaling under flock
  once `power-guard.state` shows paused=0. If it trips again mid-run, the guard log says so.

- Power guard live (`bin/lilith-power-guard`, every 30 s): jobs on heavy.lock get SIGSTOP until battery >= 35%
  and charging, then resume by themselves. State T = paused on purpose: do not kill, restart or clear the lock.
  Light work only during the pause.

- During the pause (light work): report §1, §2, §5, §6 (SF=0.25 numbers, provisional), §8, appendix A outline,
  architecture figure; 6 pages, 0 warnings. LaTeX builds (seconds) ran outside flock.

## 21:15 scaling plan change (Lilith)
- SF=0.25 done in power-saver: 58.6 min (generate 44 s, bronze 336, silver 986, gold 285, analytics 1713, serve 152).
  Not a fair timing; power-saver now off for timing runs (flag ~/.local/state/lilith/power-saver-off).
- Chain cut at 21:13: killed the `bash -c` parent only, so SF=0.5 keeps running and SF=1 will not auto-start.
  The orphaned scaling.sh still holds heavy.lock (inherited fd) until SF=0.5 ends. SF=0.5 timing is partly
  power-saver.
- Queued (task bji4zlj2q): after SF=0.5, copy scaling.json to /var/tmp/episcope-runs/scaling-powersaver.json,
  then `flock ... env KEEP=1 EVENT_LOG=1 scripts/scaling.sh 0.25` = balanced baseline + profile (run_all
  per-module times, Spark event log). Log /var/tmp/episcope-runs/profile-sf0.25.log. One flock per run.
- Code read so far: backtest/forecast cost is per district series (~6.7k NB fits), so analytics should be near
  constant across SF; silver scales with rows. Silver candidates: the flagged plan (incl. the DQ duplicate
  window) is recomputed for quarantine write, its count(), dq_report and the clean merge. Fix only if outputs
  stay identical; anything that changes results goes to shinzuu first.

## 22:05 profile and silver fix
- SF=0.5 (partly power-saver): 34.8 min (generate 60 s, bronze 457, silver 995, gold 125, analytics 406, serve 44).
- SF=0.25 balanced, before fix: 17.1 min (generate 15, bronze 114, silver 329, gold 99, analytics 425, serve 48).
  Analytics is near constant across SF (detection 55, forecast 35, backtest 310, rest 25 s); silver scales.
  Workspace kept: /var/tmp/episcope-scale-sf0.25.p2THK5 (holds event logs with the padded key: delete later).
- Silver fix (engine.flag_rows; quarantine count from the written slice; changed weeks checkpointed): Spark
  app 325 s -> 214 s, all silver tables identical (fingerprint). Evidence docs/experiments/silver_optimisation.json.
  test_quality 39 passed (one new equivalence test). test_pipeline + test_privacy 26 passed.
- For Samira (ENG-3): this before/after is usable evidence; backtest (310 s, constant) is the next cost
  (local[4] on 8 threads, 64 district groups); AQE skew/salting still to show.

## 22:40 re-time and SF=1
- SF=0.25 re-timed after the silver fix (balanced): 15.4 min (generate 15, bronze 113, silver 229, gold 111,
  analytics 414, serve 45), against 17.1 min before the fix and 58.6 min in power-saver.
- SF=1 first try failed in silver after 10.3 min (generate 29 s, bronze 231): `java.io.IOException: Disk quota
  exceeded`. Spark spills to /tmp, a RAM tmpfs with usrquota. Fixed: EPISCOPE_SPARK_LOCAL_DIR (spark.py,
  .env.example), scaling.sh sets it to $WORK/spark-local. Failed workspace deleted.
- SF=1 re-run started 22:40 (task buj3uofy8), KEEP=1 EVENT_LOG=1, log /var/tmp/episcope-runs/scaling-sf1.log.
  Kept workspace holds event logs with the padded key: delete after the Spark UI screenshots.

## 09:40 SF=1 suspended mid-run
- SF=1 (22:34 start) finished "ok" but the laptop suspended 22:52:10 to 09:24:56 (37,966 s, systemctl from the
  user session) during analytics.detection. Stages: generate 30.6, bronze 240.7, silver 613.3, gold 185.9,
  analytics 38503.8 wall (about 538 s minus the suspend), serve 55.3; about 27.7 min without the suspend. Not
  used as the gate number. Saved: /var/tmp/episcope-runs/scaling-sf1-suspended.json and -workspace.log;
  workspace deleted (event logs held the padded key).
- Clean SF=1 re-run started 09:40 (task b6x925ky6), KEEP=1 EVENT_LOG=1. If it suspends again, ask shinzuu
  about a sleep inhibitor (systemd-inhibit) for timing runs.

## 10:05 SF=1 clean: gate 5 met
- SF=1 09:35-10:03, no suspend: 28.3 min (generate 36.9, bronze 256.3, silver 663.7, gold 209.2, analytics
  480.3, serve 52.7), 34,136,930 encounter rows, raw 3.9 GB, silver 2.7 GB, dashboard data 9 MB. Gate 5 PASS
  (limit 45). Analytics: detection 56, forecast 32, backtest 365 s.
- Workspace kept for Samira's Spark UI evidence (ENG-3): /var/tmp/episcope-scale-sf1.MX1zs5 (event logs hold
  the padded key: never copy into the repo; delete after her screenshots).
- SF=0.5 re-timed: 20.4 min. All three points balanced mode, after the silver fix (commits 89eea98, 01534b6,
  f1fc38d differ only in docs and the spill-folder option). scaling.json committed.

## 10:35 SF=1 recorded (Lilith/shinzuu: no further SF=1 run)
- scaling.json holds both SF=1 runs: the 22:34 run with timing_valid=false and the suspend note, and the
  09:35-10:03 run (MX1zs5) with timing_valid=true, 28.3 min. verify.py gate 5 skips invalid runs: PASS.
  scaling.sh keeps invalid entries when it replaces an SF. Ask shinzuu before any new SF=1 run.

## Next, in order
1. ING-6 measured at SF=1 (kept workspace, main checkout untouched): drop written 10:30:27, alerts refreshed
   10:35:28, latency 301.5 s (bronze 32.2, silver 117.6, gold 53.6, alerts 32.2, export 22.1 s; the rest is
   the 60 s trigger wait). Target under 5 min missed by 1.5 s. Lilith: default trigger now 30 s (f3dcb33);
   second SF=1 run with 30 s: 80 files, 219.2 s (bronze 29.1, silver 96.1, gold 41.1, alerts 25.3, export
   17.8 s), under 5 min. Both runs in stream_latency.json and report §4.
2. Drill on HEAD (4cddbb9, DRILL_WORKTREE=0, SF=0.05, make setup) PASS: drop seed 3006, +8,741 silver rows,
   through 2026-01-14, 0 duplicates after re-run, export 9.78 MB, 38 min. Committed (d612084); report §4 updated.
3. Names (shinzuu): Tahsin Ahmed Rafi and Rimjhim Dey in formal places (report title block, appendix
   reflections line, status PDF header and member headings, 00-README team line); short names in task lists.
   Edits made; PDFs rebuilt 11:45 and checked (full names in title block, status header and member headings,
   README team line; short names in task rows and teammate boxes; no old spellings).
   Report 6 pages, 0 warnings (s1 table ragged columns, two long paths reworded, \raggedbottom).
4. Report §4 (latency, drill) and §6 (scaling table, silver fix) committed.
5. SF=1 backtest/allocation/calendar outputs saved in docs/experiments/sf1/ (backtest rel. WIS 0.838 overall;
   allocation still degenerate at SF=1: alpha 0 recommended, Gini 0.98, for Rafi). Detection at SF=1 not
   evaluated (scaling does not run `make evaluate`; the kept workspace now holds two extra drops).
   BDA-NEEDS-SHINZUU asked: new clean SF=1 build + evaluate (~35 min) for the report, or keep SF=0.25.
6. Report: SF=1 numbers, §4/§6 data parts; then ten passes (report only), versions in report/versions/.

## Decisions
- ENG-3, ENG-4 left for Samira. AN-7 degenerate allocation is Rafi's (net need 0 except antimalarials).
- `video/` never committed (slides agent reads it). Deadline unknown.
