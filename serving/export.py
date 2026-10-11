"""Export the dashboard JSON from the real gold and analytics tables. Implements VIS-4 and PRV-2.

`python -m serving.export [--out dashboard/data] [--max-mb 10]`

Writes the files of docs/contracts.md "Serving" in exactly the shapes serving/mock_data.py produces:
meta, weekly, alerts, forecasts, allocation, calendar, dq. Only aggregates leave the lake (VIS-4).

Privacy (PRV-2):
- weekly counts go through pipeline.privacy.suppress with threshold 5: every count 1-4 is hidden, then
  complementary cells are hidden until no line holds exactly one hidden cell. The lines are both directions
  of each district's disease x week table: across diseases within a week, and across weeks within a disease
  (time), so neither a weekly all-disease total nor a period total can expose a hidden cell by subtraction.
  Incidence is nulled with its count (suppress_derived), and audit_suppression must report zero leaks or the
  export fails.
- alerts: `stat` is null on every suppressed district-disease-week (the statistic would reveal the count).

Size: the dashboard budget is 10 MB in total. The fixed-size files are written first; weekly and alerts then
keep the most recent N weeks, with N the largest that fits (estimated, then checked and reduced). Suppression
runs on exactly the exported window, because its lines are the published cells.

If the evaluation trade-off file (detection_tradeoff.json, written by `make evaluate`) exists, it is copied in
too. Nothing here hard-codes a date or a row count.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import shutil
import sys

from pyspark.sql import functions as F

from pipeline.bronze import lake
from pipeline.privacy import audit_suppression, suppress
from pipeline.silver.transform import DISEASES, week_start_of

ROOT = lake.ROOT
THRESHOLD = 5
TRADEOFF_FILES = ("detection_tradeoff.json",)


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return v


def _num(v, nd=3):
    v = _clean(v)
    return None if v is None else round(float(v), nd)


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False)


# ------------------------------------------------------------------------------------------------ fixed files

def population(spark) -> dict:
    if lake.exists("silver", "districts"):
        rows = lake.read(spark, "silver", "districts").select("district_code", "population_2022").collect()
    else:
        import csv

        with (lake.data_dir() / "reference" / "districts.csv").open() as fh:
            rows = [{"district_code": r["district_code"], "population_2022": r["population_2022"]}
                    for r in csv.DictReader(fh)]
    return {r["district_code"]: float(r["population_2022"]) for r in rows if r["population_2022"]}


def forecasts_json(spark) -> list:
    if not lake.exists("gold", "forecasts"):
        return []
    rows = lake.read(spark, "gold", "forecasts").orderBy("district_code", "disease", "origin_week_start", "horizon")
    return [{"d": r["district_code"], "dz": r["disease"], "origin": _clean(r["origin_week_start"]), "h": int(r["horizon"]),
             "target": _clean(r["target_week_start"]), "mean": _num(r["mean"], 2), "q10": _num(r["q10"], 2),
             "q50": _num(r["q50"], 2), "q90": _num(r["q90"], 2)} for r in rows.collect()]


def allocation_json(spark) -> dict:
    if not lake.exists("gold", "allocation"):
        return {"scenarios": [], "rows": []}
    alloc = lake.read(spark, "gold", "allocation")
    sc = alloc.select("scenario_id", "budget", "alpha").distinct().orderBy("budget", "alpha", "scenario_id").collect()
    demand, stock = {}, {}
    if lake.exists("gold", "demand"):
        from analytics.forecast.demand import planning_demand

        pd_dem = planning_demand(lake.read(spark, "gold", "demand").toPandas())
        demand = {(r.district_code, r.resource): r.demand for r in pd_dem.itertuples()}
    if lake.exists("gold", "stock_latest"):
        stock = {(r["district_code"], r["resource"]): r["stock"]
                 for r in lake.read(spark, "gold", "stock_latest").collect()}
    rows = [{"id": r["scenario_id"], "d": r["district_code"], "r": r["resource"], "alloc": _num(r["allocation"], 1),
             "unmet": _num(r["unmet"], 1), "demand": _num(demand.get((r["district_code"], r["resource"]), 0.0), 1),
             "stock": int(stock.get((r["district_code"], r["resource"]), 0) or 0)}
            for r in alloc.orderBy("scenario_id", "resource", "district_code").collect()]
    return {"scenarios": [{"id": r["scenario_id"], "budget": _num(r["budget"], 4), "alpha": _num(r["alpha"], 4)}
                          for r in sc], "rows": rows}


def calendar_json(spark) -> list:
    if not lake.exists("gold", "calendar"):
        return []
    rows = lake.read(spark, "gold", "calendar").orderBy("division_code", "disease", "epi_week").collect()
    return [{"div": r["division_code"], "dz": r["disease"], "w": int(r["epi_week"]), "idx": _num(r["seasonal_index"]),
             "action": r["action"]} for r in rows]


def dq_json(spark) -> list:
    if not lake.exists("gold", "dq_report"):
        return []
    rows = lake.read(spark, "gold", "dq_report").orderBy("batch_id", "source").collect()
    return [{"batch_id": r["batch_id"], "source": r["source"], "rows_in": int(r["rows_in"]),
             "rows_clean": int(r["rows_clean"]), "rows_quarantined": int(r["rows_quarantined"]),
             "rules": {k: _num(v, 4) for k, v in sorted((r["rule_pass_rates"] or {}).items())}} for r in rows]


def tradeoff_files() -> list[pathlib.Path]:
    found = []
    for d in (lake.data_dir() / "evaluation", ROOT / "docs" / "experiments"):
        for name in TRADEOFF_FILES:
            if (d / name).exists() and name not in {f.name for f in found}:
                found.append(d / name)
    return found


# ------------------------------------------------------------------------------------------------ weekly + alerts

def suppressed_weekly(spark, since):
    """Suppressed weekly cells from `since` (a week_start) on, as a Spark DataFrame."""
    cases = lake.read(spark, "gold", "cases_weekly").where(F.col("disease").isin(*DISEASES))
    if since is not None:
        cases = cases.where(F.col("week_start") >= F.lit(since))
    cells = cases.select("district_code", "disease", "week_start", "epi_year", "epi_week",
                         F.col("cases").cast("long").alias("cases"))
    sup = suppress(cells, "cases", ["disease", "week_start"], THRESHOLD, table_cols=["district_code"])
    return sup.localCheckpoint()


def weekly_json(sup, pop) -> list:
    out = []
    for r in sup.orderBy("district_code", "disease", "week_start").collect():
        s = bool(r["suppressed"])
        c = None if s else int(r["cases"])
        p = pop.get(r["district_code"])
        out.append({"d": r["district_code"], "dz": r["disease"], "w": _clean(r["week_start"]), "c": c, "s": s,
                    "inc": None if (s or not p) else round(c / p * 1e5, 3)})
    return out


def alerts_json(spark, sup, since) -> list:
    if not lake.exists("gold", "alerts"):
        return []
    a = lake.read(spark, "gold", "alerts").withColumn("week_start", week_start_of("epi_year", "epi_week"))
    if since is not None:
        a = a.where(F.col("week_start") >= F.lit(since))
    flags = sup.select("district_code", "disease", "week_start", "suppressed")
    a = a.join(flags, ["district_code", "disease", "week_start"], "left")
    out = []
    for r in a.orderBy("district_code", "disease", "method", "week_start").collect():
        hidden = bool(r["suppressed"])
        out.append({"d": r["district_code"], "dz": r["disease"], "w": _clean(r["week_start"]), "method": r["method"],
                    "stat": None if hidden else _num(r["statistic"]), "thr": _num(r["threshold"]),
                    "alert": bool(r["alert"])})
    return out


# ------------------------------------------------------------------------------------------------ driver

def export(spark, out: pathlib.Path, max_bytes: int) -> dict:
    if not lake.exists("gold", "cases_weekly"):
        raise SystemExit("gold.cases_weekly is missing: run `make gold` first")
    out.mkdir(parents=True, exist_ok=True)
    pop = population(spark)
    fixed = {"forecasts.json": forecasts_json(spark), "allocation.json": allocation_json(spark),
             "calendar.json": calendar_json(spark), "dq.json": dq_json(spark)}
    texts = {k: dumps(v) for k, v in fixed.items()}
    copies = tradeoff_files()
    ours = set(texts) | {"weekly.json", "alerts.json", "meta.json"} | {p.name for p in copies}
    others = sum(f.stat().st_size for f in out.iterdir() if f.is_file() and f.name not in ours)  # e.g. the TopoJSON
    fixed_bytes = (sum(len(t.encode()) for t in texts.values()) + sum(p.stat().st_size for p in copies)
                   + others + 4096)
    budget = int(max_bytes * 0.97) - fixed_bytes  # meta.json and a small margin
    if budget <= 0:
        raise SystemExit(f"fixed files alone take {fixed_bytes / 1e6:.1f} MB of the {max_bytes / 1e6:.1f} MB budget")

    weeks = sorted(r[0] for r in lake.read(spark, "gold", "cases_weekly").select("week_start").distinct().collect())
    if not weeks:
        raise SystemExit("gold.cases_weekly is empty")
    # estimate bytes per week from the most recent weeks, then fit, check and shrink until it fits
    probe = weeks[-min(8, len(weeks)):]
    sup = suppressed_weekly(spark, probe[0])
    per_week = (len(dumps(weekly_json(sup, pop))) + len(dumps(alerts_json(spark, sup, probe[0])))) / len(probe)
    n = min(len(weeks), max(1, int(budget / max(per_week * 1.05, 1))))
    while True:
        since = weeks[-n] if n < len(weeks) else None
        sup = suppressed_weekly(spark, since)
        weekly = weekly_json(sup, pop)
        alerts = alerts_json(spark, sup, since)
        size = len(dumps(weekly)) + len(dumps(alerts))
        if size <= budget or n == 1:
            break
        n = max(1, int(n * min(0.95, budget / size)))

    audit = audit_suppression(sup, "cases", ["disease", "week_start"], THRESHOLD, table_cols=["district_code"])
    if audit["small_visible"] or audit["single_suppression_lines"]:
        raise RuntimeError(f"suppression audit failed: {audit}")

    data_through = None
    if lake.exists("silver", "encounters"):
        data_through = lake.read(spark, "silver", "encounters").agg(F.max("report_date")).first()[0]
    meta = {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "data_through": _clean(data_through or (weeks[-1] + dt.timedelta(days=6))),
            "diseases": list(DISEASES), "suppression_threshold": THRESHOLD}
    texts.update({"weekly.json": dumps(weekly), "alerts.json": dumps(alerts), "meta.json": dumps(meta)})
    for name, text in texts.items():
        (out / name).write_text(text)
    for p in copies:
        shutil.copyfile(p, out / p.name)
    total = sum(f.stat().st_size for f in out.iterdir() if f.is_file())
    if total > max_bytes:
        raise RuntimeError(f"dashboard data is {total / 1e6:.2f} MB, over the {max_bytes / 1e6:.0f} MB budget")
    return {"weeks_exported": n, "weeks_available": len(weeks), "first_week": _clean(weeks[-n]),
            "bytes_total": total, "suppressed_cells": sum(1 for r in weekly if r["s"]), "cells": len(weekly),
            "audit": audit, "copied": [p.name for p in copies]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=pathlib.Path, default=ROOT / "dashboard" / "data")
    p.add_argument("--max-mb", type=float, default=10.0, help="total size budget of the output folder (MB)")
    a = p.parse_args(argv)
    lake.load_env()
    spark = lake.spark_session("serving_export")
    try:
        res = export(spark, a.out, int(a.max_mb * 1024 * 1024))
    finally:
        spark.stop()
    print(f"serving: {res['weeks_exported']}/{res['weeks_available']} weeks from {res['first_week']}, "
          f"{res['bytes_total'] / 1e6:.2f} MB, {res['suppressed_cells']}/{res['cells']} cells suppressed, "
          f"audit {res['audit']}, copied {res['copied']}")
    return res


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
