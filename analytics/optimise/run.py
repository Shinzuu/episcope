"""Allocation job. Implements AN-7 (gold.allocation, baseline comparison) and AN-8 (curve data, recommended alpha).

Inputs: gold.demand (AN-6), gold.stock_latest (stock 0 if the table is missing), data/reference/districts.csv
(population_2022, division_code). Net need = max(0, q90 planning demand - stock).

Outputs:
    gold.allocation                         every LP scenario of the grid (contract columns)
    <results>/allocation_curve.csv          one row per scenario, incl. population-proportional baselines
    <results>/allocation_baseline.csv       baseline allocations (kept out of gold.allocation: they have no alpha)
    <results>/allocation_summary.json       B_ref, recommended alpha with its reason, assumptions

Run: python -m analytics.optimise.run
"""

import json

import pandas as pd

from analytics.forecast import io
from analytics.forecast.demand import planning_demand
from analytics.optimise import assumptions as A
from analytics.optimise.curve import recommend_alpha, run_grid

ALLOC_SCHEMA = ("scenario_id string, budget double, alpha double, district_code string, resource string, "
                "allocation double, unmet double")


def load_reference(district_codes):
    """population and division per district from districts.csv; equal populations and the P-code division
    prefix (BD + 2 digits) if the file is missing."""
    path = io.districts_csv()
    if path.exists():
        ref = pd.read_csv(path, dtype={"district_code": str, "division_code": str})
        return (dict(zip(ref["district_code"], ref["population_2022"].astype(float))),
                dict(zip(ref["district_code"], ref["division_code"])))
    print(f"warning: {path} missing; using equal populations")
    return {d: 1.0 for d in district_codes}, {d: str(d)[:4] for d in district_codes}


def net_need(demand, stock=None):
    plan = planning_demand(demand)
    if stock is not None and len(stock):
        plan = plan.merge(stock[["district_code", "resource", "stock"]], on=["district_code", "resource"], how="left")
    else:
        plan["stock"] = 0.0
    plan["stock"] = plan["stock"].fillna(0.0)
    plan["need"] = (plan["demand"] - plan["stock"]).clip(lower=0.0)
    return plan


def optimise(demand, stock, pop, division):
    need = net_need(demand, stock)
    alloc, curve, base, bref = run_grid(need, pop, division)
    rec = recommend_alpha(curve)
    return need, alloc, curve, base, bref, rec


def write_results(need, curve, base, bref, rec):
    out = io.results_dir()
    curve.to_csv(out / "allocation_curve.csv", index=False)
    base.to_csv(out / "allocation_baseline.csv", index=False)
    lp_c = curve[curve["kind"] == "lp"]
    bl_c = curve[curve["kind"] == "population_proportional"]
    comp = []
    for level in sorted(lp_c["budget_level"].unique()):
        g = lp_c[(lp_c["budget_level"] == level) & (lp_c["alpha"].astype(float) == rec["alpha"])]
        b = bl_c[bl_c["budget_level"] == level]
        if len(g) and len(b):
            comp.append({"budget_level": level, "lp_pct_need_met": float(g["pct_need_met"].iloc[0]),
                         "baseline_pct_need_met": float(b["pct_need_met"].iloc[0]),
                         "lp_objective": float(g["objective"].iloc[0]), "baseline_objective": float(b["objective"].iloc[0])})
    summary = {
        "b_ref_bdt": bref,
        "budget_levels": list(A.BUDGET_LEVELS), "alphas": list(A.ALPHAS),
        "recommended": rec,
        "lp_vs_population_baseline_at_recommended_alpha": comp,
        "national_net_need": need.groupby("resource")["need"].sum().to_dict(),
        "assumptions": {"unit_cost_bdt": A.UNIT_COST_BDT, "unit_volume_m3": A.UNIT_VOLUME_M3,
                        "transport_factor": A.TRANSPORT_FACTOR,
                        "normalisation": "unmet[d,r] / national net need N_r (our extension, research.md §10)"},
    }
    (out / "allocation_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def main():
    from pipeline.spark import get_spark

    io.limit_threads()
    spark = get_spark("episcope-optimise")
    try:
        demand = io.read_gold(spark, "gold.demand").toPandas()
        stock = io.read_gold(spark, "gold.stock_latest").toPandas() if io.gold_exists("gold.stock_latest") else None
        pop, division = load_reference(sorted(demand["district_code"].unique()))
        need, alloc, curve, base, bref, rec = optimise(demand, stock, pop, division)
        io.write_gold(spark.createDataFrame(alloc, ALLOC_SCHEMA), "gold.allocation")
    finally:
        spark.stop()
    s = write_results(need, curve, base, bref, rec)
    print(f"gold.allocation: {len(alloc)} rows; recommended alpha {rec['alpha']}: {rec['reason']}")
    return s


if __name__ == "__main__":
    main()
