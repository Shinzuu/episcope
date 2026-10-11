"""Mock serving exports for building the dashboard before the real pipeline exists.

Implements VIS-1, VIS-2, VIS-4, VIS-5 (scenario grid) and VIS-6 test data. Writes `dashboard/data/*.json` in
exactly the shapes listed in docs/contracts.md "Serving", for 64 districts and 4 diseases, under 10 MB, with
primary (1-4) and simple secondary suppression (PRV-2) so the dashboard's hatching can be exercised.

Nothing here is real or pipeline output. The real exporter is `serving/export.py`, which overwrites these files.
Districts come from `data/reference/districts.csv` when it exists, else from the built-in COD-AB v03 list.
The end week defaults to the most recent Monday, so no date is hard-coded.

    python -m serving.mock_data [--out dashboard/data] [--weeks 156] [--seed 7] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISEASES = ["dengue", "diarrhoea", "ili", "malaria"]
RESOURCES = ["beds", "iv_saline", "ns1_kits", "ors", "antimalarials"]
SUPPRESS_BELOW = 5

# code, name, approximate population (millions). COD-AB v03 codes; populations are rough, for mock data only.
BUILTIN_DISTRICTS = [
    ("BD1004", "Barguna", 1.0), ("BD1006", "Barishal", 2.6), ("BD1009", "Bhola", 2.0), ("BD1042", "Jhalokati", 0.7),
    ("BD1078", "Patuakhali", 1.7), ("BD1079", "Pirojpur", 1.2), ("BD2003", "Bandarban", 0.5),
    ("BD2012", "Brahmanbaria", 3.3), ("BD2013", "Chandpur", 2.6), ("BD2015", "Chattogram", 9.2),
    ("BD2019", "Cumilla", 6.2), ("BD2022", "Cox's Bazar", 2.8), ("BD2030", "Feni", 1.6),
    ("BD2046", "Khagrachhari", 0.7), ("BD2051", "Lakshmipur", 2.0), ("BD2075", "Noakhali", 3.6),
    ("BD2084", "Rangamati", 0.6), ("BD3026", "Dhaka", 14.7), ("BD3029", "Faridpur", 2.2), ("BD3033", "Gazipur", 5.3),
    ("BD3035", "Gopalganj", 1.3), ("BD3048", "Kishoreganj", 3.3), ("BD3054", "Madaripur", 1.3),
    ("BD3056", "Manikganj", 1.6), ("BD3059", "Munshiganj", 1.6), ("BD3067", "Narayanganj", 3.9),
    ("BD3068", "Narsingdi", 2.6), ("BD3082", "Rajbari", 1.2), ("BD3086", "Shariatpur", 1.3), ("BD3093", "Tangail", 4.0),
    ("BD4001", "Bagerhat", 1.6), ("BD4018", "Chuadanga", 1.2), ("BD4041", "Jashore", 3.1), ("BD4044", "Jhenaidah", 2.0),
    ("BD4047", "Khulna", 2.6), ("BD4050", "Kushtia", 2.1), ("BD4055", "Magura", 1.0), ("BD4057", "Meherpur", 0.7),
    ("BD4065", "Narail", 0.8), ("BD4087", "Satkhira", 2.2), ("BD4539", "Jamalpur", 2.5), ("BD4561", "Mymensingh", 5.9),
    ("BD4572", "Netrakona", 2.3), ("BD4589", "Sherpur", 1.5), ("BD5010", "Bogura", 3.7), ("BD5038", "Joypurhat", 1.0),
    ("BD5064", "Naogaon", 2.8), ("BD5069", "Natore", 1.9), ("BD5070", "Chapainawabganj", 1.8), ("BD5076", "Pabna", 2.9),
    ("BD5081", "Rajshahi", 2.9), ("BD5088", "Sirajganj", 3.4), ("BD5527", "Dinajpur", 3.3), ("BD5532", "Gaibandha", 2.6),
    ("BD5549", "Kurigram", 2.3), ("BD5552", "Lalmonirhat", 1.4), ("BD5573", "Nilphamari", 2.0),
    ("BD5577", "Panchagarh", 1.2), ("BD5585", "Rangpur", 3.2), ("BD5594", "Thakurgaon", 1.5),
    ("BD6036", "Habiganj", 2.4), ("BD6058", "Moulvibazar", 2.1), ("BD6090", "Sunamganj", 2.7), ("BD6091", "Sylhet", 3.9),
]
MALARIA_ENDEMIC = {"BD2003", "BD2084", "BD2046", "BD2022"}  # Chittagong Hill Tracts and Cox's Bazar
URBAN = {"BD3026", "BD2015", "BD3033", "BD3067"}


def load_districts() -> list[dict]:
    ref = ROOT / "data/reference/districts.csv"
    if ref.exists():
        with ref.open() as f:
            rows = list(csv.DictReader(f))
        return [{"code": r["district_code"], "div": r["division_code"], "pop": float(r["population_2022"])}
                for r in rows]
    return [{"code": c, "div": c[:4], "pop": p * 1e6} for c, _, p in BUILTIN_DISTRICTS]


def iso_week(d: dt.date) -> int:
    return d.isocalendar()[1]


def seasonal_rate(disease: str, week: np.ndarray, code: str) -> np.ndarray:
    """Expected weekly cases per 100k for one district, by ISO week number."""
    w = week.astype(float)
    bump = lambda peak, width: np.exp(-0.5 * ((((w - peak + 26) % 52) - 26) / width) ** 2)  # noqa: E731
    if disease == "dengue":
        rate = 0.05 + 2.2 * bump(40, 5)
        rate *= 3.0 if code in URBAN else 0.6
    elif disease == "diarrhoea":
        rate = 4.0 + 3.5 * bump(17, 4) + 2.5 * bump(34, 4)
    elif disease == "ili":
        rate = 2.5 + 2.0 * bump(30, 5) + 1.5 * bump(3, 3)
    else:  # malaria: almost all in the hill districts, so most other cells fall in 1-4 and get suppressed
        rate = (3.0 + 6.0 * bump(25, 5)) if code in MALARIA_ENDEMIC else 0.05 + 0.15 * bump(25, 5)
    return rate


def suppress(counts: np.ndarray) -> np.ndarray:
    """Primary suppression of 1-4, plus secondary per district-week across diseases (axis 1)."""
    mask = (counts >= 1) & (counts < SUPPRESS_BELOW)
    # counts shape: (district, disease, week). Where a district-week line has exactly one hidden cell,
    # also hide the smallest non-zero visible cell in that line.
    single = mask.sum(axis=1) == 1
    for di, wi in zip(*np.nonzero(single)):
        line = counts[di, :, wi]
        cand = [(line[k], k) for k in range(line.size) if not mask[di, k, wi] and line[k] > 0]
        if cand:
            mask[di, min(cand)[1], wi] = True
    return mask


def build(weeks: int, seed: int, end: dt.date) -> dict[str, object]:
    rng = np.random.default_rng(seed)
    dists = load_districts()
    last_monday = end - dt.timedelta(days=end.weekday())
    week_starts = [last_monday - dt.timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    wnum = np.array([iso_week(d) for d in week_starts])
    nD, nZ, nW = len(dists), len(DISEASES), weeks

    expected = np.zeros((nD, nZ, nW))
    for i, d in enumerate(dists):
        for k, z in enumerate(DISEASES):
            year_factor = rng.lognormal(0, 0.25, size=nW // 52 + 2).repeat(52)[:nW]
            expected[i, k] = seasonal_rate(z, wnum, d["code"]) * d["pop"] / 1e5 * year_factor
    # Inject a handful of recent outbreaks so the alert views have something to show.
    for _ in range(10):
        i, k = rng.integers(nD), rng.integers(nZ)
        start = nW - rng.integers(2, 10)
        expected[i, k, start:start + 4] *= rng.uniform(2.5, 5.0)
    counts = rng.poisson(expected)
    mask = suppress(counts)

    pops = np.array([d["pop"] for d in dists])
    weekly = []
    for i, d in enumerate(dists):
        for k, z in enumerate(DISEASES):
            for w in range(nW):
                s = bool(mask[i, k, w])
                c = None if s else int(counts[i, k, w])
                weekly.append({"d": d["code"], "dz": z, "w": week_starts[w].isoformat(), "c": c, "s": s,
                               "inc": None if s else round(c / pops[i] * 1e5, 3)})

    # Alerts for the last 52 weeks. ears_c2: stat = observed count, thr = mean + 3 sd of weeks t-9..t-3 (count
    # scale, so the time series can draw it). cusum: one-sided on the standardised series (k = 1, h = 5), reset after
    # each signal.
    # A stat on a suppressed week would reveal the count, so it is exported as null.
    alerts = []
    a_from = max(9, nW - 52)
    for i, d in enumerate(dists):
        for k, z in enumerate(DISEASES):
            x = counts[i, k].astype(float)
            cus = 0.0
            for w in range(a_from, nW):
                base = x[w - 9:w - 2]
                mu, sd = base.mean(), max(base.std(ddof=1), 1.0)
                thr = round(mu + 3 * sd, 2)
                hidden = bool(mask[i, k, w])
                alerts.append({"d": d["code"], "dz": z, "w": week_starts[w].isoformat(), "method": "ears_c2",
                               "stat": None if hidden else round(x[w], 2), "thr": thr,
                               "alert": bool(x[w] > thr and x[w] >= SUPPRESS_BELOW)})
                cus = max(0.0, cus + (x[w] - mu) / sd - 1.0)
                fired = bool(cus > 5.0 and not hidden)
                alerts.append({"d": d["code"], "dz": z, "w": week_starts[w].isoformat(), "method": "cusum",
                               "stat": None if hidden else round(cus, 2), "thr": 5.0, "alert": fired})
                if fired:
                    cus = 0.0

    # Forecasts: last 6 origins x horizons 1-4, from the seasonal expectation with a lognormal spread.
    forecasts = []
    for o in range(nW - 6, nW):
        origin = week_starts[o]
        for i, d in enumerate(dists):
            for k, z in enumerate(DISEASES):
                level = counts[i, k, max(0, o - 3):o + 1].mean()
                for h in range(1, 5):
                    target = origin + dt.timedelta(weeks=h)
                    seas = seasonal_rate(z, np.array([iso_week(target)]), d["code"])[0] * d["pop"] / 1e5
                    mean = 0.6 * level + 0.4 * seas
                    spread = 0.25 + 0.08 * h
                    q = [mean * np.exp(spread * zq) for zq in (-1.2816, 0.0, 1.2816)]
                    forecasts.append({"d": d["code"], "dz": z, "origin": origin.isoformat(), "h": h,
                                      "target": target.isoformat(), "mean": round(mean, 2), "q10": round(q[0], 2),
                                      "q50": round(q[1], 2), "q90": round(q[2], 2)})

    # Allocation scenario grid: 5 budget levels x 10 alpha values (research.md section 10).
    res_disease = {"beds": None, "iv_saline": "diarrhoea", "ns1_kits": "dengue", "ors": "diarrhoea",
                   "antimalarials": "malaria"}
    per_case = {"beds": 0.08, "iv_saline": 3.0, "ns1_kits": 1.0, "ors": 6.0, "antimalarials": 1.0}
    latest = forecasts[-nD * nZ * 4:]
    q90 = {}
    for f in latest:
        q90[(f["d"], f["dz"])] = q90.get((f["d"], f["dz"]), 0.0) + f["q90"]
    demand = np.zeros((nD, len(RESOURCES)))
    for i, d in enumerate(dists):
        for r, res in enumerate(RESOURCES):
            zs = [res_disease[res]] if res_disease[res] else DISEASES
            demand[i, r] = round(sum(q90[(d["code"], z)] for z in zs) * per_case[res])
    stock = np.round(demand * rng.uniform(0.2, 1.1, size=demand.shape))
    need = np.maximum(demand - stock, 0)
    pshare = pops / pops.sum()
    scenarios, rows, sid = [], [], 0
    for b in (0.25, 0.5, 0.75, 1.0, 1.5):
        for a10 in range(10):
            alpha = a10 / 10
            sid += 1
            scenarios.append({"id": sid, "budget": b, "alpha": alpha})
            for r, res in enumerate(RESOURCES):
                total = b * need[:, r].sum()
                share = need[:, r] / max(need[:, r].sum(), 1)
                alloc = np.round(total * ((1 - alpha) * share + alpha * pshare))
                unmet = np.maximum(need[:, r] - alloc, 0)
                for i, d in enumerate(dists):
                    rows.append({"id": sid, "d": d["code"], "r": res, "alloc": int(alloc[i]), "unmet": int(unmet[i]),
                                 "demand": int(demand[i, r]), "stock": int(stock[i, r])})

    divisions = sorted({d["div"] for d in dists})
    calendar = []
    for div in divisions:
        members = [d["code"] for d in dists if d["div"] == div]
        for z in DISEASES:
            wk = np.arange(1, 53)
            idx = np.mean([seasonal_rate(z, wk, c) for c in members], axis=0)
            idx = idx / idx.mean()
            onset = int(wk[np.argmax(idx > idx.min() + 0.25 * (idx.max() - idx.min()))])
            for w in wk:
                action = ("preposition" if w == (onset - 3 - 1) % 52 + 1 else
                          "campaign" if w == (onset - 5 - 1) % 52 + 1 else "none")
                calendar.append({"div": div, "dz": z, "w": int(w), "idx": round(float(idx[w - 1]), 3),
                                 "action": action})

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    dq = []
    for b in range(5):
        day = (last_monday - dt.timedelta(days=4 - b)).isoformat()
        for src in ("encounters", "facilities", "stock"):
            rows_in = int(rng.integers(5_000, 40_000)) if src == "encounters" else int(rng.integers(600, 4_000))
            bad = int(rows_in * rng.uniform(0.02, 0.05))
            dq.append({"batch_id": f"{src}-{day}", "source": src, "rows_in": rows_in, "rows_clean": rows_in - bad,
                       "rows_quarantined": bad,
                       "rules": {f"DQ{n:02d}": round(float(rng.uniform(0.95, 1.0)), 4) for n in range(1, 13)}})

    meta = {"generated_at": now.isoformat().replace("+00:00", "Z"),
            "data_through": (last_monday + dt.timedelta(days=6)).isoformat(),
            "diseases": DISEASES, "suppression_threshold": SUPPRESS_BELOW}
    return {"meta.json": meta, "weekly.json": weekly, "alerts.json": alerts, "forecasts.json": forecasts,
            "allocation.json": {"scenarios": scenarios, "rows": rows}, "calendar.json": calendar, "dq.json": dq}


def write(out: pathlib.Path, files: dict[str, object]) -> int:
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, obj in files.items():
        text = json.dumps(obj, separators=(",", ":"))
        (out / name).write_text(text)
        total += len(text.encode())
    return total


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=pathlib.Path, default=ROOT / "dashboard/data")
    p.add_argument("--weeks", type=int, default=156)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today() - dt.timedelta(days=7))
    a = p.parse_args(argv)
    size = write(a.out, build(a.weeks, a.seed, a.end))
    print(f"wrote mock serving JSON to {a.out} ({size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
