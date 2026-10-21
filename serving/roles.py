"""Role-based exports for district users. Implements PRV-3.

PRV-3: "national users see all districts; a district user sees their own district in detail and others only in
aggregate". serving/export.py writes the national bundle (dashboard/data/). This module writes one bundle per
district to dashboard/roles/<district_code>/, in the same file shapes, so the dashboard opened with
`?district=<code>` runs unchanged on it:

- the user's own district: weekly counts, alerts, forecasts and allocation rows as in the national bundle
  (already suppressed there);
- every other division: one aggregate series (weekly counts summed over its districts, allocation summed);
- the user's own division: the aggregate of its OTHER districts only. Publishing the whole division total next
  to the own district would let a hidden own-district cell be recovered by subtraction;
- calendar (per division) and the data-quality report are national aggregates and are copied as they are.

An aggregate cell is published only if every district cell under it is visible in the national bundle
(aggregate_cells); it is therefore a sum of counts the national bundle already shows, and no difference of
published numbers, within one bundle or across bundles, gives back a hidden district cell.

This shapes what each role is sent. Keeping a district user from fetching the national files is the web
server's job (authentication and a per-role path); a static `make dashboard` server does not do that.
"""

from __future__ import annotations

import pathlib

from pipeline.bronze import lake

THRESHOLD = 5
REST = "~"  # agg_id of "division k without district d" is "<k>~<d>"


def division_of(code: str) -> str:
    """Division = BD + 2-digit division prefix of the P-code (research.md §1.3), as the dashboard does."""
    return code[:4]


def division_names(spark) -> dict:
    if lake.exists("silver", "districts"):
        rows = lake.read(spark, "silver", "districts").select("division_code", "division_name").distinct().collect()
        return {r["division_code"]: r["division_name"] for r in rows if r["division_code"]}
    return {}


def aggregate_cells(national_weekly, districts):
    """Aggregate cells keyed by agg_id: each division over all its districts, and each division without one
    district ("<k>~<d>"). Rows: {disease, week_start, cases, suppressed}.

    Disclosure rule: an aggregate cell is published only if every district cell under it is visible in the
    national bundle; otherwise it is hidden. Every published aggregate is then a sum of counts the national
    bundle already shows, so no combination of bundles (an aggregate minus another, or minus visible district
    cells) can give back a hidden district cell. Visible district counts are 0 or at least 5, so a visible
    aggregate is too."""
    cell = {(r["d"], r["dz"], r["w"]): r for r in national_weekly}
    keys = sorted({(r["dz"], r["w"]) for r in national_weekly})
    by_div = {}
    for d in districts:
        by_div.setdefault(division_of(d), []).append(d)
    groups = {k: ds for k, ds in by_div.items()}
    for k, ds in by_div.items():
        for d in ds:
            groups[f"{k}{REST}{d}"] = [x for x in ds if x != d]
    out = {}
    for agg, members in groups.items():
        rows = []
        for dz, w in keys:
            parts = [cell.get((m, dz, w)) for m in members]
            hidden = any(p is not None and p["s"] for p in parts)
            total = sum(p["c"] for p in parts if p is not None and not p["s"])
            rows.append({"disease": dz, "week_start": w, "cases": None if hidden else total, "suppressed": hidden})
        out[agg] = rows
    small = sum(1 for rows in out.values() for r in rows if r["cases"] is not None and 1 <= r["cases"] < THRESHOLD)
    if small:
        raise RuntimeError(f"role export: {small} visible aggregate counts between 1 and {THRESHOLD - 1}")
    return out, {"small_visible": small, "hidden_aggregate_cells": sum(r["suppressed"] for rows in out.values()
                                                                        for r in rows)}


def _weekly_rows(code, rows, pop):
    out = []
    for r in sorted(rows, key=lambda r: (r["disease"], r["week_start"])):
        s = bool(r["suppressed"])
        c = None if s else int(r["cases"])
        out.append({"d": code, "dz": r["disease"], "w": r["week_start"], "c": c, "s": s,
                    "inc": None if (s or not pop) else round(c / pop * 1e5, 3)})
    return out


def _alloc_rows(allocation, own):
    """Own district's rows as they are; other districts summed per division (own division without `own`)."""
    keep, agg = [], {}
    for r in allocation["rows"]:
        if r["d"] == own:
            keep.append(r)
            continue
        k = (r["id"], r["r"], division_of(r["d"]))
        a = agg.setdefault(k, {"id": r["id"], "d": k[2], "r": r["r"], "alloc": 0.0, "unmet": 0.0, "demand": 0.0,
                               "stock": 0})
        for f in ("alloc", "unmet", "demand"):
            a[f] += r[f] or 0.0
        a["stock"] += r["stock"] or 0
    for a in agg.values():
        for f in ("alloc", "unmet", "demand"):
            a[f] = round(a[f], 1)
    return {"scenarios": allocation["scenarios"], "rows": keep + list(agg.values())}


def write_role_bundles(spark, root: pathlib.Path, national: dict, pop: dict, meta: dict, dumps) -> dict:
    """national: the parsed national objects (weekly, alerts, forecasts, allocation, calendar, dq)."""
    districts = sorted({r["d"] for r in national["weekly"]})
    cells, audit = aggregate_cells(national["weekly"], districts)
    names = division_names(spark)
    divisions = sorted(k for k in cells if REST not in k)
    div_pop = {}
    for d, p in pop.items():
        div_pop[division_of(d)] = div_pop.get(division_of(d), 0.0) + p
    root.mkdir(parents=True, exist_ok=True)
    sizes = []
    for own in districts:
        k_own = division_of(own)
        weekly = [r for r in national["weekly"] if r["d"] == own]
        labels = {}
        for k in divisions:
            if k == k_own:
                rows, p = cells.get(f"{k}{REST}{own}", []), div_pop.get(k, 0.0) - pop.get(own, 0.0)
                labels[k] = f"{names.get(k, k)} division, other districts"
            else:
                rows, p = cells[k], div_pop.get(k, 0.0)
                labels[k] = f"{names.get(k, k)} division"
            weekly += _weekly_rows(k, rows, p)
        files = {
            "weekly.json": weekly,
            "alerts.json": [r for r in national["alerts"] if r["d"] == own],
            "forecasts.json": [r for r in national["forecasts"] if r["d"] == own],
            "allocation.json": _alloc_rows(national["allocation"], own),
            "calendar.json": national["calendar"],
            "dq.json": national["dq"],
            "meta.json": {**meta, "role": {"district": own, "aggregates": labels}},
        }
        d = root / own
        d.mkdir(parents=True, exist_ok=True)
        for name, obj in files.items():
            (d / name).write_text(dumps(obj))
        sizes.append(sum(f.stat().st_size for f in d.iterdir()))
    return {"bundles": len(districts), "max_bundle_bytes": max(sizes) if sizes else 0, "audit": audit}
