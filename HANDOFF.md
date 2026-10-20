# Handoff (2026-10-20 19:40)

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

## Next, in order
1. Scaling SF 0.25, 0.5, 1 under flock (SF=1 with event log, workspace kept) -> docs/experiments/scaling.json.
2. ING-6 latency: live `make stream`, then `make drop`, read docs/experiments/stream_latency.json.
3. Final hidden-drop drill on HEAD.
4. Report: SF=1 numbers, §4/§6 data parts; then ten passes (report only), versions in report/versions/.

## Decisions
- ENG-3, ENG-4 left for Samira. AN-7 degenerate allocation is Rafi's (net need 0 except antimalarials).
- `video/` never committed (slides agent reads it). Deadline unknown.
