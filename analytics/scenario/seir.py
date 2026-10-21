"""Epidemic scenario module. Implements AN-10 (could-have).

A deterministic SEIR model calibrated on the novel-respiratory surge seen in the data, with what-if intervention
scenarios.

1. Find the surge without ground truth: national weekly ILI cases from gold.cases_weekly, minus a seasonal
   baseline (median of the same ISO week in the other years). The surge is the run of weeks around the largest
   excess where the excess stays above SURGE_SHARE of its peak.
2. Calibrate: SEIR with a daily Euler step, population N = national population, latent and infectious periods
   fixed (LATENT_DAYS, INFECTIOUS_DAYS, ASSUMPTION); fit R0, the seed I0 and the reporting share rho (reported
   excess = rho x new infections per week) by least squares on the surge weeks.
3. Scenarios: from the week the surge was first detectable (SURGE_SHARE of peak), contacts are cut by 20% or
   40%, starting then or two weeks later. For each: peak weekly reported cases, peak week, total reported cases
   from the first surge week to 12 weeks after its last, and the change against no intervention.

Outputs in <EPISCOPE_RESULTS_DIR>: seir_scenarios.json (fit, scenarios), seir_curves.csv (weekly curves).

Run: python -m analytics.scenario.seir
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from analytics.forecast import io

LATENT_DAYS = 3.0       # ASSUMPTION. Typical of novel respiratory viruses (SARS-CoV-2 ancestral strain: 3-5 days).
INFECTIOUS_DAYS = 5.0   # ASSUMPTION. Same source of judgement; R0 is fitted, so only the generation time is fixed.
SURGE_SHARE = 0.10      # surge weeks: excess above 10% of the peak excess
EXTRA_WEEKS = 12        # scenario horizon beyond the last surge week
SCENARIOS = [           # (name, contact reduction, start delay in weeks after first detection)
    ("no intervention", 0.0, 0),
    ("20% fewer contacts from detection", 0.2, 0),
    ("40% fewer contacts from detection", 0.4, 0),
    ("40% fewer contacts, 2 weeks late", 0.4, 2),
]


def national_series(cases):
    """Weekly national ILI counts with the seasonal baseline and the excess."""
    ili = cases[cases["disease"] == "ili"].groupby(["week_start", "epi_year", "epi_week"], as_index=False)["cases"].sum()
    ili = ili.sort_values("week_start").reset_index(drop=True)
    base = []
    for _, r in ili.iterrows():
        other = ili[(ili["epi_week"] == r["epi_week"]) & (ili["epi_year"] != r["epi_year"])]["cases"]
        base.append(float(other.median()) if len(other) else float("nan"))
    ili["baseline"] = base
    ili["excess"] = (ili["cases"] - ili["baseline"]).clip(lower=0).fillna(0.0)
    return ili


def surge_window(ili):
    """(first, last) row positions of the surge around the largest excess."""
    peak = int(ili["excess"].idxmax())
    thr = SURGE_SHARE * ili["excess"].iloc[peak]
    lo = peak
    while lo > 0 and ili["excess"].iloc[lo - 1] > thr:
        lo -= 1
    hi = peak
    while hi < len(ili) - 1 and ili["excess"].iloc[hi + 1] > thr:
        hi += 1
    return lo, hi


def simulate(r0, i0, n, weeks, reduction=0.0, start_day=None):
    """New infections per week from a daily-step SEIR; contacts fall by `reduction` from `start_day` on."""
    sigma, gamma = 1.0 / LATENT_DAYS, 1.0 / INFECTIOUS_DAYS
    s, e, i = n - i0, 0.0, i0
    out = np.zeros(weeks)
    for day in range(weeks * 7):
        beta = r0 * gamma * (1.0 - (reduction if start_day is not None and day >= start_day else 0.0))
        inf = beta * s * i / n
        s, e, i = s - inf, e + inf - sigma * e, i + sigma * e - gamma * i
        out[day // 7] += inf
    return out


def fit(observed, n):
    """Least-squares fit of (R0, log I0, logit rho) to weekly reported excess. Returns a dict."""
    weeks = len(observed)
    scale = max(float(np.max(observed)), 1.0)

    def resid(p):
        r0, i0, rho = p[0], np.exp(p[1]), 1.0 / (1.0 + np.exp(-p[2]))
        return (rho * simulate(r0, i0, n, weeks) - observed) / scale

    best = None
    for r0 in (1.3, 1.8, 2.5):
        for rho in (-6.0, -3.0, 0.0):
            res = least_squares(resid, [r0, np.log(max(observed[0], 1.0) * 10), rho],
                                bounds=([1.01, np.log(1.0), -12.0], [6.0, np.log(n * 0.01), 6.0]))
            if best is None or res.cost < best.cost:
                best = res
    r0, i0, rho = best.x[0], float(np.exp(best.x[1])), float(1.0 / (1.0 + np.exp(-best.x[2])))
    pred = rho * simulate(r0, i0, n, weeks)
    ss_res = float(np.sum((observed - pred) ** 2))
    ss_tot = float(np.sum((observed - observed.mean()) ** 2)) or 1.0
    return {"r0": float(r0), "i0": i0, "rho": rho, "r2": 1.0 - ss_res / ss_tot, "pred": pred}


def run(cases, population):
    ili = national_series(cases)
    lo, hi = surge_window(ili)
    win = ili.iloc[lo:hi + 1].reset_index(drop=True)
    obs = win["excess"].to_numpy(float)
    f = fit(obs, population)
    weeks = len(win) + EXTRA_WEEKS  # scenarios run past the observed surge, so a delayed peak is not cut off
    dates = pd.to_datetime(win["week_start"].iloc[0]) + pd.to_timedelta(7 * np.arange(weeks), unit="D")
    pad = np.full(EXTRA_WEEKS, np.nan)
    out, curves = [], {"week_start": dates.strftime("%Y-%m-%d"), "observed_excess": np.r_[obs, pad],
                       "fitted": np.r_[f["pred"], pad]}
    for name, red, delay in SCENARIOS:
        start = None if red == 0 else delay * 7
        rep = f["rho"] * simulate(f["r0"], f["i0"], population, weeks, red, start)
        curves[name] = rep
        out.append({"scenario": name, "contact_reduction": red, "start_delay_weeks": delay,
                    "peak_weekly_cases": float(rep.max()), "peak_week": dates[int(rep.argmax())].strftime("%Y-%m-%d"),
                    "total_cases": float(rep.sum())})
    base = out[0]
    for s in out:
        s["peak_change_pct"] = 100.0 * (s["peak_weekly_cases"] / base["peak_weekly_cases"] - 1.0)
        s["total_change_pct"] = 100.0 * (s["total_cases"] / base["total_cases"] - 1.0)
    summary = {
        "surge": {"first_week": str(win["week_start"].iloc[0]), "last_week": str(win["week_start"].iloc[-1]),
                  "weeks": len(win), "peak_week": str(win["week_start"].iloc[int(obs.argmax())]),
                  "peak_excess": float(obs.max()), "total_excess": float(obs.sum())},
        "fit": {"r0": f["r0"], "seed_infections": f["i0"], "reporting_share": f["rho"], "r2": f["r2"],
                "latent_days": LATENT_DAYS, "infectious_days": INFECTIOUS_DAYS, "population": population},
        "scenarios": out,
        "note": ("Scenarios start at the first surge week (excess above 10% of its peak), i.e. when detection could "
                 "first have flagged it. Deterministic model: no uncertainty band, and contacts are cut uniformly."),
    }
    return summary, pd.DataFrame(curves)


def main():
    from pipeline.spark import get_spark

    io.limit_threads()
    spark = get_spark("episcope-seir")
    try:
        cases = (io.read_gold(spark, "gold.cases_weekly").select("disease", "week_start", "epi_year", "epi_week", "cases")
                 .where("disease = 'ili'").toPandas())
    finally:
        spark.stop()
    from analytics.optimise.run import load_reference

    pop, _ = load_reference([])  # national population from data/reference/districts.csv
    summary, curves = run(cases, float(sum(pop.values())))
    out = io.results_dir()
    (out / "seir_scenarios.json").write_text(json.dumps(summary, indent=2) + "\n")
    curves.to_csv(out / "seir_curves.csv", index=False)
    s = summary
    print(f"surge {s['surge']['first_week']}..{s['surge']['last_week']}, R0 {s['fit']['r0']:.2f}, "
          f"R2 {s['fit']['r2']:.2f}; " + "; ".join(f"{x['scenario']}: peak {x['peak_change_pct']:+.0f}%"
                                                   for x in s["scenarios"]))


if __name__ == "__main__":
    main()
