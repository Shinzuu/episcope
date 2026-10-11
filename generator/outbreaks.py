"""Outbreak and artefact planning, and the outbreak curve shapes (our choice, research.md §8.4).

Implements ING-1 (injected outbreaks and the silent-then-backfill artefact) for AN-2/AN-3 evaluation.

Shapes:
- fast: gamma pulse, shape 3, scale D/8 (peak at a quarter of the duration D), D = 2-7 weeks;
- slow: logistic growth with a doubling time of about 3 weeks, plateau, then a linear decline over the last
  fifth of D, D = 13-20 weeks;
- nationwide: a discrete SEIR epidemic curve (R0 1.8, 3-day latency, 5-day infectious period) for a novel
  respiratory virus (J-codes), seeded in Dhaka; each district starts later by distance / 25 km per day.
Every curve is scaled to peak 1, and the excess intensity is (peak_multiplier - 1) * g(t) * baseline, with
the baseline floored at the district's annual mean so off-season outbreaks are still visible.

Ground truth is written only to ground_truth/outbreaks.csv, artefacts to ground_truth/artefacts.csv.
"""

from dataclasses import dataclass

import numpy as np

from generator import config as C
from generator.reference import matches

FAST, SLOW, NATIONWIDE = "fast", "slow", "nationwide"


@dataclass
class Outbreak:
    ordinal: int
    outbreak_id: str
    disease: int
    district_code: str          # or "ALL"
    start: int                  # day index (days since 1970-01-01)
    duration: int
    peak_multiplier: float
    shape: str
    delays: dict = None         # district_code -> start delay in days (nationwide only)

    @property
    def end(self):
        extra = max(self.delays.values()) if self.delays else 0
        return self.start + self.duration - 1 + extra


@dataclass
class Artefact:
    facility_id: str
    district_code: str
    silent_start: int
    silent_days: int

    @property
    def backfill(self):
        return self.silent_start + self.silent_days


def curve(ob, days, district_code):
    """Shape kernel g(t) in [0, 1] for this outbreak at int days, for one district."""
    tau = np.asarray(days, dtype=float) - ob.start
    if ob.shape == NATIONWIDE:
        tau = tau - ob.delays.get(district_code, 0)
    d = float(ob.duration)
    g = np.zeros_like(tau)
    inside = (tau >= 0) & (tau < d)
    t = tau[inside]
    if ob.shape == FAST:
        theta = d / 8.0
        g[inside] = (t / theta) ** 2 * np.exp(-t / theta) / (4 * np.exp(-2))
    elif ob.shape == SLOW:
        r = np.log(2) / 21.0
        mid = 0.55 * d
        lo = 1 / (1 + np.exp(r * mid))
        grow = (1 / (1 + np.exp(-r * (t - mid))) - lo) / (1 - lo)
        decline = np.clip((d - t) / (0.2 * d), 0, 1)
        g[inside] = grow * decline / max((1 / (1 + np.exp(-r * (0.8 * d - mid))) - lo) / (1 - lo), 1e-9)
    else:
        g[inside] = _seir(int(d))[t.astype(int)]
    return np.clip(g, 0, 1)


