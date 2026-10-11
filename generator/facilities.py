"""S5 facility registry: hospitals, upazila health complexes and clinics per district.

Implements ING-1 (S5 with district_code and beds, at least 600 rows for 64 districts).
The registry depends only on the district list and a facility seed, so an incremental drop made with a
different seed still points at the same facilities.
"""

import numpy as np
import pandas as pd

from generator import config as C
from generator.reference import crc

PER_DISTRICT = 11.5  # average facilities per district: 64 districts give about 736 (schema min_rows 600)


def facility_counts(pop):
    """(mch, dh, uhc, clinic) counts per district from population, totalling about PER_DISTRICT each."""
    pop = np.asarray(pop, dtype=float)
    n = len(pop)
    rank = np.argsort(np.argsort(-pop))
    mch = (rank < max(1, round(n * 0.4))).astype(int)  # the larger 40% of districts have a medical college
    dh = np.ones(n, dtype=int)
    target = int(np.ceil(PER_DISTRICT * n))
    rest = target - mch.sum() - dh.sum()
    base_uhc = np.full(n, 3)
    base_cl = np.ones(n, dtype=int)
    extra = rest - base_uhc.sum() - base_cl.sum()
    share = np.sqrt(pop) / np.sqrt(pop).sum() * max(extra, 0)
    alloc = np.floor(share).astype(int)
    rem = int(max(extra, 0) - alloc.sum())
    alloc[np.argsort(-(share - alloc))[:rem]] += 1
    # split the extra between upazila complexes (rural, sqrt pop) and clinics (urban, larger districts)
    to_clinic = np.round(alloc * np.clip(pop / pop.max(), 0.25, 0.75)).astype(int)
    return mch, dh, base_uhc + alloc - to_clinic, base_cl + to_clinic


def build(districts, facility_seed):
    rows = []
    mch, dh, uhc, cl = facility_counts(districts.population_2022.values)
    for i, d in enumerate(districts.itertuples()):
        rng = np.random.default_rng([facility_seed, C.STREAM_FACILITY, crc(d.district_code)])
        dnum = "".join(ch for ch in d.district_code if ch.isdigit()) or str(crc(d.district_code) % 10000)
        big = d.population_2022 > 8_000_000
        seq = 0

        def add(ftype, name, beds, icu, upazila, spread):
            nonlocal seq
            seq += 1
            rows.append({
                "facility_id": f"F{dnum}{seq:03d}",
                "facility_name": name,
                "facility_type": ftype,
                "district_code": d.district_code,
                "upazila_code": upazila,
                "latitude": round(float(d.lat + rng.normal(0, spread)), 5),
                "longitude": round(float(d.lon + rng.normal(0, spread)), 5),
                "beds": int(beds),
                "icu_beds": int(icu),
            })

        for k in range(mch[i]):
            add("medical_college_hospital", f"{d.district_name} Medical College Hospital",
                rng.integers(1200, 2600) if big else rng.integers(500, 1000), rng.integers(10, 41), None, 0.02)
        for k in range(dh[i]):
            add("district_hospital", f"{d.district_name} District Hospital",
                rng.integers(250, 500) if big else rng.integers(100, 251), rng.integers(0, 11), None, 0.02)
        for k in range(uhc[i]):
            add("upazila_health_complex", f"{d.district_name} Upazila Health Complex {k + 1}",
                rng.choice([31, 50], p=[0.4, 0.6]), 0, f"{d.district_code}{k + 1:02d}", 0.15)
        for k in range(cl[i]):
            upz = f"{d.district_code}{rng.integers(1, uhc[i] + 1):02d}" if rng.random() < 0.6 else None
            add("clinic", f"{d.district_name} Clinic {k + 1}", rng.integers(10, 31), rng.integers(0, 5), upz, 0.05)
    df = pd.DataFrame(rows)
    df["upazila_code"] = df["upazila_code"].astype("string")
    return df


def orphan_id(dnum, k):
    """A facility_id that can never exist in S5 (sequence numbers stay below 900)."""
    return f"F{dnum}{900 + k:03d}"
