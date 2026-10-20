#!/usr/bin/env bash
# Scalability experiment (ENG-2): the full pipeline at each scale factor, timed per stage, from a fresh clone.
#
# For every SF: clone the repo into a temp dir, link this checkout's .venv, write a random salt, then run the
# README stages one by one (generate, bronze, silver, gold, analytics, serve) and time each. Output sizes
# (raw data, each lake layer, dashboard data) and the hardware are recorded too. Results are merged into
# docs/experiments/scaling.json (one entry per SF; a re-run replaces that SF's entry), which gate 5 of
# `make check` reads.
#
# Optional: EVENT_LOG=1 turns on the Spark event log (into the work dir) for Spark UI evidence. The silver job's
# plan holds the padded HMAC key (pipeline/privacy/deid.py), so event logs stay outside the repo and are deleted
# with the work dir unless KEEP=1.
#
# Usage: scripts/scaling.sh 0.25 0.5 1   (env: SEED=42 DRILL_TMP=/var/tmp KEEP=0 EVENT_LOG=0
#                                         EPISCOPE_DRIVER_MEMORY=5g EPISCOPE_SPARK_MASTER=local[4])
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED="${SEED:-42}"
OUT="$REPO/docs/experiments/scaling.json"
export EPISCOPE_DRIVER_MEMORY="${EPISCOPE_DRIVER_MEMORY:-5g}"
export EPISCOPE_SPARK_MASTER="${EPISCOPE_SPARK_MASTER:-local[4]}"
STAGES=(generate bronze silver gold analytics serve)
[ $# -gt 0 ] || { echo "usage: $0 SF [SF...]" >&2; exit 2; }

for SF in "$@"; do
  WORK="$(mktemp -d "${DRILL_TMP:-/var/tmp}/episcope-scale-sf$SF.XXXXXX")"
  CLONE="$WORK/repo"
  LOG="$WORK/scaling.log"
  echo "SF=$SF: workspace $WORK"
  git clone -q "$REPO" "$CLONE" || exit 1
  ln -s "$REPO/.venv" "$CLONE/.venv"
  sed "s/^EPISCOPE_HASH_SALT=.*/EPISCOPE_HASH_SALT=$(python3 -c 'import secrets;print(secrets.token_hex(32))')/" \
    "$CLONE/.env.example" >"$CLONE/.env"
  export EPISCOPE_SPARK_LOCAL_DIR="$WORK/spark-local"   # spill on disk, not in a /tmp tmpfs; deleted with $WORK
  if [ "${EVENT_LOG:-0}" = "1" ]; then
    mkdir -p "$WORK/spark-events"
    export EPISCOPE_EVENT_LOG_DIR="$WORK/spark-events"
  fi
  TIMES="$WORK/times.tsv"
  : >"$TIMES"
  status=ok
  for st in "${STAGES[@]}"; do
    t0=$(date +%s.%N)
    echo "== $(date '+%F %T') $st" | tee -a "$LOG"
    if ! ( cd "$CLONE" && make "$st" SF="$SF" SEED="$SEED" ) >>"$LOG" 2>&1; then
      status="failed at $st"
      echo "SF=$SF failed at $st (log: $LOG)" >&2
    fi
    t1=$(date +%s.%N)
    printf '%s\t%s\n' "$st" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b - a}')" >>"$TIMES"
    [ "$status" = ok ] || break
  done
  python3 - "$OUT" "$SF" "$SEED" "$TIMES" "$CLONE" "$status" <<'PY'
import json, os, pathlib, platform, subprocess, sys
out, sf, seed, times, clone, status = sys.argv[1:7]
clone = pathlib.Path(clone)

def du(p):
    p = clone / p
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0

stages = {}
for line in pathlib.Path(times).read_text().splitlines():
    k, v = line.split("\t")
    stages[k] = round(float(v), 1)
manifest = clone / "data/raw/_manifest.json"
m = json.loads(manifest.read_text()) if manifest.exists() else {}
cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), platform.machine())
mem_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
entry = {
    "sf": float(sf), "seed": int(seed), "status": status,
    "stage_seconds": stages, "total_seconds": round(sum(stages.values()), 1),
    "encounter_rows": m.get("encounter_rows"), "stock_rows": m.get("stock_rows"), "facilities": m.get("facilities"),
    "bytes": {"raw": du("data/raw"), "bronze": du("lake/bronze"), "silver": du("lake/silver"),
              "gold": du("lake/gold"), "dashboard_data": du("dashboard/data")},
    "hardware": {"cpu": cpu, "threads": os.cpu_count(), "ram_gb": mem_gb, "os": platform.platform()},
    "spark": {"master": os.environ["EPISCOPE_SPARK_MASTER"], "driver_memory": os.environ["EPISCOPE_DRIVER_MEMORY"]},
    "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=clone, capture_output=True,
                             text=True).stdout.strip(),
}
p = pathlib.Path(out)
runs = [r for r in (json.loads(p.read_text()) if p.exists() else []) if r["sf"] != float(sf)]
runs = sorted(runs + [entry], key=lambda r: r["sf"])
p.write_text(json.dumps(runs, indent=2) + "\n")
print(f"SF={sf}: {status}, total {entry['total_seconds'] / 60:.1f} min, stages {stages}")
PY
  if [ "${KEEP:-0}" != "1" ] && [ "$status" = ok ]; then rm -rf "$WORK"; else echo "kept $WORK"; fi
done
