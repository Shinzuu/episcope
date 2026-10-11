"""S2 population: HDX `cod-ps-bgd` ADM2 2022 table (BBS-derived) to district totals and age-sex bands.

Implements S2 and ING-1 (raw CSV cached in bronze with metadata). Supplies the denominators for incidence
per 100k (VIS-1, PRV-2 inputs) and for population-proportional equity in the allocation LP (AN-7, AN-8).
Source: UNFPA / US Census Bureau from BBS Census 2022, CC BY-IGO, https://data.humdata.org/dataset/cod-ps-bgd

Totals are used exactly as published (national sum about 165.65M). They are NOT scaled to the BBS
final adjusted Census 2022 total (about 169.83M, roughly 2.5% higher), because that figure is not yet
verified at a primary source (research.md section 2.2). Incidence per 100k is therefore about 2.5% higher
than it would be against the adjusted total; the report states this.

Join is on ADM2 P-code only: this file uses old spellings (Barisal, Chittagong, Comilla ...).
"""

import re

import pandas as pd

from .common import BRONZE, download

URL = ("https://data.humdata.org/dataset/fdf0606c-8a3b-421a-b3e8-903301e5b2ff/resource/"
       "43bfa9fd-f571-4973-9f31-91093e1e6142/download/bgd_admpop_adm2_2022.csv")
SOURCE = "hdx-cod-ps"
DEST = BRONZE / "population" / f"source={SOURCE}" / "bgd_admpop_adm2_2022.csv"

# Contract bands (docs/contracts.md) built from the source's 5-year bands, by lower age bound.
BANDS = [("0-4", 0, 4), ("5-14", 5, 14), ("15-24", 15, 24), ("25-44", 25, 44), ("45-64", 45, 64),
         ("65+", 65, 200)]
_COL = re.compile(r"^([FM])_(\d{2})(?:_(\d{2})|Plus)$")


def fetch():
    return download(URL, DEST, SOURCE)


def read_raw(path=DEST):
    # utf-8-sig strips the BOM on the first header.
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"ADM2_PCODE": str})


def district_totals(raw):
    out = raw[["ADM2_PCODE", "T_TL"]].rename(columns={"ADM2_PCODE": "district_code", "T_TL": "population_2022"})
    out["population_2022"] = out["population_2022"].astype("int64")
    if out.district_code.duplicated().any():
        raise ValueError("duplicate ADM2_PCODE in population table")
    return out


def age_sex(raw):
    """Long table district_code, age_band, sex, population. Checks that bands sum back to F_TL and M_TL."""
    cols = {c: _COL.match(c) for c in raw.columns}
    cols = {c: m for c, m in cols.items() if m}
    if not cols:
        raise ValueError("no F_/M_ age columns found in population table")
    rows = []
    for _, r in raw.iterrows():
        for sex in ("F", "M"):
            for band, lo, hi in BANDS:
                total = sum(int(r[c]) for c, m in cols.items()
                            if m.group(1) == sex and lo <= int(m.group(2)) <= hi)
                rows.append((r["ADM2_PCODE"], band, sex, total))
    out = pd.DataFrame(rows, columns=["district_code", "age_band", "sex", "population"])
    check = out.groupby(["district_code", "sex"]).population.sum().unstack()
    expect = raw.set_index("ADM2_PCODE")[["F_TL", "M_TL"]].rename(columns={"F_TL": "F", "M_TL": "M"})
    diff = (check - expect.loc[check.index]).abs().max().max()
    if diff > 0:
        raise ValueError(f"age bands do not sum to sex totals (max diff {diff})")
    return out.sort_values(["district_code", "sex", "age_band"], key=_band_order).reset_index(drop=True)


def _band_order(s):
    if s.name == "age_band":
        return s.map({b: i for i, (b, _, _) in enumerate(BANDS)})
    return s
