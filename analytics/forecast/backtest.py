"""Rolling-origin backtest. Implements AN-5.

Origins: each of the last 52 weeks before the latest observed week (fewer if the history is short), FPP3 §5.10
expanding window. At every origin T the NB2 models forecast T+1..T+4 using only data <= T. The seasonal-naive
baseline (model.py) is scored on exactly the same (district, disease, origin, horizon) cells.

Scores (scoring.py): MAE of the median, 80% interval coverage (q10 <= y <= q90), WIS over K = 11 central
intervals plus the median (research.md §4.1), its decomposition, WIS over the 80% interval only (the quantiles
gold.forecasts publishes), and relative WIS = WIS_model / WIS_baseline (< 1 means the model beats the baseline).

Cost control: `refit_every` (default 4) refits each horizon's model every 4th origin and reuses it in between
with the features known at each origin. 64 districts x 2 diseases x 13 refits x 4 horizons is about 6.7k fits.

Outputs, in <EPISCOPE_RESULTS_DIR> (default docs/experiments):
    backtest_scores.csv     one row per scored cell
    backtest_metrics.csv    per disease x horizon, per disease, and overall
    backtest_summary.json   the same plus settings and fallback counts

Run: python -m analytics.forecast.backtest [--origins 52] [--refit-every 4]
"""

import argparse
import json

import numpy as np
import pandas as pd
from pyspark.sql import functions as Fn

from analytics.forecast import features as F
from analytics.forecast import io
from analytics.forecast.forecast import WEATHER_SCHEMA
from analytics.forecast.model import QUANTILES, WIS_ALPHAS, forecast_series, qcol
from analytics.forecast.scoring import coverage, mae, wis_from_quantiles

BACKTEST_DISEASES = ("dengue", "diarrhoea")
N_ORIGINS = 52
REFIT_EVERY = 4

_QCOLS = [qcol(q) for q in QUANTILES]
_COLS = (["district_code", "disease", "origin_week_start", "horizon", "target_week_start", "y", "method", "mean"]
         + _QCOLS + ["bl_method", "bl_mean"] + ["bl_" + c for c in _QCOLS])
SCHEMA = ", ".join(
    f"`{c}` " + ("string" if c in ("district_code", "disease", "method", "bl_method") else
                 "date" if c.endswith("week_start") else "int" if c == "horizon" else "double")
    for c in _COLS)


