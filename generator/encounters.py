"""S4 encounter kernel: vectorised numpy generation for one (onset year, district group) task.

Implements ING-1 (25M+ realistic encounters, deterministic) and plants the defects for DQ-1/DQ-2.

Model per district d, disease k, onset day t (research.md §8.1):
    lambda = SF * rate_k * pop_d * w_dk * season_k(doy) * climate_dk(t)
    count ~ NegBin(mean lambda, dispersion alpha_k)           (Poisson-gamma mixture)
Climate links (weather from data/reference, climatology outside its range):
    dengue    exp(0.25 z_rain + 0.25 z_temp), rain and temperature averaged over days t-56..t-28 (4-8 weeks)
    diarrhoea 1 + 1.2 * sum_l w_l e_{t-l}, e = clip((precip - 50 mm) / 30, 0, 4), lags 1-10 days
    ILI       exp(0.15 z_rh7)
    malaria   exp(0.20 z_rain), rain averaged over days t-42..t-14
Outbreak excess is drawn as separate rows (see outbreaks.py), so the base rows for a given seed are the same
whatever date window is requested; that keeps an incremental drop consistent with the full run.

Randomness is keyed per (seed, stream, district, year, month[, outbreak]) (research.md §8.2), so the output
does not depend on worker count or task order. Each month is generated in full and then filtered to the
requested report-date window.
"""

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
from scipy.stats import nbinom

from generator import calendar_bd as cal
from generator import config as C
from generator.outbreaks import curve
from generator.reference import crc

SEXES = ["M", "F"] + [s for s in C.INVALID_SEXES.tolist()]
FIELDS = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex",
          "icd10_code", "lab_confirmed", "admitted", "outcome"]


def delay_pmf():
    p = C.DELAY_SIZE / (C.DELAY_SIZE + C.DELAY_MEAN)
    pmf = nbinom.pmf(np.arange(C.DELAY_MAX + 1), C.DELAY_SIZE, p)
    return pmf / pmf.sum()


def season_table():
    doy = np.arange(1, 367)
    tab = np.zeros((len(C.DISEASES), 367))
    for k, (bumps, floor) in C.SEASON.items():
        s = np.full(doy.size, floor, dtype=float)
        for peak, kappa, w in bumps:
            s += w * np.exp(kappa * (np.cos(2 * np.pi * (doy - peak) / 365.25) - 1))
        s /= s[:365].mean()
        tab[k, 1:] = s
    return tab


def icd_table():
    """Global list of ICD strings: valid codes per disease, then the bad codes."""
    codes = []
    for k in range(len(C.DISEASES)):
        codes += [c for c in C.ICD[k][0] if c not in codes]
    return codes + [c for c in C.BAD_ICD if c not in codes]