def _seir(n, r0=1.8, latent=3.0, infectious=5.0):
    s, e, i = 1 - 1e-4, 0.0, 1e-4
    out = np.zeros(n)
    beta, sigma, gamma = r0 / infectious, 1 / latent, 1 / infectious
    for k in range(n):
        new_e, new_i, new_r = beta * s * i, sigma * e, gamma * i
        s, e, i = s - new_e, e + new_e - new_i, i + new_i - new_r
        out[k] = new_i
    return out / out.max()


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def plan(districts, weights, seed, first, last, incremental, id_prefix):
    """Plan outbreaks inside [first, last] (int days). Returns a list of Outbreak."""
    rng = np.random.default_rng([seed, C.STREAM_PLAN, first, last])
    span = last - first + 1
    codes = districts.district_code.tolist()
    pop = districts.population_2022.values.astype(float)
    obs = []

    def overlaps(code, dz, s, e):
        return any(o.district_code in (code, "ALL") and o.disease == dz and s <= o.end and o.start <= e
                   for o in obs)

    def add(dz, code, start, dur, mult, shape, delays=None):
        k = len(obs) + 1
        obs.append(Outbreak(k, f"{id_prefix}{k:03d}", dz, code, int(start), int(dur), round(float(mult), 2),
                            shape, delays))

    if not incremental and span >= 150:
        # one nationwide novel-respiratory surge
        dhaka = [i for i, r in enumerate(districts.itertuples()) if matches(r.district_code, r.district_name, C.DHAKA)]
        hub = dhaka[0] if dhaka else int(np.argmax(pop))
        dist = _haversine_km(districts.lat.values[hub], districts.lon.values[hub], districts.lat.values,
                             districts.lon.values)
        delays = {c: int(round(km / 25.0)) for c, km in zip(codes, dist)}
        dur = 110
        latest = max(first + 30, last - dur - max(delays.values()))
        add(C.D_ILI, "ALL", rng.integers(first + 30, latest + 1), dur, rng.uniform(2.5, 3.5), NATIONWIDE, delays)
        # one slow-growing dengue or diarrhoea outbreak in a large district (not the hub)
        big = [i for i in np.argsort(-pop)[: max(3, len(codes) // 6)] if i != hub] or [hub]
        dur = int(rng.integers(91, 141))
        start = rng.integers(first + 14, max(first + 15, last - dur))
        add(int(rng.choice([C.D_DENGUE, C.D_DIARRHOEA])), codes[int(rng.choice(big))], start, dur,
            rng.uniform(2.0, 3.0), SLOW)

    n_target = max(3, span // 4) if incremental else max(30, int(round(7 * span / 365.25)))
    malaria_idx = np.flatnonzero(weights[:, C.D_MALARIA] > 1.0)
    attempts = 0
    while len(obs) < n_target and attempts < 50 * n_target:
        attempts += 1
        dz = int(rng.choice(4, p=[0.3, 0.3, 0.2, 0.2]))
        if dz == C.D_MALARIA and malaria_idx.size and rng.random() < 0.8:
            i = int(rng.choice(malaria_idx))
        elif rng.random() < 0.3:
            i = int(rng.integers(len(codes)))  # any district, including small ones (hard to detect)
        else:
            w = np.sqrt(pop * weights[:, dz])
            i = int(rng.choice(len(codes), p=w / w.sum()))
        if incremental:
            dur = int(rng.integers(7, 29))
            start = int(rng.integers(first, max(first + 1, last - 2)))
        else:
            dur = int(rng.integers(14, 50))
            start = int(rng.integers(first + 14, max(first + 15, last - 14)))
        if overlaps(codes[i], dz, start, start + dur):
            continue
        add(dz, codes[i], start, dur, rng.uniform(1.5, 6.0), FAST)
    return obs


def plan_artefacts(facilities, seed, first, last):
    """Silent-then-backfill facilities: 3 weeks of silence, then all held reports arrive in one day."""
    span = last - first + 1
    if span < 10:
        return []
    rng = np.random.default_rng([seed, C.STREAM_PLAN, first, last, 7])
    silent = min(21, span // 2)
    n = 2 if span >= 365 else 1
    big = facilities[facilities.facility_type.isin(["district_hospital", "medical_college_hospital"])]
    if big.empty:
        big = facilities
    pick = big.sample(n=min(n, len(big)), random_state=int(rng.integers(2**31))).reset_index(drop=True)
    out = []
    for r in pick.itertuples():
        hi = last - silent  # back-fill day must stay inside the window
        lo = first + min(30, max(0, hi - first))
        out.append(Artefact(r.facility_id, r.district_code, int(rng.integers(lo, hi + 1)), silent))
    return out
