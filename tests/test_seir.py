"""AN-10: the SEIR calibration recovers a known epidemic and interventions lower the peak."""

import numpy as np
import pandas as pd

from analytics.scenario import seir


def _cases(r0=1.8, n=1e7, weeks=150, start=100):
    """ILI weekly counts: flat seasonal background in three years plus an SEIR surge reported at 1%."""
    rng = np.random.default_rng(1)
    dates = pd.date_range("2022-01-03", periods=weeks, freq="7D")
    iso = dates.isocalendar()
    surge = np.zeros(weeks)
    surge[start:start + 30] = 0.01 * seir.simulate(r0, 50.0, n, 30)
    c = 200 + rng.poisson(5, weeks) + surge
    return pd.DataFrame({"disease": "ili", "week_start": dates.date, "epi_year": iso.year.values,
                         "epi_week": iso.week.values, "cases": c.round()})


def test_fit_recovers_r0_and_interventions_help():
    summary, curves = seir.run(_cases(), 1e7)
    assert abs(summary["fit"]["r0"] - 1.8) < 0.25 and summary["fit"]["r2"] > 0.9
    peaks = {s["scenario"]: s["peak_weekly_cases"] for s in summary["scenarios"]}
    names = [s[0] for s in seir.SCENARIOS]
    assert peaks[names[2]] < peaks[names[1]] < peaks[names[0]]   # more reduction, lower peak
    assert peaks[names[2]] <= peaks[names[3]]                      # acting late is worse than acting at detection
    assert len(curves) == summary["surge"]["weeks"] + seir.EXTRA_WEEKS
