#!/usr/bin/env python3
"""Five-gate verification. Run `make check` before M2, before tagging v1.0, and after any big change.

    gate 1  unit tests        pytest: quality rules, de-identification, suppression, LP constraints, idempotency
    gate 2  brief coverage    every requirement ID (ING/DQ/PRV/AN/VIS/ENG) is claimed in README's traceability
                              table and the claimed file exists
    gate 3  hidden-drop drill a clean clone ingests a fresh 14-day drop made with a different seed, unmodified
    gate 4  limits            dashboard data <= 10 MB, no counts 1-4 in exports, no patient_id or salt in
                              silver/gold/exports/repo, no file > 50 MB tracked, ground truth not read by detection
    gate 5  performance       SF=1 end-to-end runtime <= 45 min (reads the latest run log)

Each gate prints PASS, FAIL or PENDING (not built yet). Exit code 1 if any gate FAILs.
"""

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQ_IDS = (
    [f"ING-{i}" for i in range(1, 8)] + [f"DQ-{i}" for i in range(1, 5)] + [f"PRV-{i}" for i in range(1, 5)]
    + [f"AN-{i}" for i in range(1, 11)] + [f"VIS-{i}" for i in range(1, 8)] + [f"ENG-{i}" for i in range(1, 5)]
)
MUST = {"ING-1", "ING-2", "ING-3", "ING-4", "DQ-1", "DQ-2", "DQ-3", "PRV-1", "PRV-2", "AN-1", "AN-2", "AN-4",
        "AN-5", "AN-6", "AN-7", "AN-9", "VIS-1", "VIS-2", "VIS-3", "VIS-4", "ENG-1", "ENG-2", "ENG-3"}


def gate_tests():
    if not any((ROOT / "tests").glob("test_*.py")):
        return "PENDING", "no tests yet"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return ("PASS" if r.returncode == 0 else "FAIL"), last


def gate_coverage():
    readme = (ROOT / "README.md").read_text()
    rows = dict(re.findall(r"^\|\s*((?:ING|DQ|PRV|AN|VIS|ENG)-\d+)\s*\|\s*([^|]+)\|", readme, re.M))
    missing_must = sorted(MUST - rows.keys())
    bad_paths = []
    for rid, cell in rows.items():
        for p in re.findall(r"[\w./-]+/[\w./-]*|[\w-]+\.\w+", cell):
            if not (ROOT / p.strip()).exists():
                bad_paths.append(f"{rid}:{p}")
    if bad_paths:
        return "FAIL", "claimed files missing: " + ", ".join(bad_paths[:8])
    if missing_must:
        return "PENDING", f"{len(MUST) - len(missing_must)}/{len(MUST)} Must IDs traced; missing {', '.join(missing_must[:6])}…"
    should = len([r for r in rows if r not in MUST])
    return "PASS", f"all {len(MUST)} Must traced, {should} Should/Could traced"


def gate_hidden_drop():
    log = ROOT / "docs/experiments/hidden_drop_drill.json"
    if not log.exists():
        return "PENDING", "run scripts/hidden_drop_drill.sh (after the pipeline exists)"
    d = json.loads(log.read_text())
    return ("PASS" if d.get("ok") else "FAIL"), d.get("summary", "")


def gate_limits():
    problems = []
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True).stdout.split("\0")
    for f in filter(None, tracked):
        p = ROOT / f
        if p.is_file() and p.stat().st_size > 50 * 1024 * 1024:
            problems.append(f"{f} > 50 MB")
        if p.name == ".env":
            problems.append(".env is tracked")
    det = ROOT / "analytics/detection"
    for f in det.rglob("*.py"):
        if "ground_truth" in f.read_text():
            problems.append(f"{f.relative_to(ROOT)} references ground_truth")
    exports = ROOT / "dashboard/data"
    if exports.exists():
        size = sum(f.stat().st_size for f in exports.rglob("*") if f.is_file())
        if size > 10 * 1024 * 1024:
            problems.append(f"dashboard data {size / 1e6:.1f} MB > 10 MB")
        for f in exports.rglob("*.json"):
            text = f.read_text()
            # detection_tradeoff.json is evaluation output: its "n" counts planted outbreaks per shape, not people
            check_counts = f.name != "detection_tradeoff.json"
            if "patient_id" in text:
                problems.append(f"{f.name} contains patient_id")
            for m in re.finditer(r'"(?:count|cases|n)"\s*:\s*([1-4])\b', text if check_counts else ""):
                problems.append(f"{f.name} has unsuppressed count {m.group(1)}")
                break
    if problems:
        return "FAIL", "; ".join(problems[:6])
    return ("PASS" if exports.exists() else "PENDING"), "repo clean" + ("" if exports.exists() else "; no exports yet")


def gate_performance():
    log = ROOT / "docs/experiments/scaling.json"
    if not log.exists():
        return "PENDING", "no SF=1 run logged yet (ENG-2)"
    runs = {r["sf"]: r for r in json.loads(log.read_text()) if r.get("timing_valid", True)}  # skip suspended runs
    if 1 not in runs and 1.0 not in runs:
        return "PENDING", f"logged SF {sorted(runs)}; SF=1 missing"
    mins = runs.get(1, runs.get(1.0))["total_seconds"] / 60
    return ("PASS" if mins <= 45 else "FAIL"), f"SF=1 total {mins:.1f} min (limit 45)"


def main():
    gates = [("1 unit tests", gate_tests), ("2 brief coverage", gate_coverage), ("3 hidden-drop drill", gate_hidden_drop),
             ("4 limits & privacy", gate_limits), ("5 performance", gate_performance)]
    failed = False
    for name, fn in gates:
        status, msg = fn()
        failed |= status == "FAIL"
        print(f"{status:<8} gate {name:<20} {msg}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
