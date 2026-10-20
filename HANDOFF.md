# Handoff (2026-10-20 21:20)

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

## Next, in order
1. Profile, fix the biggest cost, re-time SF=0.25 balanced (before/after), then SF=1 (own flock).
2. ING-6 latency: live `make stream`, then `make drop`, read docs/experiments/stream_latency.json.
3. Final hidden-drop drill on HEAD.
4. Report: SF=1 numbers, §4/§6 data parts; then ten passes (report only), versions in report/versions/.

## Decisions
- ENG-3, ENG-4 left for Samira. AN-7 degenerate allocation is Rafi's (net need 0 except antimalarials).
- `video/` never committed (slides agent reads it). Deadline unknown.
