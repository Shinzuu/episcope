"""Column-level transforms for silver: typing, ICD-10 to disease buckets, ISO epi-week. Implements ING-2.

Disease buckets (generator/schema.json notes and WHO ICD-10 chapters):
    dengue     A90, A91                 (dengue fever, dengue haemorrhagic fever)
    diarrhoea  A00-A09                  (cholera, other intestinal infections, infectious gastroenteritis)
    ili        J09, J10, J11            (influenza, the ILI proxy)
    malaria    B50-B54
    other      anything else
Matched on the three-character category, so subcategories (A09.0, B54.9) map with their parent.

Epi-week is the ISO week (docs/contracts.md): epi_week = weekofyear(onset_date),
epi_year = extract(YEAROFWEEK FROM onset_date), week_start = the Monday of that week.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

DISEASES = ("dengue", "diarrhoea", "ili", "malaria")
OTHER = "other"


def disease_of(icd: str | Column) -> Column:
    c = F.upper(F.substring(F.trim(F.col(icd) if isinstance(icd, str) else icd), 1, 3))
    letter, num = F.substring(c, 1, 1), F.substring(c, 2, 2).try_cast("int")
    return (F.when(c.isin("A90", "A91"), F.lit("dengue"))
             .when((letter == "A") & num.between(0, 9), F.lit("diarrhoea"))
             .when(c.isin("J09", "J10", "J11"), F.lit("ili"))
             .when((letter == "B") & num.between(50, 54), F.lit("malaria"))
             .otherwise(F.lit(OTHER)))


def with_epi_week(df: DataFrame, date_col: str = "onset_date") -> DataFrame:
    d = F.col(date_col)
    return (df.withColumn("epi_year", F.expr(f"extract(YEAROFWEEK FROM {date_col})").cast("int"))
              .withColumn("epi_week", F.weekofyear(d).cast("int"))
              .withColumn("week_start", F.to_date(F.date_trunc("week", d))))


def week_start_of(epi_year: str | Column, epi_week: str | Column) -> Column:
    """Monday of ISO week (epi_year, epi_week): Monday of the week holding 4 January, plus (week - 1) weeks."""
    y = F.col(epi_year) if isinstance(epi_year, str) else epi_year
    w = F.col(epi_week) if isinstance(epi_week, str) else epi_week
    jan4 = F.make_date(y, F.lit(1), F.lit(4))
    return F.date_add(F.to_date(F.date_trunc("week", jan4)), ((w - 1) * 7).cast("int"))


def type_encounters(df: DataFrame) -> DataFrame:
    """Clean S4 rows (strings) -> contract types. Rows reaching here passed the DQ_TYPE_* rules."""
    return df.select(
        F.col("encounter_id"), F.col("patient_id"), F.col("facility_id"),
        F.col("onset_date").try_cast("date").alias("onset_date"),
        F.col("report_date").try_cast("date").alias("report_date"),
        F.col("age_years").try_cast("int").alias("age_years"),
        F.col("sex"), F.upper(F.trim(F.col("icd10_code"))).alias("icd10_code"),
        F.col("lab_confirmed").try_cast("boolean").alias("lab_confirmed"),
        F.col("admitted").try_cast("boolean").alias("admitted"),
        F.col("outcome"), F.col("bronze_file"),
    )


def type_facilities(df: DataFrame) -> DataFrame:
    return df.select(
        "facility_id", "facility_name", "facility_type", "district_code", "upazila_code",
        F.col("latitude").try_cast("double").alias("latitude"),
        F.col("longitude").try_cast("double").alias("longitude"),
        F.col("beds").try_cast("int").alias("beds"),
        F.col("icu_beds").try_cast("int").alias("icu_beds"),
    )


STOCK_VALUES = ("iv_saline_units", "ns1_kits", "ors_packets", "antimalarial_courses", "occupied_beds")


def type_stock(df: DataFrame) -> DataFrame:
    return df.select(
        "facility_id", F.col("snapshot_date").try_cast("date").alias("snapshot_date"),
        *[F.col(c).try_cast("int").alias(c) for c in STOCK_VALUES], "bronze_file",
    )
