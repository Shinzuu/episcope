"""Outbreak detection per district and disease. Implements AN-1 (EARS C2 and CUSUM in Spark) and AN-3
(two methods plus the reporting artefact handled: onset-week counts, silent facility-weeks treated as missing).

Modules:
    config   defaults and paths
    prepare  dense weekly calendar, reporting completeness, leave-one-year-out seasonal index (Spark windows)
    methods  EARS C2 (Spark window functions), CUSUM (grouped applyInPandas), the naive baseline detector
    run      `python -m analytics.detection.run`: gold.cases_weekly -> gold.alerts

This package reads only gold.cases_weekly. It never reads evaluation labels; tests/test_detection.py enforces
that with a grep.
"""
