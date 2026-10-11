"""S6 stock feed: one JSON line per facility per snapshot_date, as a daily incremental feed.

Implements ING-1 (S6 stock snapshots) and feeds gold.stock_latest for AN-6/AN-7.

Each facility holds stock sized by its beds. Daily use follows the disease seasonality that drives that
supply (NS1 kits and IV saline: dengue; ORS: diarrhoea; antimalarials: malaria, mostly in endemic
districts). When a line falls below a quarter of its maximum, a restock arrives 3-10 days later, so
stock-outs happen in peak season. About 1% of facility-days are missing from the feed.
"""

import numpy as np

from generator import config as C

RESOURCES = ["iv_saline_units", "ns1_kits", "ors_packets", "antimalarial_courses"]
MAX_PER_BED = np.array([30.0, 5.0, 60.0, 2.0])
DAYS_OF_STOCK = 20.0
MISSING_RATE = 0.01


def simulate(ctx, facilities, malaria_weight):
    """Return (days int array, values int array [n_days, n_fac, 5], present bool [n_days, n_fac])."""
    days = np.arange(ctx.first, ctx.last + 1)
    n_d, n_f = days.size, len(facilities)
    rng = np.random.default_rng([ctx.seed, C.STREAM_STOCK, ctx.first, ctx.last])
    beds = facilities.beds.values.astype(float)
    mx = beds[:, None] * MAX_PER_BED[None, :]
    mx[:, 3] *= malaria_weight  # antimalarials stocked where malaria is
    mx = np.maximum(np.round(mx), 0)
    doy = (days.astype("datetime64[D]") - days.astype("datetime64[D]").astype("datetime64[Y]")).astype(int) + 1
    s = ctx.season
    drive = np.stack([s[C.D_DENGUE, doy] * 0.6 + s[C.D_DIARRHOEA, doy] * 0.4, s[C.D_DENGUE, doy],
                      s[C.D_DIARRHOEA, doy], s[C.D_MALARIA, doy]], axis=1)  # [n_d, 4]
    level = np.round(mx * rng.uniform(0.5, 1.0, mx.shape))
    pending = np.full(mx.shape, -1)  # day index when a restock arrives
    out = np.zeros((n_d, n_f, 5), dtype=np.int64)
    occ_noise = rng.normal(0, 0.05, (n_d, n_f))
    for t in range(n_d):
        arrive = pending == t
        level[arrive] = mx[arrive]
        pending[arrive] = -1
        use = rng.poisson(mx / DAYS_OF_STOCK * drive[t][None, :])
        level = np.maximum(level - use, 0)
        low = (level < 0.25 * mx) & (pending < 0) & (mx > 0)
        pending[low] = t + rng.integers(3, 11, low.sum())
        out[t, :, :4] = level
        occ = 0.55 + 0.25 * (s[C.D_DENGUE, doy[t]] / s[C.D_DENGUE].max()) + occ_noise[t]
        out[t, :, 4] = np.round(beds * np.clip(occ, 0.2, 1.3))
    present = rng.random((n_d, n_f)) >= MISSING_RATE
    return days, out, present


def write(ctx, facilities, malaria_weight):
    days, vals, present = simulate(ctx, facilities, malaria_weight)
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


def facility_malaria_weight(facilities, district_weights):
    """Antimalarial stocking factor per facility from its district's malaria weight (capped)."""
    w = facilities.district_code.map(district_weights).fillna(0.0).values
    return np.clip(w / 5.0, 0.0, 3.0)

