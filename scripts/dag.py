"""Pipeline DAG with retries. Implements ENG-4 (the orchestration half; the tests are under tests/).

`make all` runs this. Each stage is a Makefile target; a stage runs only after every stage it depends on has
succeeded, and a failed stage is retried (default 2 retries, 30 s then 60 s apart) before the run stops.
Retrying is safe because every stage is idempotent: the generator replaces its output, bronze skips files it has
already loaded (checksums), silver MERGEs and gold rebuilds only the weeks that changed (ING-4).

A plain-Python DAG rather than Airflow or Prefect: Airflow needs 4-8 GB of RAM on a 16 GB laptop and Prefect
would be a new dependency for six stages (research.md §14).

Each run writes a JSON record (stage, attempts, seconds, status) to <lake>/_runs/dag-<UTC time>.json.

    python scripts/dag.py [--sf 0.25] [--seed 42] [--retries 2] [--only gold analytics] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

# stage -> stages it needs. The order of the dict is the run order (a valid topological order).
DAG = {
    "reference": [],
    "generate": ["reference"],
    "bronze": ["generate"],
    "silver": ["bronze"],
    "gold": ["silver"],
    "analytics": ["gold"],
    "serve": ["analytics"],
    "catalog": ["gold"],
}
BACKOFF_SECONDS = (30, 60, 120)


def order(dag=DAG, only=None):
    """Stages to run, dependencies first. `only` keeps the listed stages (their dependencies are assumed done)."""
    seen, out = set(), []

    def visit(s, path=()):
        if s in path:
            raise ValueError(f"cycle: {' -> '.join(path + (s,))}")
        if s in seen:
            return
        for d in dag[s]:
            visit(d, path + (s,))
        seen.add(s)
        out.append(s)

    for s in dag:
        visit(s)
    return [s for s in out if not only or s in only]


def make_command(stage, sf, seed):
    return ["make", "--no-print-directory", stage, f"SF={sf}", f"SEED={seed}"]


def run(stages, command, retries=2, sleep=time.sleep, runner=subprocess.call):
    """Run each stage with retries; stop at the first stage that still fails. Returns the per-stage records."""
    records = []
    for st in stages:
        rec = {"stage": st, "attempts": 0, "seconds": 0.0, "status": "failed"}
        t0 = time.time()
        for attempt in range(retries + 1):
            rec["attempts"] = attempt + 1
            print(f"[dag] {st}: attempt {attempt + 1} of {retries + 1}", flush=True)
            if runner(command(st)) == 0:
                rec["status"] = "ok"
                break
            if attempt < retries:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                print(f"[dag] {st} failed; retrying in {wait} s", flush=True)
                sleep(wait)
        rec["seconds"] = round(time.time() - t0, 1)
        records.append(rec)
        print(f"[dag] {st}: {rec['status']} after {rec['attempts']} attempt(s), {rec['seconds']} s", flush=True)
        if rec["status"] != "ok":
            break
    return records


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sf", default=os.environ.get("SF", "0.25"))
    p.add_argument("--seed", default=os.environ.get("SEED", "42"))
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--only", nargs="*", choices=list(DAG))
    p.add_argument("--dry-run", action="store_true", help="print the stage order and exit")
    a = p.parse_args(argv)
    stages = order(only=a.only)
    if a.dry_run:
        print(" -> ".join(stages))
        return 0
    records = run(stages, lambda s: make_command(s, a.sf, a.seed), a.retries)
    sys.path.insert(0, str(ROOT))
    from pipeline.bronze import lake

    lake.load_env()
    out = lake.lake_dir() / "_runs"
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"dag-{stamp}.json").write_text(json.dumps(
        {"sf": a.sf, "seed": a.seed, "retries": a.retries, "stages": records}, indent=2) + "\n")
    ok = len(records) == len(stages) and all(r["status"] == "ok" for r in records)
    print(f"[dag] {'all stages ok' if ok else 'stopped at ' + records[-1]['stage']}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
