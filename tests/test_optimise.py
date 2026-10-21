"""Tests for AN-7 (allocation LP, baseline) and AN-8 (equity curve, alpha recommendation), on synthetic needs."""

import numpy as np
import pandas as pd
import pytest

from analytics.optimise import assumptions as A
from analytics.optimise import lp
from analytics.optimise.curve import b_ref, recommend_alpha, run_grid
from analytics.optimise.run import net_need

RES = ["beds", "ns1_kits", "ors"]


def _case(seed=3):
    rng = np.random.default_rng(seed)
    districts = [f"D{i:02d}" for i in range(8)]
    pop = {d: float(rng.integers(500_000, 5_000_000)) for d in districts}
    division = {d: ("K1" if i < 4 else "K2") for i, d in enumerate(districts)}
    rows = []
    for i, d in enumerate(districts):
        for r in RES:
            # need concentrated in the first two districts, some districts need nothing
            base = {"beds": 40, "ns1_kits": 900, "ors": 3000}[r]
            v = base * (5 if i < 2 else rng.uniform(0, 1)) * (0 if i == 7 else 1)
            rows.append({"district_code": d, "resource": r, "need": float(v)})
    return pd.DataFrame(rows), pop, division


NEED, POP, DIV = _case()
COST = A.UNIT_COST_BDT
BREF = b_ref(NEED)


def _share(pop):
    t = sum(pop.values())
    return {d: v / t for d, v in pop.items()}


@pytest.mark.parametrize("level", [0.25, 0.5, 1.0, 1.5])
@pytest.mark.parametrize("alpha", [0.0, 0.3, 0.7, 1.0])
def test_budget_and_equity_floor_hold(level, alpha):
    budget = level * BREF
    tcap = lp.transport_capacity(NEED, POP, DIV)
    alloc, info = lp.solve(NEED, POP, DIV, budget, alpha, transport_cap=tcap)
    assert info["status"] == "Optimal"
    spend = (alloc["allocation"] * alloc["resource"].map(COST)).sum()
    assert spend <= budget * (1 + 1e-7) + 1e-6
    p = _share(POP)
    for r, g in alloc.groupby("resource"):
        tot = g["allocation"].sum()
        for d, x in zip(g["district_code"], g["allocation"]):
            assert x >= alpha * p[d] * tot - 1e-6 * max(1.0, tot)
    for k, cap in tcap.items():
        vol = sum(A.UNIT_VOLUME_M3[r] * x for d, r, x in alloc[["district_code", "resource", "allocation"]]
                  .itertuples(index=False) if DIV[d] == k)
        assert vol <= cap * (1 + 1e-7) + 1e-9
    assert (alloc["allocation"] >= -1e-9).all() and (alloc["unmet"] >= -1e-9).all()


def _greedy_efficiency(need, budget):
    """Independent pure-efficiency optimum with no transport limit: a fractional knapsack over need slices.
    Slice k of need[d,r] (size need/K) removes t_k/N_r of the tranche objective per unit at cost c_r, so slices
    are bought by t_k/(N_r c_r), deepest shortfall first. Returns the share-of-need-unmet metric (info objective)
    of that allocation."""
    N = need.groupby("resource")["need"].sum()
    K, tw = A.UNMET_TRANCHES, A.UNMET_TRANCHE_WEIGHTS
    slices = sorted(((tw[k] / N[r] / COST[r], r, v / K) for r, v in zip(need["resource"], need["need"])
                     for k in range(K) if v > 0), key=lambda s: -s[0])
    left, bought = budget, {r: 0.0 for r in N.index}
    for _, r, size in slices:
        take = min(size, left / COST[r])
        bought[r] += take
        left -= take * COST[r]
    return float(sum(1 - bought[r] / N[r] for r in N.index if N[r] > 0))


@pytest.mark.parametrize("level", [0.1, 0.25, 0.5, 0.9])
def test_alpha_zero_is_pure_efficiency(level):
    budget = level * BREF
    alloc0, info0 = lp.solve(NEED, POP, DIV, budget, 0.0)
    _, info_free = lp.solve(NEED, POP, DIV, budget, 0.5, equity=False)
    assert info0["objective"] == pytest.approx(_greedy_efficiency(NEED, budget), rel=1e-6, abs=1e-7)
    assert info0["objective"] == pytest.approx(info_free["objective"], rel=1e-6, abs=1e-7)
    # pure efficiency never sends supplies where there is no need
    merged = alloc0.merge(NEED, on=["district_code", "resource"])
    assert (merged["allocation"] <= merged["need"] * (1 + 1e-6) + 1e-3).all()   # CBC tolerance


def test_alpha_one_is_population_split():
    alloc, _ = lp.solve(NEED, POP, DIV, 0.5 * BREF, 1.0)
    p = _share(POP)
    for r, g in alloc.groupby("resource"):
        tot = g["allocation"].sum()
        assert np.allclose(g["allocation"].values, [p[d] * tot for d in g["district_code"]], atol=1e-6 * max(tot, 1))


