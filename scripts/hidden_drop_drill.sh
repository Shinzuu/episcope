#!/usr/bin/env bash
# Hidden-drop drill. Implements the ENG-4 / ING-4 rehearsal of the instructor's test (ANALYSIS.md trap 1):
#   1. clone the repo to a temp dir (plus the working tree's uncommitted, non-ignored files, so the drill tests
#      what is about to be committed; DRILL_WORKTREE=0 tests HEAD only);
#   2. set up from the README (`make setup`; DRILL_SETUP=link reuses this checkout's .venv to save time);
#   3. full build: `make all SF=$SF SEED=$SEED`;
#   4. `make drop` with a NEW seed (a fresh 14-day drop the pipeline has never seen);
#   5. the incremental pipeline: `make bronze silver gold analytics serve`;
#   6. the same steps again (bronze silver gold), which must change nothing.
# Writes docs/experiments/hidden_drop_drill.json: {ok, summary, rows_added, duplicates_after_rerun, ...}.
#
# Two inputs are not produced by the README steps and are copied from this checkout, both stated in the JSON:
#   - data/reference/ (built from public downloads by pipeline/reference; data/ is git-ignored);
#   - a fresh random EPISCOPE_HASH_SALT in .env. `make setup` already writes a random salt; the drill replaces
#     it with its own, so the clone never shares a salt with this checkout. The salt is never printed.
#
# Usage: scripts/hidden_drop_drill.sh   (env: SF=0.05 SEED=42 DROP_SEED=<random> DRILL_SETUP=make|link
#                                        DRILL_TMP=/var/tmp KEEP=0)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SF="${SF:-0.05}"
SEED="${SEED:-42}"
DROP_SEED="${DROP_SEED:-$(( (RANDOM % 9000) + 1000 ))}"
DRILL_SETUP="${DRILL_SETUP:-make}"
DRILL_WORKTREE="${DRILL_WORKTREE:-1}"
OUT="$REPO/docs/experiments/hidden_drop_drill.json"
# /var/tmp, not /tmp: /tmp is often RAM-backed (tmpfs) and the clone holds a venv plus generated data
WORK="$(mktemp -d "${DRILL_TMP:-/var/tmp}/episcope-drill.XXXXXX")"
CLONE="$WORK/repo"
LOG="$WORK/drill.log"
export EPISCOPE_DRIVER_MEMORY="${EPISCOPE_DRIVER_MEMORY:-3g}"
export EPISCOPE_SPARK_MASTER="${EPISCOPE_SPARK_MASTER:-local[2]}"
STEP="start"
T0=$(date +%s)

fail() {
  local msg="$1"
  mkdir -p "$(dirname "$OUT")"
  python3 - "$OUT" "$msg" "$STEP" "$LOG" "$SF" "$SEED" "$DROP_SEED" <<'PY'
import json, sys, pathlib
out, msg, step, log, sf, seed, drop = sys.argv[1:8]
tail = pathlib.Path(log).read_text(errors="replace").splitlines()[-30:] if pathlib.Path(log).exists() else []
json.dump({"ok": False, "summary": f"FAILED at {step}: {msg}", "rows_added": None, "duplicates_after_rerun": None,
           "sf": float(sf), "seed": int(seed), "drop_seed": int(drop), "log_tail": tail},
          open(out, "w"), indent=2)
PY
  echo "drill FAILED at $STEP: $msg (log: $LOG)" >&2
  KEEP=1
  exit 1
}

cleanup() { if [ "${KEEP:-0}" != "1" ]; then rm -rf "$WORK"; else echo "drill workspace kept at $WORK" >&2; fi; }
trap cleanup EXIT

run() {  # run <step> <command...>: log everything, fail the drill on a non-zero exit
  STEP="$1"; shift
  echo "== $STEP: $*" | tee -a "$LOG"
  ( cd "$CLONE" && "$@" ) >>"$LOG" 2>&1 || fail "command failed: $*"
}

echo "drill workspace: $WORK (SF=$SF SEED=$SEED DROP_SEED=$DROP_SEED)"

# 1. clone ---------------------------------------------------------------------------------------------
STEP="clone"
git clone -q "$REPO" "$CLONE" >>"$LOG" 2>&1 || fail "git clone"
if [ "$DRILL_WORKTREE" = "1" ]; then
  ( cd "$REPO" && git ls-files -z --modified --others --exclude-standard ) \
    | ( cd "$REPO" && xargs -0 -r cp --parents -t "$CLONE" ) 2>>"$LOG" || fail "copying working-tree files"
  ( cd "$REPO" && git ls-files -z --deleted ) | ( cd "$CLONE" && xargs -0 -r rm -f ) 2>>"$LOG"
fi

# 2. setup ---------------------------------------------------------------------------------------------
if [ "$DRILL_SETUP" = "link" ]; then
  STEP="setup (linked venv)"
  ln -s "$REPO/.venv" "$CLONE/.venv" && cp "$CLONE/.env.example" "$CLONE/.env" || fail "linking .venv"
else
  run "setup" make setup
fi
STEP="salt"
SALT="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
sed -i "s/^EPISCOPE_HASH_SALT=.*/EPISCOPE_HASH_SALT=$SALT/" "$CLONE/.env" || fail "writing salt"
unset SALT
STEP="reference"
[ -f "$REPO/data/reference/districts.csv" ] || fail "no data/reference in $REPO (build pipeline/reference first)"
mkdir -p "$CLONE/data" && cp -r "$REPO/data/reference" "$CLONE/data/reference" || fail "copying data/reference"

# 3. full build ----------------------------------------------------------------------------------------
run "full build" make all SF="$SF" SEED="$SEED"