class Ctx:
    """Everything a worker needs; built once in generate.py and shipped to each worker."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.season = season_table()
        self.delay_cdf = np.cumsum(delay_pmf())
        self.icd_codes = icd_table()
        self.icd_index = {c: i for i, c in enumerate(self.icd_codes)}
        self.bad_icd_idx = np.array([self.icd_index[c] for c in C.BAD_ICD])
        lo, hi = self.first - 120, self.last + 120
        self.cal_lo = lo
        hol = np.zeros(hi - lo + 1, dtype=bool)
        hd = self.holidays[(self.holidays >= lo) & (self.holidays <= hi)]
        hol[hd - lo] = True
        self.is_holiday = hol
        wd = cal.weekday(np.arange(lo, hi + 1))
        work = ~hol & (wd != cal.FRIDAY) & (wd != cal.SATURDAY)
        wdays = np.flatnonzero(work)
        nxt = np.searchsorted(wdays, np.arange(hol.size))
        nxt = np.minimum(nxt, wdays.size - 1)
        self.next_work = wdays[nxt] + lo


def _doy(days):
    d = days.astype("datetime64[D]")
    return (d - d.astype("datetime64[Y]")).astype(int) + 1


def _rolling_mean(x, start_lag, end_lag):
    """Mean of x[t-end_lag .. t-start_lag] for each t, where x has end_lag days of history prepended."""
    cs = np.concatenate([[0.0], np.cumsum(x)])
    n = x.size - end_lag
    t = np.arange(n) + end_lag
    return (cs[t - start_lag + 1] - cs[t - end_lag]) / (end_lag - start_lag + 1)


def climate(ctx, code, days):
    """(5, n) multiplicative climate effect for the given consecutive int days."""
    hist = 60
    precip, tmean, rh = ctx.weather.get(code, int(days[0]) - hist, int(days[-1]))
    n = days.size
    out = np.ones((len(C.DISEASES), n))
    rain_d = _rolling_mean(precip, 28, 56)[-n:]
    temp_d = _rolling_mean(tmean, 28, 56)[-n:]
    z_r = np.clip((rain_d - 7.0) / 6.0, -1.5, 2.5)
    z_t = np.clip((temp_d - 26.0) / 3.0, -2.0, 1.5)
    out[C.D_DENGUE] = np.exp(0.25 * z_r + 0.25 * z_t)
    e = np.clip((precip - 50.0) / 30.0, 0, 4)
    lags = np.arange(1, 11)
    w = lags * np.exp(-lags / 2.5)
    w /= w.sum()
    kx = np.zeros(n)
    for lag, wl in zip(lags, w):
        kx += wl * e[hist - lag: hist - lag + n]
    out[C.D_DIARRHOEA] = 1 + 1.2 * kx
    rh7 = _rolling_mean(rh, 0, 6)[-n:]
    out[C.D_ILI] = np.exp(0.15 * np.clip((rh7 - 75.0) / 10.0, -2, 2))
    rain_m = _rolling_mean(precip, 14, 42)[-n:]
    out[C.D_MALARIA] = np.exp(0.20 * np.clip((rain_m - 7.0) / 6.0, -1.5, 2.5))
    return out


def base_mean(ctx, di, days):
    d = ctx.districts[di]
    per_day = ctx.sf * C.RATE_PER_1000 / 1000.0 / 365.25 * d["pop"] * d["w"]
    return per_day[:, None] * ctx.season[:, _doy(days)] * climate(ctx, d["code"], days)


def _nb(rng, mean, alpha):
    mean = np.maximum(mean, 0)
    lam = rng.gamma(1.0 / alpha, mean * alpha) if np.isscalar(alpha) else \
        rng.gamma(1.0 / alpha[:, None], mean * alpha[:, None])
    return rng.poisson(lam)


def make_rows(ctx, rng, di, onset, dz, src, outbreak=False):
    """Expand onset days + disease into full encounter rows (dict of numpy columns)."""
    n = onset.size
    d = ctx.districts[di]
    band = np.empty(n, dtype=np.int8)
    male = np.empty(n, dtype=bool)
    icd = np.empty(n, dtype=np.int16)
    for k in np.unique(dz):
        m = dz == k
        cell = np.searchsorted(d["agesex_cdf"][k], rng.random(m.sum()), side="right")
        cell = np.minimum(cell, 11)
        band[m] = cell % 6
        male[m] = cell < 6
        codes, probs = (C.ICD_OUTBREAK.get(k) if outbreak else None) or C.ICD[k]
        pick = rng.choice(len(codes), size=m.sum(), p=np.asarray(probs) / np.sum(probs))
        icd[m] = np.array([ctx.icd_index[c] for c in codes])[pick]
    age = C.AGE_LO[band] + np.floor(rng.random(n) * (C.AGE_HI[band] - C.AGE_LO[band] + 1)).astype(int)
    old = band == 5
    age[old] = np.minimum(65 + np.floor(rng.exponential(8.0, old.sum())).astype(int), 100)
    sex = np.where(male, 0, 1).astype(np.int8)

    fac = d["fac_idx"][np.minimum(np.searchsorted(d["fac_cdf"], rng.random(n), side="right"),
                                  d["fac_idx"].size - 1)]
    patient = rng.integers(1_000_000_000, 10_000_000_000, n, dtype=np.int64)
    rep = np.flatnonzero(rng.random(n) < C.REPEAT_PATIENT_RATE)
    if rep.size and n > 1:
        patient[rep] = patient[rng.integers(0, n, rep.size)]

    # severity cascade
    code = np.array(ctx.icd_codes, dtype=object)[icd]
    lab_p = C.LAB_P[dz].copy()
    lab_p[code == "J10"] = 1.0
    lab_p[code == "A00"] = 0.6
    lab = rng.random(n) < lab_p
    adm_p = C.ADMIT_P[dz] * np.where((band == 0) | (band == 5), 1.8, 1.0)
    adm_p = adm_p * np.where((dz == C.D_DENGUE) & lab, 1.3, 1.0)
    adm_p[code == "A91"] = 0.9
    adm_p = adm_p * np.where(code == "A00", 2.0, 1.0)
    admitted = rng.random(n) < np.minimum(adm_p, 0.95)
    u = rng.random(n)
    p_die = np.where(admitted, C.DIED_ADMITTED_P[dz] * np.where(code == "A91", 4.0, 1.0),
                     C.DIED_OUTPATIENT_P) * np.where(band == 5, 3.0, 1.0)
    p_ref = np.where(admitted, C.REFER_ADMITTED_P, C.REFER_OUTPATIENT_P)
    p_unk = np.where(admitted, C.UNKNOWN_ADMITTED_P, C.UNKNOWN_OUTPATIENT_P)
    outcome = np.zeros(n, dtype=np.int8)  # recovered
    outcome[u < p_die + p_ref + p_unk] = 3
    outcome[u < p_die + p_ref] = 1
    outcome[u < p_die] = 2

    # reporting delay, Fri/Sat and holiday dip on report_date only
    delay = np.searchsorted(ctx.delay_cdf, rng.random(n), side="right").clip(0, C.DELAY_MAX)
    report = onset + delay
    orig = report.copy()
    wd = cal.weekday(report)
    wk = ((wd == cal.FRIDAY) | (wd == cal.SATURDAY)) & (rng.random(n) < C.WEEKEND_SHIFT_P)
    report = np.where(wk, report + (cal.SUNDAY - wd), report)
    ri = np.clip(report - ctx.cal_lo, 0, ctx.is_holiday.size - 1)
    hol = ctx.is_holiday[ri] & (rng.random(n) < C.HOLIDAY_SHIFT_P)
    report = np.where(hol, ctx.next_work[ri], report)
    report = np.where(report - onset > C.DELAY_MAX, orig, report)

    # per (onset day) sequence number -> deterministic encounter_id
    order = np.argsort(onset, kind="stable")
    so = onset[order]
    first_pos = np.searchsorted(so, so, side="left")
    seq = np.empty(n, dtype=np.int32)
    seq[order] = np.arange(n) - first_pos + 1

    rows = {
        "dist": np.full(n, di, dtype=np.int16), "onset": onset.astype(np.int32),
        "idday": onset.astype(np.int32),  # true onset, used in encounter_id; defects never touch it
        "src": np.full(n, src, dtype=np.int16), "seq": seq, "patient": patient, "fac": fac.astype(np.int32),
        "report": report.astype(np.int32), "age": age.astype(np.int16), "sex": sex, "icd": icd,
        "lab": lab, "admitted": admitted, "outcome": outcome,
    }
    return _defects(ctx, rng, d, rows)


def _defects(ctx, rng, d, rows):
    n = rows["onset"].size
    if n == 0:
        return rows
    # resubmitted duplicates: same encounter_id, reported again 0-3 days later, outcome sometimes updated
    dup = np.flatnonzero(rng.random(n) < C.DUP_RATE / (1 - C.DUP_RATE))
    if dup.size:
        copies = {k: v[dup].copy() for k, v in rows.items()}
        copies["report"] = copies["report"] + rng.choice([0, 0, 1, 2, 3], dup.size)
        upd = (copies["outcome"] == 3) & (rng.random(dup.size) < 0.5)
        copies["outcome"][upd] = 0
        rows = {k: np.concatenate([rows[k], copies[k]]) for k in rows}
        n = rows["onset"].size
    m = rng.random(n) < C.ORPHAN_RATE
    rows["fac"][m] = d["orphan_idx"][rng.integers(0, d["orphan_idx"].size, m.sum())]
    m = rng.random(n) < C.INVALID_AGE_RATE
    rows["age"][m] = rng.choice(C.INVALID_AGES, m.sum())
    m = rng.random(n) < C.INVALID_SEX_RATE
    rows["sex"][m] = rng.integers(2, len(SEXES), m.sum())
    m = rng.random(n) < C.BAD_ICD_RATE
    rows["icd"][m] = rng.choice(ctx.bad_icd_idx, m.sum())
    m = rng.random(n) < C.ONSET_AFTER_REPORT_RATE
    rows["onset"][m] = rows["report"][m] + rng.integers(1, 6, m.sum())
    return rows


def _months(year, lo, hi):
    """(first_day, last_day) int pairs for whole calendar months of `year` overlapping [lo, hi]."""
    out = []
    for m in range(1, 13):
        a = np.datetime64(f"{year}-{m:02d}-01").astype(np.int64)
        b = (np.datetime64(f"{year}-{m:02d}") + 1).astype("datetime64[D]").astype(np.int64) - 1
        if b >= lo and a <= hi:
            out.append((m, int(a), int(b)))
    return out


def generate_task(ctx, year, group):
    """All rows with onset in `year` for districts in `group`, filtered to the report window."""
    lo = ctx.first - C.DELAY_MAX - 4  # onsets this early can still report inside the window
    parts = []
    for di in ctx.groups[group]:
        d = ctx.districts[di]
        key = crc(d["code"])
        for month, a, b in _months(year, lo, ctx.last):
            days = np.arange(a, b + 1)
            mean = base_mean(ctx, di, days)
            rng = np.random.default_rng([ctx.seed, C.STREAM_BASE, key, year, month])
            counts = _nb(rng, mean, C.NB_ALPHA)
            dz = np.repeat(np.repeat(np.arange(len(C.DISEASES)), days.size), counts.ravel())
            onset = np.repeat(np.tile(days, len(C.DISEASES)), counts.ravel())
            parts.append(make_rows(ctx, rng, di, onset, dz, 0))
            for ob in ctx.outbreaks:
                if ob.district_code not in ("ALL", d["code"]) or ob.start > b or ob.end < a:
                    continue
                g = curve(ob, days, d["code"])
                if not g.any():
                    continue
                k = ob.disease
                floor = ctx.sf * C.RATE_PER_1000[k] / 1000.0 / 365.25 * d["pop"] * d["w"][k]
                excess = (ob.peak_multiplier - 1) * g * np.maximum(mean[k], floor)
                orng = np.random.default_rng([ctx.seed, C.STREAM_OUTBREAK, key, year, month, ob.ordinal])
                cnt = _nb(orng, excess, C.NB_ALPHA_OUTBREAK)
                on = np.repeat(days, cnt)
                parts.append(make_rows(ctx, orng, di, on, np.full(on.size, k), ob.ordinal, outbreak=True))
    rows = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]} if parts else None
    if rows is None:
        return None
    for art in ctx.artefacts:
        m = (rows["fac"] == art["fac"]) & (rows["report"] >= art["start"]) & \
            (rows["report"] < art["start"] + art["days"])
        rows["report"][m] = art["start"] + art["days"]
    keep = (rows["report"] >= ctx.first) & (rows["report"] <= ctx.last)
    rows = {k: v[keep] for k, v in rows.items()}
    order = np.lexsort((rows["seq"], rows["src"], rows["idday"], rows["dist"], rows["report"]))
    return {k: v[order] for k, v in rows.items()}


def _strings(table, idx):
    return pc.cast(pa.DictionaryArray.from_arrays(pa.array(idx, type=pa.int32()), pa.array(table)), pa.string())


def to_arrow(ctx, rows):
    onset = rows["onset"].astype("datetime64[D]")
    idday = rows["idday"].astype("datetime64[D]")
    y = idday.astype("datetime64[Y]").astype(int) + 1970
    mth = idday.astype("datetime64[M]").astype(int) % 12 + 1
    dd = (idday - idday.astype("datetime64[M]")).astype(int) + 1
    ymd = pa.array(y * 10000 + mth * 100 + dd)
    src_tab = ["B"] + [f"O{k:03d}" for k in range(1, ctx.max_src + 1)]
    enc = pc.binary_join_element_wise(
        f"E{ctx.seed}-", _strings(ctx.codes, rows["dist"]), "-", pc.cast(ymd, pa.string()), "-",
        _strings(src_tab, rows["src"]), pc.utf8_lpad(pc.cast(pa.array(rows["seq"]), pa.string()), 6, "0"), "")
    return pa.table({
        "encounter_id": enc,
        "patient_id": pc.binary_join_element_wise("P", pc.cast(pa.array(rows["patient"]), pa.string()), ""),
        "facility_id": _strings(ctx.fac_ids, rows["fac"]),
        "onset_date": pa.array(onset, type=pa.date32()),
        "report_date": pa.array(rows["report"].astype("datetime64[D]"), type=pa.date32()),
        "age_years": pa.array(rows["age"].astype(np.int32)),
        "sex": _strings(SEXES, rows["sex"]),
        "icd10_code": _strings(ctx.icd_codes, rows["icd"]),
        "lab_confirmed": pa.array(rows["lab"]),
        "admitted": pa.array(rows["admitted"]),
        "outcome": _strings(C.OUTCOMES, rows["outcome"]),
    })


def write_task(ctx, year, group):
    """Generate one task and write it as report_date=YYYY-MM-DD/part-{year}-{group}.csv files."""
    rows = generate_task(ctx, year, group)
    if rows is None or rows["report"].size == 0:
        return 0
    table = to_arrow(ctx, rows)
    rep = rows["report"]
    cuts = np.flatnonzero(np.diff(rep)) + 1
    starts = np.concatenate([[0], cuts])
    ends = np.concatenate([cuts, [rep.size]])
    opts = pacsv.WriteOptions(include_header=True)
    for s, e in zip(starts, ends):
        day = str(np.datetime64(int(rep[s]), "D"))
        path = ctx.out / "encounters" / f"report_date={day}"
        path.mkdir(parents=True, exist_ok=True)
        pacsv.write_csv(table.slice(s, e - s), path / f"part-{year}-{group}.csv", opts)
    return int(rep.size)
