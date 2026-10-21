"""S6 stock feed: one JSON line per facility per snapshot_date, as a daily incremental feed.

Implements ING-1 (S6 stock snapshots) and feeds gold.stock_latest for AN-6/AN-7.

Stock follows the simulated case load (allocation_diagnosis.md explains why the earlier bed-sized model gave a
national net need of zero). For every facility and day:
- expected cases per disease are the district's encounter intensity (base, climate and outbreak excess, the same
  formulas as encounters.py, at the run's SF) times the facility's share of the district's encounters;
- expected use per resource = cases x USE_PER_CASE (IV saline per admitted case, NS1 per dengue case, ORS per
  diarrhoea case, antimalarials per malaria case); actual use is Poisson around it and capped by what is on hand;
- inventory is a min/max policy on the facility's own recent use: par level = COVER_DAYS x the trailing 28-day
  mean use; below REORDER_AT x par an order is placed, it arrives 5-14 days later and is filled 70-100%
  (central shortfalls). A rise in cases (season, outbreak) therefore drains stock before the next delivery;
- occupied beds = beds x a fixed share for other conditions + modelled inpatients (admissions x length of stay).
About 1% of facility-days are missing from the feed.
"""

import numpy as np

from generator import config as C
from generator.outbreaks import curve

RESOURCES = ["iv_saline_units", "ns1_kits", "ors_packets", "antimalarial_courses"]
# Units used per case of each disease (rows: resources above; columns: C.DISEASES). Generator truth; the
# analytics side estimates the same quantities independently (analytics/forecast/demand_assumptions.py).
USE_PER_CASE = np.zeros((4, len(C.DISEASES)))
USE_PER_CASE[0, C.D_DENGUE] = 4.0 * C.ADMIT_P[C.D_DENGUE]        # 4 L IV fluid per admitted dengue case
USE_PER_CASE[0, C.D_DIARRHOEA] = 4.0 * C.ADMIT_P[C.D_DIARRHOEA]  # WHO Plan C, about 4 L per admitted case
USE_PER_CASE[0, C.D_MALARIA] = 2.0 * C.ADMIT_P[C.D_MALARIA]      # maintenance fluids, severe malaria
USE_PER_CASE[1, C.D_DENGUE] = 1.5                                # NS1 tests per reported dengue case
USE_PER_CASE[2, C.D_DIARRHOEA] = 6.0                             # ORS sachets per diarrhoea case
USE_PER_CASE[3, C.D_MALARIA] = 1.1                               # ACT courses per malaria case
LOS_DAYS = np.zeros(len(C.DISEASES))
LOS_DAYS[C.D_DENGUE], LOS_DAYS[C.D_DIARRHOEA], LOS_DAYS[C.D_MALARIA] = 5.0, 2.0, 4.0
COVER_DAYS = 21.0        # par level in days of recent use
REORDER_AT = 0.35        # order when stock falls below this share of par
LEAD_DAYS = (5, 15)      # delivery 5-14 days after the order
FILL = (0.7, 1.0)        # share of the order that arrives
MIN_PAR_SHARE = 0.25     # par never below this share of the facility's long-run mean (no zero par in a lull)
OTHER_OCCUPANCY = (0.50, 0.70)  # bed share used by conditions the generator does not model
MISSING_RATE = 0.01


def expected_cases(ctx, facilities, days):
    """[n_days, n_fac, n_diseases] expected cases by onset day, from the encounter model."""
    from generator.encounters import base_mean

    out = np.zeros((days.size, len(facilities), len(C.DISEASES)))
    for di, d in enumerate(ctx.districts):
        mean = base_mean(ctx, di, days)  # [n_diseases, n_days]
        tot = mean.copy()
        for ob in ctx.outbreaks:
            if ob.district_code not in ("ALL", d["code"]) or ob.start > days[-1] or ob.end < days[0]:
                continue
            g = curve(ob, days, d["code"])
            if g.any():
                k = ob.disease
                floor = ctx.sf * C.RATE_PER_1000[k] / 1000.0 / 365.25 * d["pop"] * d["w"][k]
                tot[k] += (ob.peak_multiplier - 1) * g * np.maximum(mean[k], floor)
        idx = np.asarray(d["fac_idx"])
        idx_ok = idx < len(facilities)  # orphan IDs are not real facilities
        if not idx_ok.any():
            continue
        share = np.diff(np.r_[0.0, d["fac_cdf"]])[idx_ok]
        out[:, idx[idx_ok], :] += tot.T[:, None, :] * share[None, :, None]
    return out


