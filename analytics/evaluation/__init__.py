"""Detection evaluation against injected outbreaks. Implements AN-2 (sensitivity, false alerts per
district-year, median days to detection, and the C1 threshold trade-off curve with a recommended operating
point).

This is the only package allowed to read ground_truth/outbreaks.csv. analytics/detection never does.

Modules:
    metrics  pure-pandas scoring of alerts against outbreak windows
    sweep    threshold sweep for EARS C2 and CUSUM, operating-point recommendation
    run      `python -m analytics.evaluation.run`: writes the trade-off curve as CSV and JSON
"""
