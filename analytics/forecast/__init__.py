"""Forecasting and demand conversion. Implements AN-4 (forecast), AN-5 (backtest) and AN-6 (demand).

Modules:
    io                  lake paths and Delta read/write helpers shared by forecast, optimise and calendar
    features            weekly grid and NB-GLM design matrix (lagged weather, Fourier seasonality, recent level)
    model               NB2 GLM fit/predict with fallbacks, seasonal-naive baseline, predictive quantiles
    scoring             WIS (research.md §4.1), 80% coverage, MAE
    forecast            Spark job: one model set per district via cogrouped applyInPandas -> gold.forecasts
    backtest            rolling-origin backtest over the last 52 weeks vs seasonal naive -> CSV/JSON
    demand_assumptions  every conversion rate with its source or an ASSUMPTION note
    demand              forecasts -> resource demand -> gold.demand
"""
