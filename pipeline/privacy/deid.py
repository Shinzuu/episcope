"""De-identification for silver. Implements PRV-1 (keyed pseudonymisation, age bands).

patient_id is replaced by HMAC-SHA256(K, patient_id), built natively from Spark's ``sha2`` with the RFC 2104
inner/outer pad construction (research.md section 9.1). No Python UDF runs over the rows:

    HMAC(K, m) = H((K' xor opad) || H((K' xor ipad) || m)),  K' = K padded (or hashed, if > 64 bytes) to 64 bytes

The key K is read from the environment variable EPISCOPE_HASH_SALT (set in .env, which is git-ignored). If it
is unset, blank or still the .env.example placeholder, the code raises instead of hashing with a weak key.
HMAC rather than sha256(salt || id) because a national-ID space is small enough to enumerate; the secret
key is the only protection.

Known limit: the padded keys travel as binary literals inside the Spark plan, so ``explain()`` output or an
enabled Spark event log contains K' xor ipad, from which K can be recovered. Keep event logging off for
silver jobs, or treat event logs as secret.
"""

from __future__ import annotations

import hashlib
import os

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

SALT_ENV = "EPISCOPE_HASH_SALT"
_PLACEHOLDER = "change-me-to-a-long-random-string"  # value shipped in .env.example
_BLOCK = 64  # SHA-256 block size in bytes

# Age bands of data/reference/population_age_sex.csv (docs/contracts.md), so incidence denominators line up.
AGE_BANDS = ((0, 4, "0-4"), (5, 14, "5-14"), (15, 24, "15-24"), (25, 44, "25-44"), (45, 64, "45-64"))
AGE_BAND_OPEN = (65, "65+")
AGE_VALID_MAX = 120


def _secret() -> bytes:
    value = os.environ.get(SALT_ENV)
    if value is None or not value.strip():
        raise RuntimeError(f"{SALT_ENV} is not set. Copy .env.example to .env and set a long random secret.")
    if value.strip() == _PLACEHOLDER:
        raise RuntimeError(f"{SALT_ENV} still holds the .env.example placeholder; set a real secret in .env.")
    return value.encode("utf-8")


def _pads(key: bytes) -> tuple[bytes, bytes]:
    if len(key) > _BLOCK:
        key = hashlib.sha256(key).digest()
    key = key.ljust(_BLOCK, b"\x00")
    return bytes(b ^ 0x36 for b in key), bytes(b ^ 0x5C for b in key)


def hmac_sha256(col: str | Column) -> Column:
    """Hex HMAC-SHA256 of a column under the EPISCOPE_HASH_SALT key. Null in, null out."""
    c = F.col(col) if isinstance(col, str) else col
    ipad, opad = _pads(_secret())
    message = c.cast("string").cast("binary")  # via string, so an int-typed ID hashes like its text form
    inner = F.unhex(F.sha2(F.concat(F.lit(bytearray(ipad)), message), 256))
    return F.sha2(F.concat(F.lit(bytearray(opad)), inner), 256)


def age_band(col: str | Column) -> Column:
    """Age in years -> band label (0-4, 5-14, 15-24, 25-44, 45-64, 65+). Invalid or missing ages give null."""
    c = (F.col(col) if isinstance(col, str) else col).try_cast("int")
    expr = F.when(c.isNull() | (c < 0) | (c > AGE_VALID_MAX), F.lit(None).cast("string"))
    for lo, hi, label in AGE_BANDS:
        expr = expr.when(c.between(lo, hi), F.lit(label))
    return expr.otherwise(F.lit(AGE_BAND_OPEN[1]))


def deidentify(df: DataFrame, id_col: str = "patient_id", age_col: str = "age_years",
               key_col: str = "patient_key", keep_age: bool = False) -> DataFrame:
    """Replace ``id_col`` by its keyed hash ``key_col`` and add ``age_band``. Drops the raw ID always and the
    exact age unless ``keep_age`` (exact age is a quasi-identifier; analytics only need the band)."""
    out = df.withColumn(key_col, hmac_sha256(id_col))
    if age_col in df.columns:
        out = out.withColumn("age_band", age_band(age_col))
        if not keep_age:
            out = out.drop(age_col)
    return out.drop(id_col)
