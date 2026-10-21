"""EpiScope synthetic data generator: S4 encounters, S5 facilities, S6 stock and the outbreak ground truth.

Implements ING-1 (synthetic sources S4-S6 at scale, brief §5.2) and ENG-2 (vectorised, parallel, timed),
and plants the defects that DQ-1/DQ-2 must catch and the ground truth AN-2/AN-3 are scored on.

Usage:
    python -m generator.generate --sf 0.25 --seed 42 [--start 2021-01-01 --end 2025-12-31] [--out data/raw]
    python -m generator.generate --incremental-days 14 --seed 7     # fresh 14-day drop, same layout

Full mode replaces <out>/encounters, <out>/stock and <out>/facilities, and overwrites
<truth-dir>/outbreaks.csv and artefacts.csv. Without --start/--end the window is the weather file's range.

Incremental mode writes report dates [start, start + N - 1]; start defaults to the day after the latest
report_date already in <out>/encounters (or after the weather range if <out> is empty). It reuses the
existing facilities.csv so every foreign key still resolves, replaces only its own partitions, and merges
its outbreaks into the ground-truth files. Records with onsets up to 21 days before the drop arrive in it,
which is the late data ING-4 must merge.

Same arguments give byte-identical output (research.md §8.2).
"""

import argparse
import json
import multiprocessing as mp
import os
import pathlib
import shutil
import sys
import time

import numpy as np
import pandas as pd

from generator import calendar_bd as cal
from generator import config as C
from generator import encounters as E
from generator import facilities as F
from generator import outbreaks as O
from generator import reference as R
from generator import stock as S
from pipeline.bronze import lake

_CTX = None


def _init(ctx):
    global _CTX
    _CTX = ctx


def _run(task):
    t0 = time.time()
    if task[0] == "stock":
        n = S.write(_CTX, _CTX.facilities)
    else:
        n = E.write_task(_CTX, task[1], task[2])
    return task, n, time.time() - t0


def _day(s):
    return int(np.datetime64(s, "D").astype(np.int64))


def _date(d):
    return str(np.datetime64(int(d), "D"))


def district_weights(districts):
    """(n_districts, 5) relative intensity per district and disease, population-weighted mean 1."""
    n = len(districts)
    pop = districts.population_2022.values.astype(float)
    density = pop / np.maximum(districts.area_km2.values.astype(float), 1.0)
    rel = density / np.median(density)
    w = np.ones((n, len(C.DISEASES)))
    for i, r in enumerate(districts.itertuples()):
        het = np.random.default_rng([C.STRUCT_SEED, C.STREAM_STRUCT, R.crc(r.district_code)]).lognormal(0, 0.2, 5)
        dhaka = R.matches(r.district_code, r.district_name, C.DHAKA)
        w[i, C.D_DENGUE] = rel[i] ** 0.6 * (1.5 if dhaka else 1.0) * het[0]
        w[i, C.D_DIARRHOEA] = het[1]
        w[i, C.D_ILI] = rel[i] ** 0.2 * het[2]
        if R.matches(r.district_code, r.district_name, C.MALARIA_CHT):
            mw = C.MALARIA_W["cht"]
        elif R.matches(r.district_code, r.district_name, C.MALARIA_COX):
            mw = C.MALARIA_W["cox"]
        elif R.matches(r.district_code, r.district_name, C.MALARIA_ENDEMIC):
            mw = C.MALARIA_W["endemic"]
        else:
            mw = C.MALARIA_W["other"]
        w[i, C.D_MALARIA] = mw * het[3]
        w[i, C.D_BACKGROUND] = 1.0
    w /= (pop[:, None] * w).sum(axis=0) / pop.sum()
    return w


def agesex_cdfs(code, pop):
    """(5, 12) cumulative probabilities over cells (M bands 0-5, then F bands 0-5) per disease."""
    sub = pop[pop.district_code == code]
    cells = np.zeros(12)
    for j, sex in enumerate(["M", "F"]):
        for b, band in enumerate(C.AGE_BANDS):
            cells[j * 6 + b] = sub[(sub.sex == sex) & (sub.age_band == band)].population.sum()
    if cells.sum() <= 0:
        cells = np.tile(R.AGE_SHARES, 2)
    out = np.zeros((len(C.DISEASES), 12))
    for k in range(len(C.DISEASES)):
        p = cells * np.tile(C.AGE_RR[k], 2) * np.repeat([C.MALE_RR[k], 1.0], 6)
        out[k] = np.cumsum(p / p.sum())
    return out