def _trailing_mean(x, window):
    """Mean over the last `window` rows (fewer at the start), along axis 0."""
    c = np.cumsum(x, axis=0)
    c = np.concatenate([np.zeros((1,) + x.shape[1:]), c])
    n = np.arange(1, x.shape[0] + 1)
    lo = np.maximum(n - window, 0)
    return (c[n] - c[lo]) / (n - lo)[:, None, None]


def simulate(ctx, facilities):
    """Return (days int array, values int array [n_days, n_fac, 5], present bool [n_days, n_fac])."""
    days = np.arange(ctx.first, ctx.last + 1)
    n_d, n_f = days.size, len(facilities)
    rng = np.random.default_rng([ctx.seed, C.STREAM_STOCK, ctx.first, ctx.last])
    cases = expected_cases(ctx, facilities, days)                  # [n_d, n_f, n_dz]
    use_exp = np.einsum("tfk,rk->tfr", cases, USE_PER_CASE)        # [n_d, n_f, 4]
    recent = _trailing_mean(use_exp, 28)
    par = COVER_DAYS * np.maximum(recent, MIN_PAR_SHARE * use_exp.mean(axis=0, keepdims=True))
    level = np.round(par[0] * rng.uniform(0.6, 1.0, (n_f, 4)))
    pending_day = np.full((n_f, 4), -1)
    pending_qty = np.zeros((n_f, 4))
    beds = facilities.beds.values.astype(float)
    other = beds * rng.uniform(*OTHER_OCCUPANCY, n_f)
    inpatients = np.einsum("tfk,k->tf", cases, C.ADMIT_P * LOS_DAYS)  # steady-state census of modelled inpatients
    out = np.zeros((n_d, n_f, 5), dtype=np.int64)
    occ_noise = rng.normal(0, 0.05, (n_d, n_f))
    for t in range(n_d):
        arrive = pending_day == t
        level[arrive] += pending_qty[arrive]
        pending_day[arrive], pending_qty[arrive] = -1, 0.0
        use = np.minimum(rng.poisson(use_exp[t]), level)
        level = level - use
        low = (level < REORDER_AT * par[t]) & (pending_day < 0) & (par[t] >= 1)
        pending_day[low] = t + rng.integers(*LEAD_DAYS, low.sum())
        pending_qty[low] = np.round((par[t][low] - level[low]) * rng.uniform(*FILL, low.sum()))
        out[t, :, :4] = level
        occ = (other + inpatients[t]) * (1 + occ_noise[t])
        out[t, :, 4] = np.round(np.clip(occ, 0.2 * beds, 1.3 * beds))
    present = rng.random((n_d, n_f)) >= MISSING_RATE
    return days, out, present


def write(ctx, facilities):
    days, vals, present = simulate(ctx, facilities)
    ids = facilities.facility_id.tolist()
    n = 0
    for t, day in enumerate(days):
        ds = str(np.datetime64(int(day), "D"))
        path = ctx.out / "stock" / f"snapshot_date={ds}"
        path.mkdir(parents=True, exist_ok=True)
        v = vals[t]
        lines = [
            f'{{"facility_id": "{ids[f]}", "snapshot_date": "{ds}", "iv_saline_units": {v[f, 0]}, '
            f'"ns1_kits": {v[f, 1]}, "ors_packets": {v[f, 2]}, "antimalarial_courses": {v[f, 3]}, '
            f'"occupied_beds": {v[f, 4]}}}'
            for f in np.flatnonzero(present[t])
        ]
        (path / "part-00000.json").write_text("\n".join(lines) + "\n")
        n += len(lines)
    return n