def test_lp_beats_population_baseline_and_baseline_respects_budget():
    budget = 0.5 * BREF
    alloc, info = lp.solve(NEED, POP, DIV, budget, 0.0)
    base = lp.population_proportional(NEED, POP, budget)
    assert (base["allocation"] * base["resource"].map(COST)).sum() == pytest.approx(budget)
    eb, el = lp.evaluate(base, NEED, POP), lp.evaluate(alloc, NEED, POP)
    assert el["objective"] < eb["objective"]
    assert el["pct_need_met"] > eb["pct_need_met"]


def test_unmet_is_need_minus_allocation():
    alloc, _ = lp.solve(NEED, POP, DIV, 0.3 * BREF, 0.2)
    m = alloc.merge(NEED, on=["district_code", "resource"])
    assert np.allclose(m["unmet"], np.maximum(0, m["need"] - m["allocation"]))


def test_grid_curve_and_recommendation():
    levels, alphas = (0.25, 0.5, 0.75, 1.0, 1.5), (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    alloc, curve, base, bref = run_grid(NEED, POP, DIV, levels, alphas)
    assert bref == pytest.approx(BREF)
    assert alloc["scenario_id"].nunique() == len(levels) * len(alphas)
    assert len(alloc) == len(levels) * len(alphas) * NEED.shape[0]
    assert (curve.loc[curve.kind == "lp", "status"] == "Optimal").all()
    assert len(curve[curve.kind == "population_proportional"]) == len(levels)
    # more equity never improves efficiency at a fixed budget
    for _, g in curve[curve.kind == "lp"].groupby("budget_level"):
        obj = g.sort_values("alpha")["objective"].values
        assert (np.diff(obj) >= -1e-7).all()
    rec = recommend_alpha(curve)
    assert rec["alpha"] in alphas and "percentage points" in rec["reason"]
    assert rec["alpha"] in rec["admissible_alphas"]
    lpc = curve[curve.kind == "lp"]
    for level in (0.25, 0.5, 0.75):                   # the tolerance holds at every scarce budget
        g = lpc[lpc.budget_level == level].set_index("alpha")["pct_need_met"]
        assert g[rec["alpha"]] >= g[0.0] - rec["tolerance_pp"] - 1e-9


def test_net_need_subtracts_stock():
    dem = pd.DataFrame([{"district_code": "X", "resource": "ors", "horizon_weeks": h, "demand_mean": 5.0,
                         "demand_q90": 10.0} for h in (1, 2, 3, 4)]
                       + [{"district_code": "X", "resource": "beds", "horizon_weeks": h, "demand_mean": 5.0,
                           "demand_q90": float(h)} for h in (1, 2, 3, 4)])
    stock = pd.DataFrame([{"district_code": "X", "resource": "ors", "stock": 15.0}])
    nn = net_need(dem, stock).set_index("resource")
    assert nn.loc["ors", "need"] == pytest.approx(25.0)     # 4 x 10 - 15
    assert nn.loc["beds", "need"] == pytest.approx(4.0)     # busiest week, no stock


def test_money_follows_need_not_a_corner():
    """Equal need in many districts and half the money: every district gets a share (allocation_diagnosis.md).
    With one flat unmet price the LP was indifferent and gave some districts everything and the rest nothing."""
    districts = [f"E{i:02d}" for i in range(10)]
    need = pd.DataFrame([{"district_code": d, "resource": "beds", "need": 100.0} for d in districts])
    pop = {d: 1_000_000.0 for d in districts}
    alloc, info = lp.solve(need, pop, {d: "K" for d in districts}, 0.5 * b_ref(need), 0.0)
    cov = alloc.set_index("district_code")["allocation"] / 100.0
    assert info["status"] == "Optimal"
    assert (cov > 0.2).all() and (cov < 0.8).all()        # no district left at 0 or filled to 1
    assert cov.sum() == pytest.approx(5.0, rel=1e-6)      # the whole budget is still used


def test_bigger_need_gets_more():
    need = pd.DataFrame([{"district_code": "A", "resource": "ors", "need": 3000.0},
                         {"district_code": "B", "resource": "ors", "need": 1000.0}])
    pop = {"A": 1e6, "B": 1e6}
    alloc, _ = lp.solve(need, pop, {"A": "K", "B": "K"}, 0.5 * b_ref(need), 0.0)
    a = alloc.set_index("district_code")["allocation"]
    assert a["A"] > a["B"] > 0


def test_bed_census_counts_current_inpatients_as_available():
    from analytics.optimise.run import bed_census

    dem = pd.DataFrame([{"district_code": "X", "resource": "beds", "horizon_weeks": h, "demand_mean": 10.0,
                         "demand_q90": 20.0} for h in (1, 2, 3, 4)])
    cases = pd.DataFrame([{"district_code": "X", "disease": "dengue", "week_start": pd.Timestamp("2025-12-22"),
                           "admissions": 14}, {"district_code": "X", "disease": "dengue",
                                               "week_start": pd.Timestamp("2025-12-15"), "admissions": 99}])
    stock = pd.DataFrame([{"district_code": "X", "resource": "beds", "stock": 5.0}])
    nn = net_need(dem, stock, bed_census(cases)).set_index("resource")
    assert nn.loc["beds", "need"] == pytest.approx(20.0 - 5.0 - 14 * 5.0 / 7.0)  # latest week only, LOS 5 days
