"""Model parameters for the synthetic generator: diseases, seasonality, climate links, severity, defects.

Implements ING-1 (realistic S4 at scale) and the defect rates that DQ-1/DQ-2 test against.
Sources for every choice are in docs/research.md §1.2 (seasonality) and §8 (generator realism).
All rates are per capita, so the row count follows the population in data/reference, never a constant.
"""

import numpy as np

# Random-stream identifiers. Each (seed, stream, key...) tuple gives an independent numpy stream
# (research.md §8.2), so output does not depend on worker count or task order.
STREAM_BASE = 1
STREAM_OUTBREAK = 2
STREAM_FACILITY = 3
STREAM_STOCK = 4
STREAM_PLAN = 5
STREAM_STRUCT = 6  # district heterogeneity: fixed, seed-independent "geography"
STRUCT_SEED = 20210101

DISEASES = ["dengue", "diarrhoea", "ili", "malaria", "background"]
D_DENGUE, D_DIARRHOEA, D_ILI, D_MALARIA, D_BACKGROUND = range(5)
OUTBREAK_DISEASES = ["dengue", "diarrhoea", "ili", "malaria"]

AGE_BANDS = ["0-4", "5-14", "15-24", "25-44", "45-64", "65+"]
AGE_LO = np.array([0, 5, 15, 25, 45, 65])
AGE_HI = np.array([4, 14, 24, 44, 64, 65])  # 65+ handled with an exponential tail

# Encounters per 1,000 population per year at SF=1. With the 2022 population (~165.7M) this is about
# 6.2M encounters a year from these rates; SF=1 gives 34.1M rows for 2021-2025, planted defects included
# (the brief asks for 25M or more).
RATE_PER_1000 = np.array([4.2, 9.7, 7.8, 0.24, 15.1])

# NB2 dispersion alpha per disease (Var = mu + alpha mu^2), research.md §8.1.
NB_ALPHA = np.array([0.30, 0.15, 0.15, 0.50, 0.05])
NB_ALPHA_OUTBREAK = 0.20

# Seasonality: sum of von Mises bumps on day of year, plus a floor; normalised to mean 1 over a year.
# (peak_doy, kappa, weight). Dengue peaks early October (OpenDengue 2022/2024, research.md §1.2);
# diarrhoea is bimodal pre/post monsoon; ILI peaks in the rainy season; malaria May-Oct.
SEASON = {
    D_DENGUE: ([(280, 2.6, 1.0)], 0.03),
    D_DIARRHOEA: ([(110, 2.0, 0.6), (285, 2.0, 0.4)], 0.35),
    D_ILI: ([(196, 1.5, 1.0)], 0.30),
    D_MALARIA: ([(200, 1.8, 1.0)], 0.08),
    D_BACKGROUND: ([(15, 0.4, 0.15)], 1.0),
}

# Age relative risk by band and male:female ratio, per disease.
AGE_RR = np.array([
    [0.5, 1.0, 1.6, 1.4, 0.9, 0.6],   # dengue: young adults
    [3.0, 1.0, 0.7, 0.7, 0.9, 1.6],   # diarrhoea: under-5 and elderly
    [2.0, 1.6, 0.8, 0.7, 0.9, 1.5],   # ILI
    [0.8, 1.1, 1.3, 1.2, 0.9, 0.6],   # malaria: working-age (forest work)
    [1.0, 0.6, 0.7, 1.0, 1.4, 1.8],   # background
])
MALE_RR = np.array([1.1, 1.0, 1.0, 1.4, 0.95])