def existing_last_report(out):
    enc = out / "encounters"
    if not enc.exists():
        return None
    days = [p.name.split("=", 1)[1] for p in enc.glob("report_date=*") if p.is_dir()]
    return max(days) if days else None


def parse(argv):
    p = argparse.ArgumentParser(prog="python -m generator.generate", description=__doc__.split("\n\n")[0])
    p.add_argument("--sf", type=float, default=0.25, help="scale factor; SF=1 gives 25M+ encounters for 5 years")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start", help="first report_date (default: weather start, or after existing data)")
    p.add_argument("--end", help="last report_date (default: weather end)")
    lake.load_env()  # the same EPISCOPE_DATA_DIR that bronze reads, so a custom .env still lines up (ING-1)
    p.add_argument("--out", default=str(lake.data_dir() / "raw"))
    p.add_argument("--truth-dir", default="ground_truth")
    p.add_argument("--reference", default=str(lake.data_dir() / "reference"))
    p.add_argument("--incremental-days", type=int, help="write a fresh N-day drop in the same layout")
    p.add_argument("--facility-seed", type=int, default=42, help="seed of the S5 registry (keep fixed)")
    p.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    a = p.parse_args(argv)
    if a.sf <= 0:
        p.error("--sf must be positive")
    if a.incremental_days is not None and a.incremental_days < 1:
        p.error("--incremental-days must be at least 1")
    return a