PROBE="$WORK/probe.py"
cat >"$PROBE" <<'PY'
"""Counts used by the drill (runs inside the clone)."""
import json, os, sys
os.environ.setdefault("EPISCOPE_SPARK_MASTER", "local[2]")
from pyspark.sql import functions as F
from pipeline.bronze import lake
spark = lake.spark_session("drill_probe")
try:
    s = lake.read(spark, "silver", "encounters")
    g = lake.read(spark, "gold", "cases_weekly")
    keys = ["district_code", "disease", "epi_year", "epi_week"]
    gold_rows = sorted(tuple(r) for r in g.drop("batch_ids").collect())
    import hashlib
    out = {
        "silver_rows": s.count(),
        "silver_duplicate_ids": s.groupBy("encounter_id").count().where("count > 1").count(),
        "gold_rows": g.count(),
        "gold_duplicate_keys": g.groupBy(*keys).count().where("count > 1").count(),
        "gold_hash": hashlib.sha256(repr(gold_rows).encode()).hexdigest(),
        "max_report_date": str(s.agg(F.max("report_date")).first()[0]),
        "bronze_batches": lake.read(spark, "bronze", "_ingest_log").select("batch_id").distinct().count(),
        "quarantined": lake.read(spark, "silver", "quarantine").count(),
    }
finally:
    spark.stop()
print("PROBE " + json.dumps(out))
PY
probe() {  # probe <name>: run the probe in the clone, save its JSON as $WORK/<name>.json
  STEP="probe $1"
  ( cd "$CLONE" && PYTHONPATH="$CLONE" env -u HADOOP_CONF_DIR -u HADOOP_HOME -u YARN_CONF_DIR mise x -- .venv/bin/python "$PROBE" ) \
    >"$WORK/$1.out" 2>>"$LOG" || fail "probe $1"
  grep '^PROBE ' "$WORK/$1.out" | sed 's/^PROBE //' >"$WORK/$1.json"
}
probe before

# 4. new drop with a different seed --------------------------------------------------------------------
run "drop" make drop SF="$SF" DROP_SEED="$DROP_SEED"

# 5. incremental pipeline ------------------------------------------------------------------------------
run "incremental" make bronze silver gold analytics serve
probe after

# 6. re-run: must change nothing -----------------------------------------------------------------------
run "rerun" make bronze silver gold
probe rerun

# export privacy check (same rule as scripts/verify.py gate 4)
STEP="export check"
python3 - "$CLONE/dashboard/data" >"$WORK/export.json" <<'PY' || fail "export check"
import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
weekly = json.loads((d / "weekly.json").read_text())
small = sum(1 for r in weekly if r["c"] is not None and 1 <= r["c"] <= 4)
size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
print(json.dumps({"small_counts": small, "bytes": size, "weeks": len({r["w"] for r in weekly}),
                  "last_week": max(r["w"] for r in weekly)}))
PY

# 7. verdict -------------------------------------------------------------------------------------------
STEP="verdict"
mkdir -p "$(dirname "$OUT")"
python3 - "$WORK" "$OUT" "$SF" "$SEED" "$DROP_SEED" "$(( $(date +%s) - T0 ))" "$DRILL_SETUP" <<'PY'

import json, pathlib, sys
work, out, sf, seed, drop, secs, setup = sys.argv[1:8]
w = pathlib.Path(work)
b, a, r = (json.loads((w / f"{n}.json").read_text()) for n in ("before", "after", "rerun"))
e = json.loads((w / "export.json").read_text())
rows_added = a["silver_rows"] - b["silver_rows"]
dups = r["silver_duplicate_ids"] + r["gold_duplicate_keys"]
checks = {
    "drop_ingested_as_new_batch": a["bronze_batches"] == b["bronze_batches"] + 1,
    "rows_added": rows_added > 0,
    "data_through_advanced": a["max_report_date"] > b["max_report_date"],
    "no_duplicates_after_rerun": dups == 0 and a["silver_duplicate_ids"] == 0,
    "rerun_changed_nothing": r["silver_rows"] == a["silver_rows"] and r["gold_hash"] == a["gold_hash"]
                             and r["bronze_batches"] == a["bronze_batches"],
    "export_has_no_small_counts": e["small_counts"] == 0,
    "export_under_10mb": e["bytes"] <= 10 * 1024 * 1024,
}
ok = all(checks.values())
summary = (f"{'PASS' if ok else 'FAIL'}: clean clone, SF={sf} seed {seed}, then a 14-day drop with seed {drop} ingested "
           f"unmodified: +{rows_added} silver rows, data through {b['max_report_date']} -> {a['max_report_date']}, "
           f"{dups} duplicates after re-run, export {e['bytes'] / 1e6:.2f} MB with no 1-4 counts, {int(secs) // 60} min")
res = {"ok": ok, "summary": summary, "rows_added": rows_added, "duplicates_after_rerun": dups, "checks": checks,
       "sf": float(sf), "seed": int(seed), "drop_seed": int(drop), "setup": setup, "seconds": int(secs),
       "before": b, "after": a, "rerun": r, "export": e,
       "inputs_not_from_readme": ["data/reference copied from the working checkout",
                                  "random EPISCOPE_HASH_SALT written to .env (replaces the random salt make setup writes)"]}
for k in ("before", "after", "rerun"):
    res[k].pop("gold_hash", None)
json.dump(res, open(out, "w"), indent=2)
print(summary)
sys.exit(0 if ok else 1)
PY
status=$?
echo "drill result written to $OUT"
if [ "$status" -ne 0 ]; then KEEP=1; exit 1; fi
