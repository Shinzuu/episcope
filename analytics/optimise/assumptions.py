"""Optimiser assumptions: costs, volumes, transport capacity and the scenario grid. Implements AN-7, AN-8 (config).

"ASSUMPTION" marks a number we chose; the report must state it. Prices are rough Bangladesh public-sector
figures in BDT for scale only (UNVERIFIED); only their ratios matter to the LP, because the budget levels are
multiples of the cost of meeting all need.
"""

UNIT_COST_BDT = {
    "beds": 3000.0,          # ASSUMPTION. One surge bed for one week: frame, mattress and a share of nursing cost.
    "iv_saline": 100.0,      # ASSUMPTION. 1 L bag; retail prices around 90-110 BDT were reported in 2023 (UNVERIFIED).
    "ns1_kits": 300.0,       # ASSUMPTION. The 2023 government cap on the NS1 test price was widely reported as 300 BDT (UNVERIFIED).
    "ors": 6.0,              # ASSUMPTION. One sachet of social-marketing ORS.
    "antimalarials": 500.0,  # ASSUMPTION. One ACT course, procurement plus distribution.
}

UNIT_VOLUME_M3 = {
    "beds": 0.5,             # ASSUMPTION. Folding bed and mattress.
    "iv_saline": 0.0015,     # ASSUMPTION. 1 L bag with packaging.
    "ns1_kits": 0.0002,      # ASSUMPTION. One test cassette with its share of the box.
    "ors": 0.0001,           # ASSUMPTION. One sachet.
    "antimalarials": 0.0005, # ASSUMPTION. One blister course with packaging.
}

# Transport capacity per division (m3 over the planning window) = TRANSPORT_FACTOR x the division's population
# share x the volume of all national net need. A division can therefore take at most 1.5 times its
# population share of the need volume: high-need divisions (e.g. Dhaka in a dengue season) can bind.
TRANSPORT_FACTOR = 1.5       # ASSUMPTION. No public data on district logistics capacity.

# District weights w_d in the objective. 1 = every district's unmet need counts the same. A vulnerability index
# could be plugged in here; we have no defensible one, so we leave it neutral.
DISTRICT_WEIGHT_DEFAULT = 1.0

# Scenario grid (AN-8 asks for >= 5 budgets x several alphas). Budgets are multiples of B_ref, the cost of
# meeting every district's net need (q90 demand minus stock) in full.
BUDGET_LEVELS = (0.25, 0.5, 0.75, 1.0, 1.5)
ALPHAS = tuple(round(0.1 * k, 1) for k in range(0, 11))

# Headline scenario written first in the dashboard: half the money needed.
DEFAULT_BUDGET_LEVEL = 0.5

# alpha recommendation: the largest alpha whose share of need met is within this many percentage points of
# the pure-efficiency answer (alpha = 0) at every scarce budget (< B_ref).
RECOMMEND_TOLERANCE_PP = 2.0

# Unmet need priced in K equal slices with rising weights (lp.py): the deepest shortfall in a district costs
# most, so the first units sent anywhere are worth more than the last units sent to a district already well
# covered. Without it the LP is flat across districts and returns all-or-nothing corners (allocation_diagnosis.md).
UNMET_TRANCHES = 4                          # ASSUMPTION. Quarters of a district's need.
UNMET_TRANCHE_WEIGHTS = (1.0, 2.0, 3.0, 4.0)  # ASSUMPTION. The last quarter of unmet need counts 4x the first.

# Tiny cost penalty so the solver does not hand out supplies nobody needs when the budget is slack.
WASTE_PENALTY = 1e-6
