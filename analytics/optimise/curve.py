"""Efficiency-equity curve and the alpha recommendation. Implements AN-8 (and the AN-7 baseline comparison).

Grid: BUDGET_LEVELS x B_ref (>= 5 levels) by ALPHAS (0 to 1 in steps of 0.1), plus the population-proportional
baseline at each budget. B_ref = cost of meeting all net need. Efficiency axis: % of need met (mean over
resources of the share of national need met) and the LP objective. Equity axis: the lowest district coverage
and the Gini of per-capita spend.

Recommendation rule (stated in the report):
1. admissible alphas: % need met stays within RECOMMEND_TOLERANCE_PP points of alpha = 0 at every scarce
   budget (< B_ref), i.e. equity may cost at most 2 points of efficiency whatever the budget turns out to be;
2. among those, pick the alpha with the best worst-off district (mean over scarce budgets of the lowest
   district coverage); ties go to the larger alpha, which guarantees a bigger population-based share.
"""

import pandas as pd

from analytics.optimise import assumptions as A
from analytics.optimise import lp


def b_ref(need, cost=None):
    cost = cost or A.UNIT_COST_BDT
    return float(sum(cost[r] * max(0.0, v) for r, v in zip(need["resource"], need["need"])))


def scenario_id(level, alpha):
    return "baseline_b{:.2f}".format(level) if alpha is None else "b{:.2f}_a{:.1f}".format(level, alpha)


def run_grid(need, pop, division, budget_levels=A.BUDGET_LEVELS, alphas=A.ALPHAS, cost=None, volume=None,
             transport_factor=A.TRANSPORT_FACTOR, weights=None):
    """Returns (allocation rows for gold.allocation, curve DataFrame with one row per scenario incl. baselines,
    baseline allocation rows)."""
    cost = cost or A.UNIT_COST_BDT
    bref = b_ref(need, cost)
    tcap = lp.transport_capacity(need, pop, division, volume, transport_factor)
    allocs, curve, base_rows = [], [], []
    for level in budget_levels:
        budget = level * bref
        for alpha in alphas:
            alloc, info = lp.solve(need, pop, division, budget, alpha, cost, volume, tcap, weights)
            sid = scenario_id(level, alpha)
            allocs.append(alloc.assign(scenario_id=sid, budget=budget, alpha=float(alpha)))
            curve.append({"scenario_id": sid, "kind": "lp", "budget_level": level, "budget": budget, "alpha": alpha,
                          "status": info["status"], "solver_objective": info["solver_objective"],
                          "budget_dual": info["budget_dual"],
                          "binding_transport": sum(1 for v in info["transport_duals"].values() if v and abs(v) > 1e-12),
                          **lp.evaluate(alloc, need, pop, cost, weights)})
        base = lp.population_proportional(need, pop, budget, cost)
        sid = scenario_id(level, None)
        base_rows.append(base.assign(scenario_id=sid, budget=budget))
        curve.append({"scenario_id": sid, "kind": "population_proportional", "budget_level": level, "budget": budget,
                      "alpha": None, "status": "n/a", "budget_dual": None, "binding_transport": None,
                      **lp.evaluate(base, need, pop, cost, weights)})
    cols = ["scenario_id", "budget", "alpha", "district_code", "resource", "allocation", "unmet"]
    alloc_df = pd.concat(allocs, ignore_index=True)[cols] if allocs else pd.DataFrame(columns=cols)
    base_df = pd.concat(base_rows, ignore_index=True) if base_rows else pd.DataFrame()
    return alloc_df, pd.DataFrame(curve), base_df, bref


def recommend_alpha(curve, tol_pp=A.RECOMMEND_TOLERANCE_PP):
    lpc = curve[curve["kind"] == "lp"].copy()
    lpc["alpha"] = lpc["alpha"].astype(float)
    scarce = sorted(lpc.loc[lpc["budget_level"] < 1.0, "budget_level"].unique()) or sorted(lpc["budget_level"].unique())
    per_budget, admissible = {}, None
    for level in scarce:
        g = lpc[lpc["budget_level"] == level].sort_values("alpha")
        base = float(g.loc[g["alpha"] == g["alpha"].min(), "pct_need_met"].iloc[0])
        ok = g[g["pct_need_met"] >= base - tol_pp]
        per_budget[level] = float(ok["alpha"].max()) if len(ok) else float(g["alpha"].min())
        admissible = set(ok["alpha"]) if admissible is None else admissible & set(ok["alpha"])
    if not admissible:
        admissible = {float(lpc["alpha"].min())}
    eq = (lpc[lpc["budget_level"].isin(scarce) & lpc["alpha"].isin(admissible)]
          .groupby("alpha")["min_district_coverage"].mean())
    best = eq.max()
    alpha = float(max(a for a, v in eq.items() if v >= best - 1e-9))
    ref = A.DEFAULT_BUDGET_LEVEL if A.DEFAULT_BUDGET_LEVEL in per_budget else (scarce[len(scarce) // 2] if scarce else None)
    detail = {}
    if ref is not None:
        g = lpc[lpc["budget_level"] == ref].set_index("alpha")
        a0 = g.loc[g.index.min()]
        ar = g.loc[alpha]
        detail = {"reference_budget_level": ref,
                  "pct_need_met_alpha0": float(a0["pct_need_met"]), "pct_need_met_recommended": float(ar["pct_need_met"]),
                  "min_district_coverage_alpha0": float(a0["min_district_coverage"]),
                  "min_district_coverage_recommended": float(ar["min_district_coverage"]),
                  "gini_alpha0": float(a0["gini_per_capita"]), "gini_recommended": float(ar["gini_per_capita"])}
        bl = curve[(curve["kind"] == "population_proportional") & (curve["budget_level"] == ref)]
        if len(bl):
            detail["pct_need_met_population_baseline"] = float(bl["pct_need_met"].iloc[0])
    reason = (f"alpha = {alpha:.1f}: of the equity floors that keep the share of need met within {tol_pp:g} "
              f"percentage points of pure efficiency at every scarce budget (alpha <= "
              f"{max(admissible):.1f} qualifies), it gives the best-covered worst-off district on average "
              f"(mean lowest district coverage {eq[alpha]:.2f} vs {eq.get(min(admissible), float('nan')):.2f} "
              f"at alpha = {min(admissible):.1f}).")
    if detail:
        reason += (f" At {detail['reference_budget_level']:g}xB_ref it meets {detail['pct_need_met_recommended']:.1f}% "
                   f"of need (vs {detail['pct_need_met_alpha0']:.1f}% at alpha = 0"
                   + (f", {detail['pct_need_met_population_baseline']:.1f}% for the population-proportional baseline"
                      if "pct_need_met_population_baseline" in detail else "")
                   + f"); the worst-covered district gets {detail['min_district_coverage_recommended']:.2f} of its "
                   f"need (vs {detail['min_district_coverage_alpha0']:.2f} at alpha = 0).")
    return {"alpha": alpha, "tolerance_pp": tol_pp, "admissible_alphas": sorted(float(a) for a in admissible),
            "per_budget_max_alpha": {str(k): v for k, v in per_budget.items()},
            "reason": reason, **detail}