def main(argv=None):
    a = parse(argv)
    t0 = time.time()
    out, truth = pathlib.Path(a.out), pathlib.Path(a.truth_dir)
    districts, pop, weather_df, notes = R.load(a.reference)
    for n in notes:
        print(f"WARNING: {n}", file=sys.stderr)
    wdates = pd.to_datetime(weather_df["date"])
    w_first, w_last = str(wdates.min().date()), str(wdates.max().date())

    incremental = a.incremental_days is not None
    if incremental:
        if a.start:
            first = _day(a.start)
        else:
            prev = existing_last_report(out)
            first = _day(prev) + 1 if prev else _day(w_last) + 1
        last = first + a.incremental_days - 1
    else:
        first = _day(a.start or w_first)
        last = _day(a.end or w_last)
    if last < first:
        raise SystemExit("end date is before start date")

    # S5 registry: reused in incremental mode so foreign keys keep resolving
    fpath = out / "facilities" / "facilities.csv"
    if incremental and fpath.exists():
        fac = pd.read_csv(fpath, dtype={"facility_id": str, "district_code": str, "upazila_code": str})
    else:
        fac = F.build(districts, a.facility_seed)

    weights = district_weights(districts)
    codes = districts.district_code.tolist()
    fac_ids = fac.facility_id.tolist()
    rank = np.argsort(np.argsort(-districts.population_2022.values))
    groups = [[i for i in range(len(codes)) if rank[i] % C.GROUPS == g] for g in range(C.GROUPS)]
    type_w = {"medical_college_hospital": 1.0, "district_hospital": 1.0, "upazila_health_complex": 1.5,
              "clinic": 0.8}
    dlist = []
    for i, r in enumerate(districts.itertuples()):
        sub = fac[fac.district_code == r.district_code]
        dnum = "".join(ch for ch in r.district_code if ch.isdigit()) or str(R.crc(r.district_code) % 10000)
        orphans = [F.orphan_id(dnum, k) for k in range(20)]
        o_idx = np.arange(len(fac_ids), len(fac_ids) + len(orphans))
        fac_ids += orphans
        if sub.empty:  # a district with no facility: every row is an orphan, which DQ will quarantine
            f_idx, f_cdf = o_idx[:1], np.array([1.0])
        else:
            fw = sub.beds.values.astype(float) ** 0.6 * sub.facility_type.map(type_w).fillna(1.0).values
            f_idx, f_cdf = sub.index.values, np.cumsum(fw / fw.sum())
        dlist.append({"code": r.district_code, "name": r.district_name, "pop": float(r.population_2022),
                      "w": weights[i], "agesex_cdf": agesex_cdfs(r.district_code, pop),
                      "fac_idx": np.asarray(f_idx), "fac_cdf": f_cdf, "orphan_idx": o_idx})

    prefix = f"OB{a.seed}-{_date(first).replace('-', '')}-" if incremental else f"OB{a.seed}-"
    outbreaks = O.plan(districts, weights, a.seed, first, last, incremental, prefix)
    artefacts = O.plan_artefacts(fac, a.seed, first, last)
    fac_pos = {f: i for i, f in enumerate(fac_ids)}
    arts = [{"fac": fac_pos[x.facility_id], "start": x.silent_start, "days": x.silent_days} for x in artefacts]

    weather = R.Weather(weather_df, codes)
    holidays = cal.holidays(np.datetime64(first - 200, "D"), np.datetime64(last + 200, "D"))
    ctx = E.Ctx(seed=a.seed, sf=a.sf, first=first, last=last, out=out, districts=dlist, groups=groups,
                codes=codes, fac_ids=fac_ids, weather=weather, holidays=holidays, outbreaks=outbreaks,
                artefacts=arts, max_src=max([o.ordinal for o in outbreaks] + [0]), facilities=fac)

    # make the output idempotent: remove what this run is about to write
    if incremental:
        for d in range(first, last + 1):
            for sub in (f"encounters/report_date={_date(d)}", f"stock/snapshot_date={_date(d)}"):
                shutil.rmtree(out / sub, ignore_errors=True)
    else:
        for sub in ("encounters", "stock", "facilities"):
            shutil.rmtree(out / sub, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    lo = first - C.DELAY_MAX - 4
    years = range(int(_date(lo)[:4]), int(_date(last)[:4]) + 1)
    tasks = [("stock",)] + [("enc", y, g) for y in years for g in range(C.GROUPS)]
    t_plan = time.time() - t0
    rows, stock_rows, slowest, enc_secs, stock_secs = 0, 0, 0.0, 0.0, 0.0
    if a.workers <= 1:
        _init(ctx)
        results = map(_run, tasks)
    else:
        # not fork: pyarrow and numpy start threads, and forking a threaded process can deadlock
        method = "forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn"
        pool = mp.get_context(method).Pool(a.workers, initializer=_init, initargs=(ctx,))
        results = pool.imap_unordered(_run, tasks)
    try:
        for task, n, secs in results:
            slowest = max(slowest, secs)
            if task[0] == "stock":
                stock_rows, stock_secs = n, secs
            else:
                rows += n
                enc_secs += secs
    finally:
        if a.workers > 1:
            pool.close()
            pool.join()

    fpath.parent.mkdir(parents=True, exist_ok=True)
    fac.to_csv(fpath, index=False)
    write_truth(truth, outbreaks, artefacts, last, incremental)

    total = time.time() - t0
    manifest = {
        "mode": "incremental" if incremental else "full", "sf": a.sf, "seed": a.seed,
        "facility_seed": a.facility_seed, "start": _date(first), "end": _date(last),
        "districts": len(codes), "facilities": len(fac), "encounter_rows": rows, "stock_rows": stock_rows,
        "outbreaks": len(outbreaks), "artefacts": len(artefacts), "workers": a.workers,
        "seconds": {"plan": round(t_plan, 2), "total": round(total, 2), "slowest_task": round(slowest, 2),
                    "encounter_cpu": round(enc_secs, 2), "stock": round(stock_secs, 2)},
        "notes": notes,
    }
    if not incremental:
        (out / "_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return manifest


def write_truth(truth, outbreaks, artefacts, last, incremental):
    truth.mkdir(parents=True, exist_ok=True)
    ob = pd.DataFrame([{
        "outbreak_id": o.outbreak_id, "disease": C.DISEASES[o.disease], "district_code": o.district_code,
        "start_date": _date(o.start), "end_date": _date(min(o.end, last)),
        "peak_multiplier": o.peak_multiplier, "shape": o.shape,
    } for o in outbreaks], columns=["outbreak_id", "disease", "district_code", "start_date", "end_date",
                                    "peak_multiplier", "shape"])
    ar = pd.DataFrame([{
        "facility_id": x.facility_id, "district_code": x.district_code, "silent_start": _date(x.silent_start),
        "silent_end": _date(x.backfill - 1), "backfill_date": _date(x.backfill), "type": "artefact",
    } for x in artefacts], columns=["facility_id", "district_code", "silent_start", "silent_end",
                                    "backfill_date", "type"])
    for df, name, key in ((ob, "outbreaks.csv", ["outbreak_id"]),
                          (ar, "artefacts.csv", ["facility_id", "silent_start"])):
        path = truth / name
        if incremental and path.exists():
            old = pd.read_csv(path, dtype=str)
            df = pd.concat([old, df.astype(str)]).drop_duplicates(subset=key, keep="last")
        df.to_csv(path, index=False)


if __name__ == "__main__":
    main()
