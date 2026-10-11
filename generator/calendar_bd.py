"""Bangladesh reporting calendar: Fri/Sat weekend and public holidays, for the report-date dip.

Implements ING-1 (realistic reporting: fewer reports on Fri/Sat and public holidays, research.md §8.3).

Holiday list (our choice, documented for the report):
- fixed-date: 21 Feb, 26 Mar, 14 Apr, 1 May, 16 Dec, 25 Dec; 17 Mar and 15 Aug up to 2023; 5 Aug from 2025;
- Islamic, from the tabular (arithmetic) Hijri calendar (Eid 2023-2025 checked against BD dates):
  Eid-ul-Fitr block (-2..+2 around 1 Shawwal), Eid-ul-Azha block (-2..+3 around 10 Dhu al-Hijjah),
  Ashura (10 Muharram), Eid-e-Miladunnabi (12 Rabi al-Awwal), Shab-e-Barat (15 Sha'ban),
  Shab-e-Qadr (27 Ramadan).
The arithmetic calendar is within about a day of the announced dates, which is enough for a reporting dip,
and it works for any year, so a hidden drop in a year we never saw still gets its holidays.
Hindu and Buddhist lunisolar holidays are left out.
"""

import math

import numpy as np

_EPOCH_JD = 2440587.5          # Julian date of 1970-01-01
_ISLAMIC_EPOCH_JD = 1948439.5  # civil epoch of the Hijri calendar
_BD_SIGHTING_OFFSET = 0  # checked: Eid 2023-2025 BD dates match 4 of 5, the other is 1 day off


def _hijri_to_day(year, month, day):
    """Days since 1970-01-01 of a tabular Hijri date (sighting offset applied)."""
    jd = (day + math.ceil(29.5 * (month - 1)) + (year - 1) * 354 + (3 + 11 * year) // 30
          + _ISLAMIC_EPOCH_JD - 1)
    return int(round(jd - _EPOCH_JD)) + _BD_SIGHTING_OFFSET


def holidays(first, last):
    """Sorted int64 array of holiday dates (days since epoch) between two numpy datetime64[D] dates."""
    y0 = int(str(first)[:4])
    y1 = int(str(last)[:4])
    days = set()
    for y in range(y0, y1 + 1):
        fixed = ["02-21", "03-26", "04-14", "05-01", "12-16", "12-25"]
        if y <= 2023:
            fixed += ["03-17", "08-15"]
        if y >= 2025:
            fixed += ["08-05"]
        for md in fixed:
            days.add(int(np.datetime64(f"{y}-{md}").astype(np.int64)))
    # Hijri years overlapping the Gregorian range (a Hijri year is ~354 days).
    h0 = int((y0 - 622) * 33 / 32) - 1
    h1 = int((y1 - 622) * 33 / 32) + 2
    for h in range(h0, h1 + 1):
        fitr = _hijri_to_day(h, 10, 1)
        azha = _hijri_to_day(h, 12, 10)
        days.update(range(fitr - 2, fitr + 3))
        days.update(range(azha - 2, azha + 4))
        days.add(_hijri_to_day(h, 1, 10))
        days.add(_hijri_to_day(h, 3, 12))
        days.add(_hijri_to_day(h, 8, 15))
        days.add(_hijri_to_day(h, 9, 27))
    lo = int(np.datetime64(first, "D").astype(np.int64))
    hi = int(np.datetime64(last, "D").astype(np.int64))
    return np.array(sorted(d for d in days if lo <= d <= hi), dtype=np.int64)


def weekday(days):
    """ISO weekday (Mon=0 .. Sun=6) for int days since 1970-01-01 (a Thursday)."""
    return (np.asarray(days) + 3) % 7


FRIDAY, SATURDAY, SUNDAY = 4, 5, 6
