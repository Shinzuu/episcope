"""`python -m analytics.evaluation.run`: score detection against injected outbreaks and write the C1 trade-off
curve. Implements AN-2.

Reads gold.cases_weekly (to re-score every grid point) and ground_truth/outbreaks.csv (the only reader of that
file). Writes, under EPISCOPE_DATA_DIR/evaluation/ unless --out says otherwise:
    detection_tradeoff.csv   one row per (method, threshold): sensitivity, false alerts per district-year, ...
    detection_tradeoff.json  the same curve, the recommended operating points, per-shape results, definitions
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

import pandas as pd

from analytics.detection import config as DC
from analytics.detection.methods import CUSUM_INPUT, ears_c2, score_inputs
from analytics.detection.prepare import prepare
from analytics.evaluation import metrics, sweep

ROOT = DC.ROOT
DATA = pathlib.Path(os.environ.get("EPISCOPE_DATA_DIR", ROOT / "data"))
if not DATA.is_absolute():
    DATA = (ROOT / DATA).resolve()
OUTBREAKS = ROOT / "ground_truth" / "outbreaks.csv"


def evaluate_frames(prepared_pd, ears_pd, outbreaks, budget=sweep.DEFAULT_BUDGET):
    """Everything except I/O: curve DataFrame and the JSON-able report."""
    curve = pd.concat([sweep.sweep_ears(ears_pd, outbreaks), sweep.sweep_cusum(prepared_pd, outbreaks)],
                      ignore_index=True)
    rec = sweep.recommend(curve, budget)

    by_shape = {}
    for method, r in rec.items():
        if method == "ears_c2":
            a = ears_pd.assign(alert=(ears_pd["statistic"].fillna(-1e18) > r["param"]) & ears_pd["gate"].astype(bool))
        else:
            a = sweep.cusum_scored(prepared_pd, r["param"])
        m = metrics.evaluate(a, outbreaks)
        by_shape[method] = m["by_shape"]
        r["missed_outbreaks"] = sorted(m["per_outbreak"].loc[~m["per_outbreak"]["detected"], "outbreak_id"].tolist())

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "data_weeks": {"first": str(ears_pd["week_start"].min())[:10], "last": str(ears_pd["week_start"].max())[:10]},
        "false_alert_budget_per_district_year": budget,
        "recommended": rec,
        "by_shape_at_recommended": by_shape,
        "curve": curve.where(curve.notna(), None).to_dict(orient="records"),
        "definitions": metrics.__doc__.strip(),
    }
    return curve, report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cases", default=str(DC.CASES_WEEKLY_PATH))
    p.add_argument("--outbreaks", default=str(OUTBREAKS))
    p.add_argument("--out", default=str(DATA / "evaluation"))
    p.add_argument("--budget", type=float, default=sweep.DEFAULT_BUDGET,
                   help="false alert episodes per district-year allowed at the operating point")
    a = p.parse_args(argv)

    from analytics.detection.run import read_cases
    from pipeline.spark import get_spark

    # applyInPandas workers must use this interpreter (the venv), not the system python3.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

    outbreaks = metrics.load_outbreaks(a.outbreaks)
    spark = get_spark("episcope-evaluation")
    try:
        inputs = score_inputs(prepare(read_cases(spark, a.cases))).cache()
        prepared_pd = inputs.select(*DC.GROUP_KEYS, *CUSUM_INPUT, "gate").toPandas()
        ears_pd = ears_c2(inputs).toPandas()
    finally:
        spark.stop()

    curve, report = evaluate_frames(prepared_pd, ears_pd, outbreaks, a.budget)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    curve.to_csv(out / "detection_tradeoff.csv", index=False)
    (out / "detection_tradeoff.json").write_text(json.dumps(report, indent=1, default=str))
    print(f"trade-off curve -> {out}/detection_tradeoff.csv, .json")
    for method, r in report["recommended"].items():
        print(f"  {method}: {r['param_name']}={r['param']}  sensitivity={r['sensitivity']:.2f}  "
              f"false/district-year={r['false_alerts_per_district_year']:.2f}  "
              f"median delay={r['median_days_to_detection']} d  budget met={r['budget_met']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
