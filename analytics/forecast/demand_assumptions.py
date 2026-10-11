"""Every forecast-to-demand conversion assumption, in one place. Implements AN-6 (config).

Each rate says where it comes from. "ASSUMPTION" means we chose it and the report must say so; the
literature figures quoted next to some rates are context for the choice, not a verified citation
(research.md §4.6 marks them UNVERIFIED). Change a number here and the whole chain (gold.demand, the LP,
the dashboard) follows; nothing else hard-codes a rate.

Units:
    beds           one hospital bed occupied for one week (average beds in use during the target week)
    iv_saline      one 1-litre bag of IV crystalloid (normal saline / Ringer's lactate)
    ns1_kits       one dengue NS1 antigen rapid test
    ors            one ORS sachet (WHO low-osmolarity formula, made up to 1 litre)
    antimalarials  one complete adult-equivalent treatment course (ACT)
"""

# Share of reported cases that are admitted. We estimate it from gold.cases_weekly (sum admissions / sum cases
# per disease, all history) because the generator encodes it; these values are used only if that table has no
# admissions for the disease.
ADMISSION_SHARE_FALLBACK = {
    "dengue": 0.25,      # ASSUMPTION. DGHS counts mostly hospitalised cases, but our encounters include outpatients.
    "diarrhoea": 0.10,   # ASSUMPTION. Severe dehydration (WHO Plan C) is a minority of AWD presentations.
    "malaria": 0.10,     # ASSUMPTION. Severe malaria share of confirmed cases.
    "ili": 0.02,         # ASSUMPTION. Not used by any resource below; kept for completeness.
}

# Average length of stay in days. Beds occupied in week t = admissions_t * LOS / 7 (LOS <= 7 days, so the
# spill-over of earlier weeks' admissions into week t is ignored; stated as a simplification).
LOS_DAYS = {
    "dengue": 5.0,       # ASSUMPTION. Dhaka hospital series commonly report a median stay of 4-5 days (UNVERIFIED).
    "diarrhoea": 2.0,    # ASSUMPTION. Rehydration stays at icddr,b-type facilities are typically 1-2 days (UNVERIFIED).
    "malaria": 4.0,      # ASSUMPTION. Severe malaria inpatient stay.
}

# Conversion table: (disease, resource) -> (basis, units per basis). basis is "case" (per reported case) or
# "admission" (per admitted case, i.e. multiplied by the admission share). Beds are computed from LOS_DAYS.
CONVERSIONS = {
    ("dengue", "iv_saline"): ("admission", 4.0),     # ASSUMPTION. WHO 2009 dengue guideline crystalloid regimens
                                                    # (5-7 ml/kg/h tapering over 24-48 h) total about 3-5 L for an adult.
    ("diarrhoea", "iv_saline"): ("admission", 4.0),  # ASSUMPTION. WHO Plan C: 100 ml/kg in 3-6 h (about 6 L adult,
                                                    # 1-2 L child); 4 L as a mixed-age average.
    ("malaria", "iv_saline"): ("admission", 2.0),    # ASSUMPTION. Maintenance fluids for severe malaria.
    ("dengue", "ns1_kits"): ("case", 1.5),           # ASSUMPTION. One test per reported case plus 0.5 per case for
                                                    # suspected cases that test negative.
    ("diarrhoea", "ors"): ("case", 6.0),             # ASSUMPTION. WHO IMCI Plan A gives 2 sachets to take home;
                                                    # Plan B/after-discharge use raises the average to about 6.
    ("malaria", "antimalarials"): ("case", 1.1),     # ASSUMPTION. One ACT course per confirmed case (WHO malaria
                                                    # guidelines) plus 10% for re-treatment and wastage.
}

RESOURCES = ("beds", "iv_saline", "ns1_kits", "ors", "antimalarials")

# How a resource's 4 weekly demands become one planning quantity for the optimiser (AN-7):
#   "sum": consumables are used up, so the 4-week need is the sum of weekly needs;
#   "max": beds are reused, so the need is the busiest week.
# Summing per-week q90 values overstates the q90 of the 4-week total (it assumes the weeks move together);
# that is deliberately conservative for planning and is stated in the report.
PLANNING_AGGREGATION = {"beds": "max", "iv_saline": "sum", "ns1_kits": "sum", "ors": "sum", "antimalarials": "sum"}

# Brief §6.3 plans on an upper prediction quantile. q90 is used for planning; the mean is carried alongside.
PLANNING_QUANTILE = "q90"