# ICD-10 codes per disease (schema.json note), with sampling weights.
ICD = {
    D_DENGUE: (["A90", "A91"], [0.85, 0.15]),
    D_DIARRHOEA: (["A09", "A00"], [0.94, 0.06]),
    D_ILI: (["J11", "J10"], [0.80, 0.20]),
    D_MALARIA: (["B50", "B51", "B52", "B53", "B54"], [0.60, 0.12, 0.01, 0.02, 0.25]),
    # Background codes avoid A00-A09, A90-A91, B50-B54 and every J code, so no rule maps them to a target.
    D_BACKGROUND: (
        ["I10", "E11", "K29", "K35", "N39", "L03", "S52", "T14", "O80", "Z00", "M54", "R10", "R51",
         "H10", "D50", "I21", "I63", "E86", "K80", "R50"],
        [10, 9, 7, 3, 6, 5, 3, 4, 6, 8, 6, 7, 5, 3, 4, 2, 2, 3, 3, 4],
    ),
}
# Outbreak rows use a shifted code mix: cholera-heavy diarrhoea, J11-heavy respiratory.
ICD_OUTBREAK = {
    D_DIARRHOEA: (["A09", "A00"], [0.30, 0.70]),
    D_ILI: (["J11", "J10"], [0.85, 0.15]),
}
BAD_ICD = ["", "XXX", "A9", "999", "J1O", "Z99.99.9", "a90", "B5A", "NULL", "A90.X"]

# Severity cascade: lab_confirmed -> admitted -> outcome.
LAB_P = np.array([0.55, 0.08, 0.05, 0.92, 0.20])
ADMIT_P = np.array([0.35, 0.12, 0.05, 0.30, 0.08])
DIED_ADMITTED_P = np.array([0.012, 0.004, 0.006, 0.008, 0.010])
REFER_ADMITTED_P = 0.10
UNKNOWN_ADMITTED_P = 0.04
DIED_OUTPATIENT_P = 0.0003
REFER_OUTPATIENT_P = 0.04
UNKNOWN_OUTPATIENT_P = 0.06
OUTCOMES = ["recovered", "referred", "died", "unknown"]

# Reporting delay: discretised negative binomial, mean 3 days, size 1.5, truncated to 0-21 and
# renormalised (research.md §8.3: P(0) ~ 0.19, right-skewed tail).
DELAY_MEAN, DELAY_SIZE, DELAY_MAX = 3.0, 1.5, 21
WEEKEND_SHIFT_P = 0.65   # share of Fri/Sat reports pushed to the next Sunday
HOLIDAY_SHIFT_P = 0.80   # share of holiday reports pushed to the next working day

# Built-in defects (brief §5.2). Rates are shares of output rows.
DUP_RATE = 0.02          # extra rows that repeat an existing encounter_id (resubmissions)
ORPHAN_RATE = 0.005      # facility_id not in S5
INVALID_AGE_RATE = 0.005
INVALID_SEX_RATE = 0.005
BAD_ICD_RATE = 0.003
ONSET_AFTER_REPORT_RATE = 0.003
REPEAT_PATIENT_RATE = 0.03  # rows that reuse an earlier patient_id (DQ-4 patient de-duplication)

INVALID_AGES = np.array([-3, -1, 150, 212, 999])
INVALID_SEXES = np.array(["", "U", "X", "9"])

# Malaria geography: Chittagong Hill Tracts and Cox's Bazar carry ~90% (research.md §1.2).
# Matched on HDX P-code or on a normalised name, so older spellings still match.
MALARIA_CHT = ({"BD2003", "BD2084", "BD2046"}, {"bandarban", "rangamati", "khagrachhari", "khagrachari"})
MALARIA_COX = ({"BD2022"}, {"coxsbazar"})
MALARIA_ENDEMIC = (
    {"BD6058", "BD6036", "BD6091", "BD6090", "BD4572", "BD4589", "BD4561", "BD5549", "BD2015"},
    {"moulvibazar", "maulvibazar", "habiganj", "sylhet", "sunamganj", "netrakona", "netrokona", "sherpur",
     "mymensingh", "kurigram", "chattogram", "chittagong"},
)
MALARIA_W = {"cht": 150.0, "cox": 60.0, "endemic": 2.0, "other": 0.01}
DHAKA = ({"BD3026"}, {"dhaka"})

GROUPS = 4  # fixed number of district groups per year: output layout must not depend on worker count
