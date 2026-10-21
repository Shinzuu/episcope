"""Allocation LP (brief §6.3) in PuLP 3.3.2 with the bundled CBC. Implements AN-7, and the metrics AN-8 plots.

Decision variables x[d,r] >= 0 (units of resource r sent to district d) and u[d,r] >= 0 (unmet need).

    minimise    sum_{d,r,k} w_d * t_k * u[d,r,k] / N_r   (+ WASTE_PENALTY * sum c_r x[d,r] / B)
    subject to  sum_k u[d,r,k] >= need[d,r] - x[d,r],  0 <= u[d,r,k] <= need[d,r] / K      unmet demand
                x[d,r] >= alpha * p_d * sum_e x[e,r]                       equity floor (p_d = population share)
                sum_{d,r} c_r x[d,r] <= B                                  budget
                sum_{d in k} sum_r v_r x[d,r] <= T_k   for each division k    transport capacity
                (optional) sum_d x[d,r] <= S_r                             national stock

need[d,r] = max(0, q90 planning demand - current stock).

Unmet need is priced in tranches (our fix, docs/experiments/allocation_diagnosis.md): u[d,r] is split into
UNMET_TRANCHES equal slices of need[d,r], and the k-th slice (the deepest shortfall last) costs
UNMET_TRANCHE_WEIGHTS[k]. With one flat price every unit of resource r is worth the same in every district, so
the LP is indifferent between districts and the solver returns a corner: some districts get all their need and
the rest nothing. Rising slice prices make the first units in a district worth more than the last ones, so
money follows need across all districts and resources.

Our extension (research.md §10.3 issue 2): unmet need is divided by N_r = sum_d need[d,r], the national net need
for resource r. A bed and an NS1 kit are otherwise worth the same in the objective; after normalisation each
term is "share of the national need for r left unmet", unit-free and comparable across resources.

alpha = 0 is pure efficiency; alpha = 1 splits every resource by population share of what is sent, except that no
district gets more than its net need (x <= need; the floor is min(alpha p_d sum_e x[e,r], need[d,r]), a MILP). The model is
feasible for every alpha in [0, 1] (x = 0 satisfies every constraint; research.md §10.3).
"""

import warnings

import numpy as np
import pandas as pd
import pulp

from analytics.optimise import assumptions as A


def _prep(need, pop):
    D = sorted(set(pop) | set(need["district_code"]))
    R = sorted(need["resource"].unique())
    nd = {(d, r): 0.0 for d in D for r in R}
    for d, r, v in need[["district_code", "resource", "need"]].itertuples(index=False):
        nd[(d, r)] = max(0.0, float(v))
    total_pop = sum(max(pop.get(d, 0.0), 0.0) for d in D)
    p = {d: (max(pop.get(d, 0.0), 0.0) / total_pop if total_pop > 0 else 1.0 / len(D)) for d in D}
    N = {r: sum(nd[(d, r)] for d in D) for r in R}
    return D, R, nd, p, N


def transport_capacity(need, pop, division, volume=None, factor=A.TRANSPORT_FACTOR):
    """T_k = factor x population share of division k x volume of all national net need."""
    volume = volume or A.UNIT_VOLUME_M3
    D, R, nd, p, _ = _prep(need, pop)
    vtot = sum(volume.get(r, 0.0) * nd[(d, r)] for d in D for r in R)
    if vtot <= 0:
        return None
    cap = {}
    for d in D:
        k = division.get(d)
        cap[k] = cap.get(k, 0.0) + factor * p[d] * vtot
    return cap


