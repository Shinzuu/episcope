"""Score detector alerts against injected outbreaks. Implements AN-2.

Definitions (Buehler et al. 2004 CDC framework; research.md §3.4). Stated here so the report can quote them:

- Alert week. A weekly alert for week_start W covers W .. W+6. Its count is complete at W+6, so that is the
  detection date.
- Outbreak window. [start_date, end_date + buffer_days] (default buffer 7 days). An alert week counts for an
  outbreak when it overlaps that window, in the same district and disease.
- Nationwide events (district_code = "ALL") are one event. Detected when any district alerts inside the window.
  Alerts in any district during the window are not false.
- Sensitivity = detected outbreaks / outbreaks in scope. In scope: the disease and district appear in the
  alert data and the window overlaps the evaluated weeks.
- Alert episode. Consecutive alert weeks in one (district, disease, method) merge into one episode.
- False alerts per district-year = episodes overlapping no outbreak window / (districts x weeks / 52.1775).
  The district-year covers all diseases of that district.
- PPV = episodes overlapping an outbreak / all episodes.
- Days to detection = max(0, detection date of the first alert in the window - start_date), detected
  outbreaks only. Missed outbreaks are reported as missed (censored), not given a delay. Also reported: the
  share of all in-scope outbreaks detected within 7 and 14 days.
"""

import datetime as dt

import numpy as np
import pandas as pd

WEEKS_PER_YEAR = 365.2425 / 7
OUTBREAK_COLS = ["outbreak_id", "disease", "district_code", "start_date", "end_date", "peak_multiplier", "shape"]


def load_outbreaks(path):
    df = pd.read_csv(path, dtype={"outbreak_id": str, "district_code": str, "disease": str, "shape": str})
    missing = [c for c in ["outbreak_id", "disease", "district_code", "start_date", "end_date"] if c not in df]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    df["start_date"] = pd.to_datetime(df["start_date"]).dt.normalize()
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.normalize()
    if "shape" not in df:
        df["shape"] = "unknown"
    return df


def with_week_start(alerts):
    """Ensure a datetime64 week_start column (derived from ISO epi_year / epi_week when absent)."""
    a = alerts.copy()
    if "week_start" not in a or a["week_start"].isna().all():
        a["week_start"] = [dt.date.fromisocalendar(int(y), int(w), 1) for y, w in zip(a["epi_year"], a["epi_week"])]
    a["week_start"] = pd.to_datetime(a["week_start"])
    a["alert"] = a["alert"].fillna(False).astype(bool)
    return a


def episodes(alerts):
    """Merge consecutive alert weeks per (district, disease, method) into episodes: start, end (week_start)."""
    a = alerts[alerts["alert"]].sort_values(["district_code", "disease", "method", "week_start"])
    if a.empty:
        return pd.DataFrame(columns=["district_code", "disease", "method", "ep_start", "ep_end"])
    key = ["district_code", "disease", "method"]
    gap = a.groupby(key)["week_start"].diff() != pd.Timedelta(days=7)
    a = a.assign(ep=gap.cumsum())
    ep = a.groupby(key + ["ep"])["week_start"].agg(ep_start="min", ep_end="max").reset_index()
    return ep.drop(columns="ep")


def _windows(outbreaks, districts, buffer_days):
    """One row per (outbreak, district) with the window; ALL expanded to every district."""
    rows = []
    buf = pd.Timedelta(days=buffer_days)
    for o in outbreaks.itertuples(index=False):
        ds = districts if o.district_code == "ALL" else [o.district_code]
        for d in ds:
            rows.append((o.outbreak_id, d, o.disease, o.start_date, o.end_date + buf))
    return pd.DataFrame(rows, columns=["outbreak_id", "district_code", "disease", "w_start", "w_end"])


def evaluate(alerts, outbreaks, buffer_days=7):
    """Metrics for ONE method's alerts (columns district_code, disease, week_start or epi_year/epi_week, alert)."""
    a = with_week_start(alerts)
    if "method" not in a:
        a["method"] = "m"
    districts = sorted(a["district_code"].unique())
    diseases = set(a["disease"].unique())
    first, last = a["week_start"].min(), a["week_start"].max() + pd.Timedelta(days=6)
    n_weeks = a["week_start"].nunique()
    district_years = len(districts) * n_weeks / WEEKS_PER_YEAR

    ob = outbreaks[outbreaks["disease"].isin(diseases)
                   & (outbreaks["district_code"].isin(districts) | (outbreaks["district_code"] == "ALL"))]
    win = _windows(ob, districts, buffer_days)
    win = win[(win["w_start"] <= last) & (win["w_end"] >= first)]
    in_scope = ob[ob["outbreak_id"].isin(win["outbreak_id"])]

    # Alert weeks inside a window of the same district and disease.
    hits = a[a["alert"]][["district_code", "disease", "week_start"]].merge(win, on=["district_code", "disease"])
    hits = hits[(hits["week_start"] <= hits["w_end"]) & (hits["week_start"] + pd.Timedelta(days=6) >= hits["w_start"])]
    first_hit = hits.groupby("outbreak_id")["week_start"].min()

    per = in_scope[["outbreak_id", "district_code", "disease", "start_date", "shape"]].copy()
    per["detected"] = per["outbreak_id"].isin(first_hit.index)
    det_date = per["outbreak_id"].map(first_hit) + pd.Timedelta(days=6)
    per["days_to_detection"] = np.where(per["detected"], (det_date - per["start_date"]).dt.days.clip(lower=0), np.nan)

    ep = episodes(a)
    if len(ep):
        m = ep.reset_index().merge(win, on=["district_code", "disease"], how="left")
        m["overlap"] = (m["ep_start"] <= m["w_end"]) & (m["ep_end"] + pd.Timedelta(days=6) >= m["w_start"])
        true_ep = m.groupby("index")["overlap"].any()
        n_ep, n_true = len(ep), int(true_ep.sum())
    else:
        n_ep = n_true = 0
    n_false = n_ep - n_true

    n = len(per)
    delays = per.loc[per["detected"], "days_to_detection"]
    out = {
        "n_outbreaks": n,
        "detected": int(per["detected"].sum()),
        "sensitivity": float(per["detected"].mean()) if n else float("nan"),
        "alert_episodes": n_ep,
        "false_alerts": n_false,
        "district_years": district_years,
        "false_alerts_per_district_year": n_false / district_years if district_years else float("nan"),
        "ppv": n_true / n_ep if n_ep else float("nan"),
        "median_days_to_detection": float(delays.median()) if len(delays) else float("nan"),
        "detected_within_7d": float((delays <= 7).sum() / n) if n else float("nan"),
        "detected_within_14d": float((delays <= 14).sum() / n) if n else float("nan"),
    }
    by_shape = {}
    for shape, g in per.groupby("shape"):
        d = g.loc[g["detected"], "days_to_detection"]
        by_shape[str(shape)] = {"n": len(g), "detected": int(g["detected"].sum()),
                                "sensitivity": float(g["detected"].mean()),
                                "median_days_to_detection": float(d.median()) if len(d) else None}
    out["by_shape"] = by_shape
    out["per_outbreak"] = per
    return out