def choose_origins(start, end, n=N_ORIGINS):
    """The last n weekly origins strictly before `end`, keeping at least MIN_TRAIN_ROWS weeks of history."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    from analytics.forecast.model import MIN_TRAIN_ROWS

    first_ok = start + pd.Timedelta(weeks=MIN_TRAIN_ROWS + F.WEATHER_LAG_MAX)
    cands = [end - pd.Timedelta(weeks=k) for k in range(n, 0, -1)]
    return [o for o in cands if o >= first_ok]


def backtest_series(grid, origins, refit_every=REFIT_EVERY):
    fc = forecast_series(grid, origins, refit_every=refit_every, baseline=True)
    return fc[fc["y"].notna()] if len(fc) else fc


def make_district_backtester(diseases, start, end, origins, refit_every):
    start, end = pd.Timestamp(start), pd.Timestamp(end)

    def run_district(cases, weather):
        if cases.empty or not origins:
            return pd.DataFrame({c: pd.Series(dtype="object") for c in _COLS})
        out = []
        for dz in diseases:
            grid = F.weekly_grid(cases[cases["disease"] == dz], weather, start, end)
            bt = backtest_series(grid, origins, refit_every)
            if len(bt):
                bt = bt.assign(district_code=cases["district_code"].iloc[0], disease=dz)
                out.append(bt)
        if not out:
            return pd.DataFrame({c: pd.Series(dtype="object") for c in _COLS})
        df = pd.concat(out, ignore_index=True)
        for c in ("origin_week_start", "target_week_start"):
            df[c] = pd.to_datetime(df[c]).dt.date
        df["horizon"] = df["horizon"].astype("int32")
        return df[_COLS]

    return run_district


def run_backtest(spark, cases_df, weather_df=None, diseases=BACKTEST_DISEASES, n_origins=N_ORIGINS,
                 refit_every=REFIT_EVERY):
    """Returns the per-cell scores as a pandas DataFrame (small: districts x diseases x origins x 4)."""
    c = (cases_df.filter(Fn.col("disease").isin(list(diseases)))
         .select("district_code", "disease", "week_start", "cases", "reporting_facilities", "expected_facilities"))
    b = c.agg(Fn.min("week_start").alias("s"), Fn.max("week_start").alias("e")).first()
    if b is None or b["e"] is None:
        return pd.DataFrame(columns=_COLS)
    origins = choose_origins(b["s"], b["e"], n_origins)
    if weather_df is None:
        weather_df = spark.createDataFrame([], WEATHER_SCHEMA)
    w = weather_df.select("district_code", "epi_year", "epi_week", "precip_mm_sum", "tmean_c_avg")
    fn = make_district_backtester(list(diseases), b["s"], b["e"], origins, refit_every)
    return c.groupBy("district_code").cogroup(w.groupBy("district_code")).applyInPandas(fn, SCHEMA).toPandas()


def _metrics(df):
    if df.empty:
        return {"n": 0}
    m, bl = wis_from_quantiles(df, WIS_ALPHAS), wis_from_quantiles(df, WIS_ALPHAS, prefix="bl_")
    m80, bl80 = wis_from_quantiles(df, [0.2]), wis_from_quantiles(df, [0.2], prefix="bl_")
    q10, q50, q90 = qcol(0.1), qcol(0.5), qcol(0.9)
    out = {
        "n": int(len(df)),
        "mae_model": mae(df["y"], df[q50]), "mae_baseline": mae(df["y"], df["bl_" + q50]),
        "coverage80_model": coverage(df["y"], df[q10], df[q90]),
        "coverage80_baseline": coverage(df["y"], df["bl_" + q10], df["bl_" + q90]),
        "wis_model": float(np.mean(m["wis"])), "wis_baseline": float(np.mean(bl["wis"])),
        "wis80_model": float(np.mean(m80["wis"])), "wis80_baseline": float(np.mean(bl80["wis"])),
    }
    for part in ("dispersion", "overprediction", "underprediction"):
        out[f"wis_{part}_model"] = float(np.mean(m[part]))
        out[f"wis_{part}_baseline"] = float(np.mean(bl[part]))
    out["relative_wis"] = out["wis_model"] / out["wis_baseline"] if out["wis_baseline"] > 0 else float("nan")
    out["relative_mae"] = out["mae_model"] / out["mae_baseline"] if out["mae_baseline"] > 0 else float("nan")
    return out


def summarise(scores):
    """Metrics per disease x horizon, per disease (all horizons) and overall."""
    rows = []
    for (dz, h), g in scores.groupby(["disease", "horizon"]):
        rows.append({"disease": dz, "horizon": str(h), **_metrics(g)})
    for dz, g in scores.groupby("disease"):
        rows.append({"disease": dz, "horizon": "all", **_metrics(g)})
    rows.append({"disease": "all", "horizon": "all", **_metrics(scores)})
    return pd.DataFrame(rows)


def write_results(scores, settings):
    out = io.results_dir()
    s = scores.copy()
    s["wis_model"] = wis_from_quantiles(s, WIS_ALPHAS)["wis"] if len(s) else []
    s["wis_baseline"] = wis_from_quantiles(s, WIS_ALPHAS, prefix="bl_")["wis"] if len(s) else []
    keep = ["district_code", "disease", "origin_week_start", "horizon", "target_week_start", "y", "method",
            "mean", qcol(0.1), qcol(0.5), qcol(0.9), "bl_method", "bl_mean", "bl_" + qcol(0.1),
            "bl_" + qcol(0.5), "bl_" + qcol(0.9), "wis_model", "wis_baseline"]
    s[keep].to_csv(out / "backtest_scores.csv", index=False)
    metrics = summarise(scores)
    metrics.to_csv(out / "backtest_metrics.csv", index=False)
    summary = {
        "settings": settings,
        "methods_model": scores["method"].value_counts().to_dict() if len(scores) else {},
        "methods_baseline": scores["bl_method"].value_counts().to_dict() if len(scores) else {},
        "metrics": json.loads(metrics.to_json(orient="records")),
    }
    (out / "backtest_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return metrics


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--origins", type=int, default=N_ORIGINS)
    ap.add_argument("--refit-every", type=int, default=REFIT_EVERY)
    args = ap.parse_args(argv)
    io.limit_threads()
    from pipeline.spark import get_spark

    spark = get_spark("episcope-backtest")
    try:
        cases = io.read_gold(spark, "gold.cases_weekly")
        weather = io.read_gold(spark, "gold.weather_weekly") if io.gold_exists("gold.weather_weekly") else None
        scores = run_backtest(spark, cases, weather, n_origins=args.origins, refit_every=args.refit_every)
    finally:
        spark.stop()
    metrics = write_results(scores, {"origins": args.origins, "refit_every": args.refit_every,
                                     "diseases": list(BACKTEST_DISEASES), "wis_alphas": WIS_ALPHAS,
                                     "weather_lags_weeks": [F.WEATHER_LAG_MIN, F.WEATHER_LAG_MAX],
                                     "fourier_k": F.FOURIER_K, "level_window": F.LEVEL_WINDOW})
    print(metrics[["disease", "horizon", "n", "mae_model", "mae_baseline", "coverage80_model",
                   "wis_model", "wis_baseline", "relative_wis"]].to_string(index=False))


if __name__ == "__main__":
    main()
