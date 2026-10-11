"""Run every analytics stage in dependency order (ENG-1). Evaluation is separate (`make evaluate`)
because it is the only stage allowed to read ground truth (AN-2)."""

import runpy
import sys
import time

STAGES = [
    "analytics.detection.run",        # AN-1, AN-3
    "analytics.forecast.forecast",    # AN-4
    "analytics.forecast.backtest",    # AN-5
    "analytics.forecast.demand",      # AN-6
    "analytics.optimise.run",         # AN-7, AN-8
    "analytics.calendar.stl_calendar",  # AN-9
]


def main():
    for mod in STAGES:
        t0 = time.time()
        print(f"[run_all] {mod} ...", flush=True)
        sys.argv = [mod]
        runpy.run_module(mod, run_name="__main__")
        print(f"[run_all] {mod} done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
