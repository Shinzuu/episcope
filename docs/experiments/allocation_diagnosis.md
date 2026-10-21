# Allocation (AN-7, AN-8): diagnosis and fix

Date: 2026-10-21. Code before the fix: commit e85a784. Fix: commit 28952eb.

## Symptom

The SF=0.25 build (seed 42, real weather) gave a national net need of zero for beds, IV saline, NS1 kits and
ORS, and 8.8 courses of antimalarials. The LP sent everything to two districts (top-5 share of allocated value
1.00, Gini of per-capita spend 0.98) and the equity curve recommended alpha = 0 at every budget. The SF=1 run
was no better: net need zero except 17,773 ORS sachets, Gini 0.98.

## What was wrong

Three separate faults. Each was confirmed on its own.

**A. Generated stock did not depend on the case load (data).** `generator/stock.py` sized every facility's
stock by its beds (30 IV bags, 5 NS1 kits, 60 ORS sachets per bed) and drained it with a seasonal curve, not
with the facility's cases. The stock was the same at every scale factor, while demand grows with SF. On
2025-12-31 at SF=0.25, district stock was 3 to 225 times the 4-week q90 demand (median ratio: beds 49, IV
saline 47, NS1 72, ORS 11). National stock against national 4-week q90 demand:

| Resource | Stock | Demand (q90, 4 weeks) |
|---|---|---|
| IV saline | 946,467 | 22,553 |
| NS1 kits | 164,577 | 3,723 |
| ORS | 1,691,959 | 159,720 |
| Antimalarials | 10,935 | 288 |
| Free beds | 23,863 | 599 |

With stock this high, net need = max(0, demand - stock) is zero almost everywhere, so there was nothing to
allocate.

**B. The LP objective was flat across districts (model).** The objective was
sum over (d, r) of unmet[d, r] / N_r. One unit of resource r is worth the same in every district, so the LP is
indifferent to where it goes and the solver returns a corner. To test this without fault A, we set stock to
half of demand in every district (need everywhere) and solved at half of B_ref, alpha = 0:

| Resource | Districts fully covered | Districts given nothing | Partly covered |
|---|---|---|---|
| Beds | 21 | 42 | 1 |
| IV saline | 0 | 64 | 0 |
| NS1, ORS, antimalarials | all | 0 | 0 |

The money went to the resources with the cheapest national need, and within beds to an arbitrary set of 21
districts.

**C. Beds were counted twice (model).** `gold.stock_latest` holds free beds, and the bed demand forecast counts
every modelled inpatient. The patients in hospital now are in both: they hold a bed today and are part of next
week's census. Net bed need was overstated by the current census.

Not at fault: the normalisation by N_r (it makes beds and kits comparable, as intended), the equity-floor
constraint (alpha p_d sum_e x[e, r]), and the demand conversion (demand_assumptions.py; for ORS, 6 sachets x the 26,162
diarrhoea encounters reported in December 2025 is about 157,000, close to the 159,720 forecast for January).

## Fix

- **A, generator:** stock now follows each facility's own case load. Expected cases per facility and day come
  from the encounter intensity (base, climate and outbreak excess, at the run's SF) times the facility's share
  of its district's encounters. Use per case: IV saline per admitted case, 1.5 NS1 kits per dengue case, 6 ORS
  sachets per diarrhoea case, 1.1 ACT courses per malaria case. Inventory is a min/max policy on recent use:
  par level 21 days of the trailing 28-day mean, reorder below 35% of par, delivery after 5 to 14 days, filled
  70 to 100%. A rise in cases drains stock before the next delivery. Occupied beds = a fixed 50 to 70% share
  for other conditions plus modelled inpatients (admissions x length of stay). The S6 schema is unchanged.
- **B, LP:** unmet need is priced in four equal slices of each district's need with weights 1, 2, 3, 4 (the
  deepest shortfall costs most). The first units sent to a district are now worth more than the last units sent
  to a district that is already well covered, so money follows need across districts and resources. The
  reported objective (share of national need left unmet) is unchanged.
- **C, optimiser:** for beds, available = free beds + the current census of modelled patients (admissions in
  the latest week x LOS / 7).

Same isolation test after the fix (stock = half of demand, half of B_ref, alpha = 0): every district is partly
covered for every resource (beds 0.25 to 0.50 of need, IV saline 0.25, NS1 and ORS 0.75, antimalarials 1.00).

Tests: `tests/test_optimise.py` (money follows need, bigger need gets more, bed census, the pure-efficiency
optimum against an independent greedy solution), `tests/test_generator.py::test_stock_follows_case_load`.

## Before and after

SF=0.25, seed 42, real weather, half of B_ref, transport limit on. Before: the built lake of 20 October with
the old LP. After: a fresh build with the fix.

Before:

| alpha | Top-5 share of allocated value | Districts receiving | Gini (per-capita spend) | Unmet weighted demand | % need met |
|---|---|---|---|---|---|
| 0.0 | 1.000 | 2 | 0.979 | 0.500 | 50.0 |
| 0.2 | 0.844 | 64 | 0.825 | 0.598 | 40.2 |
| 0.5 | 0.610 | 64 | 0.565 | 0.745 | 25.5 |
| 1.0 | 0.270 | 64 | 0.000 | 0.989 | 1.1 |

After (fresh build from a clean clone at 28952eb, 2026-10-21 12:05 to 12:21):

| alpha | Top-5 share of allocated value | Districts receiving | Gini (per-capita spend) | Unmet weighted demand | % need met |
|---|---|---|---|---|---|
| 0.0 | 0.216 | 64 | 0.274 | 1.042 | 73.9 |
| 0.1 | 0.217 | 64 | 0.280 | 1.054 | 73.6 |
| 0.2 | 0.224 | 64 | 0.281 | 1.068 | 73.3 |
| 0.5 | 0.237 | 64 | 0.274 | 1.124 | 71.9 |
| 1.0 | 0.270 | 64 | 0.000 | 2.025 | 49.4 |

"Unmet weighted demand" is the sum over resources of the share of national need left unmet (0 = all met, one
point per resource with need); it is not comparable between before and after, because before only one resource
had any need.

National net need after the fix (4-week q90 demand minus stock; beds count the current census as available):

| Resource | Demand | Stock | Net need | Districts with need |
|---|---|---|---|---|
| IV saline | 22,553 | 56,371 | 10,962 | 63 |
| ORS | 159,720 | 32,636 | 127,084 | 64 |
| Antimalarials | 288 | 105 | 185 | 14 |
| NS1 kits | 3,723 | 52,608 | 1.5 | 1 |
| Beds | 599 | 23,186 | 0 | 0 |

The data end on 31 December, after the dengue season: NS1 kits were stocked for November's dengue load and beds
are free, so neither needs topping up in January. Diarrhoea (ORS) and IV saline are where the shortfall is.

At half of B_ref the LP meets 73.6% of need at the recommended alpha = 0.1, against 26.3% for the
population-proportional baseline (`allocation_summary.json` of that build). The recommendation is no longer
pinned to 0: floors up to 0.4 cost under 2 points of efficiency, and 0.1 gives the best worst-off district.
These are SF=0.25 numbers; the report uses the clean SF=1 run.