def solve(need, pop, division, budget, alpha, cost=None, volume=None, transport_cap=None, weights=None,
          national_stock=None, equity=True, msg=False):
    """need: DataFrame(district_code, resource, need). pop/division: {district_code: population / division}.
    Returns (allocation DataFrame(district_code, resource, allocation, unmet), info dict)."""
    cost = cost or A.UNIT_COST_BDT
    volume = volume or A.UNIT_VOLUME_M3
    D, R, nd, p, N = _prep(need, pop)
    Ract = [r for r in R if N[r] > 0]
    w = {d: (weights or {}).get(d, A.DISTRICT_WEIGHT_DEFAULT) for d in D}
    di = {d: i for i, d in enumerate(D)}
    ri = {r: j for j, r in enumerate(Ract)}
    idx = [(di[d], ri[r]) for d in D for r in Ract]

    K, tw = A.UNMET_TRANCHES, A.UNMET_TRANCHE_WEIGHTS
    tidx = [(i, j, k) for i, j in idx for k in range(K)]
    m = pulp.LpProblem("episcope_allocation", pulp.LpMinimize)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        # Cap (review, 2026-10-21): a district never receives more of a resource than its net need.
        x = {(i, j): pulp.LpVariable(f"x_{i}_{j}", lowBound=0, upBound=nd[(D[i], Ract[j])]) for i, j in idx}
        u = pulp.LpVariable.dicts("u", tidx, lowBound=0)
    bscale = max(float(budget), 1.0)
    m += (pulp.lpSum(w[d] * tw[k] * u[(di[d], ri[r], k)] * (1.0 / N[r]) for d in D for r in Ract for k in range(K))
          + pulp.lpSum(A.WASTE_PENALTY * cost[r] / bscale * x[(di[d], ri[r])] for d in D for r in Ract))
    for d in D:
        for r in Ract:
            i, j = di[d], ri[r]
            m += pulp.lpSum(u[(i, j, k)] for k in range(K)) >= nd[(d, r)] - x[(i, j)], f"unmet_{i}_{j}"
            for k in range(K):
                m += u[(i, j, k)] <= nd[(d, r)] / K, f"tranche_{i}_{j}_{k}"
    if equity and alpha > 0:
        # Equity floor: x[d,r] >= min(alpha * p_d * sum_e x[e,r], need[d,r]). The min makes it a MILP: z = 1 means
        # the district's need is met in full (x = need), z = 0 means the population floor binds. With the cap,
        # alpha = 1 is "population share of what is sent, but never more than a district needs".
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            z = {k: pulp.LpVariable(f"z_{k[0]}_{k[1]}", cat="Binary") for k in idx}
        for r in Ract:
            tot = pulp.lpSum(x[(di[e], ri[r])] for e in D)
            big = alpha * N[r]  # >= alpha * p_d * tot, because tot <= N_r under the cap
            for d in D:
                i, j = di[d], ri[r]
                m += x[(i, j)] >= alpha * p[d] * tot - big * z[(i, j)], f"equity_{i}_{j}"
                m += x[(i, j)] >= nd[(d, r)] * z[(i, j)], f"equity_full_{i}_{j}"
    m += pulp.lpSum(cost[r] * x[(di[d], ri[r])] for d in D for r in Ract) <= float(budget), "budget"
    tnames = {}
    if transport_cap:
        for j, (k, cap) in enumerate(sorted(transport_cap.items(), key=lambda kv: str(kv[0]))):
            members = [d for d in D if division.get(d) == k]
            if members:
                tnames[k] = f"transport_{j}"
                m += pulp.lpSum(volume[r] * x[(di[d], ri[r])] for d in members for r in Ract) <= float(cap), tnames[k]
    if national_stock:
        for r in Ract:
            if r in national_stock:
                m += pulp.lpSum(x[(di[d], ri[r])] for d in D) <= float(national_stock[r]), f"natstock_{ri[r]}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # PuLP 3.3 warns about its 4.0 API; we pin 3.3.2
        m.solve(pulp.PULP_CBC_CMD(msg=msg))
    status = pulp.LpStatus[m.status]
    rows = []
    for d in D:
        for r in R:
            if r in ri:
                xv = max(0.0, x[(di[d], ri[r])].value() or 0.0)
                uv = max(0.0, nd[(d, r)] - xv)
            else:
                xv, uv = 0.0, 0.0
            rows.append({"district_code": d, "resource": r, "allocation": xv, "unmet": uv})
    alloc = pd.DataFrame(rows)
    info = {
        "status": status,
        "objective": float(sum(w[d] * alloc_unmet / N[r]
                               for d, r, alloc_unmet in alloc[["district_code", "resource", "unmet"]].itertuples(index=False)
                               if N.get(r, 0) > 0)),
        "solver_objective": float(pulp.value(m.objective) or 0.0),  # what the solver minimises (tranche-weighted)
        "budget_dual": _pi(m, "budget"),
        "transport_duals": {str(k): _pi(m, n) for k, n in tnames.items()},
    }
    return alloc, info


def _pi(m, name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        c = m.constraints.get(name)
    return None if c is None or c.pi is None else float(c.pi)


def population_proportional(need, pop, budget, cost=None):
    """AN-7 baseline: split the budget across resources by the cost of their national need, then each
    resource across districts by population share. Ignores need location and transport."""
    cost = cost or A.UNIT_COST_BDT
    D, R, nd, p, N = _prep(need, pop)
    total_cost = sum(cost[r] * N[r] for r in R)
    rows = []
    for r in R:
        br = budget * cost[r] * N[r] / total_cost if total_cost > 0 else 0.0
        for d in D:
            xv = p[d] * br / cost[r]
            rows.append({"district_code": d, "resource": r, "allocation": xv, "unmet": max(0.0, nd[(d, r)] - xv)})
    return pd.DataFrame(rows)


def gini(values):
    v = np.sort(np.asarray(values, float))
    if len(v) == 0 or v.sum() <= 0:
        return 0.0
    n = len(v)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum()))


def evaluate(alloc, need, pop, cost=None, weights=None):
    """Efficiency and equity metrics for one allocation (LP or baseline)."""
    cost = cost or A.UNIT_COST_BDT
    D, R, nd, p, N = _prep(need, pop)
    a = alloc.copy()
    a["need"] = [nd.get((d, r), 0.0) for d, r in zip(a["district_code"], a["resource"])]
    a["unmet"] = np.maximum(0.0, a["need"] - a["allocation"])
    a["cost"] = a["resource"].map(cost)
    w = a["district_code"].map(lambda d: (weights or {}).get(d, A.DISTRICT_WEIGHT_DEFAULT))
    act = a[a["resource"].map(lambda r: N.get(r, 0) > 0)]
    objective = float((w[act.index] * act["unmet"] / act["resource"].map(N)).sum())
    share_met = [1 - act.loc[act["resource"] == r, "unmet"].sum() / N[r] for r in R if N[r] > 0]
    need_cost = float((act["need"] * act["cost"]).sum())
    with_need = act[act["need"] > 0]
    cov = (1 - with_need["unmet"] / with_need["need"]).groupby(with_need["district_code"]).mean()
    spend = a["allocation"] * a["cost"]
    per_cap = spend.groupby(a["district_code"]).sum() / pd.Series({d: max(pop.get(d, 0.0), 1.0) for d in D})
    return {
        "objective": objective,
        "pct_need_met": 100 * float(np.mean(share_met)) if share_met else 100.0,
        "pct_need_cost_met": 100 * (1 - float((act["unmet"] * act["cost"]).sum()) / need_cost) if need_cost > 0 else 100.0,
        "min_district_coverage": float(cov.min()) if len(cov) else 1.0,
        "gini_per_capita": gini(per_cap.reindex(D).fillna(0.0).values),
        "spend": float(spend.sum()),
        "excess_cost": float((np.maximum(0.0, a["allocation"] - a["need"]) * a["cost"]).sum()),
    }
