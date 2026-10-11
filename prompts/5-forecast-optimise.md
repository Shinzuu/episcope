<task>
Build three modules:
- analytics/forecast/ (AN-4, AN-5, AN-6): forecasting and demand conversion;
- analytics/optimise/ (AN-7, AN-8): the allocation linear programme;
- analytics/calendar/ (AN-9): the preparedness calendar.
</task>

<requirements>
1. **Forecasting (AN-4).** 1–4 week-ahead weekly counts per district for dengue and diarrhoea. Use a
   negative-binomial GLM (statsmodels) with lagged weather covariates (precipitation and temperature at
   lags of 4–8 weeks), seasonality terms and a recent-level term. Train one model per district in parallel
   with Spark applyInPandas. Output gold.forecasts with mean, q10, q50 and q90.
2. **Backtest (AN-5).** Rolling origins over the last 52 weeks, against a seasonal-naïve baseline. Report
   MAE, 80% interval coverage and WIS, using the exact formula in research.md §4. Write the results as
   CSV/JSON.
3. **Demand (AN-6).** Convert forecasts into resource demand (beds, IV saline, NS1 kits, ORS,
   antimalarials). Keep the conversion assumptions in one config file, each with a comment giving its
   source or saying it is an assumption. Carry the uncertainty through, using q90 for planning.
4. **Optimiser (AN-7).** PuLP with CBC, following brief §6.3:
   - minimise weighted unmet demand;
   - constraints: budget, equity floor α, transport capacity.

   Normalise unmet demand per resource so beds and kits are comparable, and document this as our
   extension (research.md §10). Compare with a population-proportional baseline. Output gold.allocation.
5. **Equity curve (AN-8).** Run at least 5 budget levels × several α values. Write the efficiency–equity
   curve data and recommend an α, with the reason.
6. **Calendar (AN-9).** STL per division and disease on weekly counts. Turn the seasonal component into
   "pre-position supplies by epi-week X" and "start campaign by epi-week Y", stating the lead-time rule.
   Output gold.calendar.
7. **Tests** in tests/test_forecast.py and tests/test_optimise.py:
   - the forecast has the right shape and its quantiles are ordered;
   - WIS is correct on a hand example;
   - LP constraints hold: the budget is never exceeded and the equity floor is met;
   - α = 0 matches the pure-efficiency answer;
   - the calendar marks the dengue pre-position week before the peak.
8. **Test data.** Until the real gold tables exist, use small synthetic inputs built in the tests.
</requirements>

<decisions>
Already decided: a negative-binomial GLM as the main model, for interpretability (conflict C5), and PuLP 3.3.2
with CBC.
Your call: the GLM feature set beyond the stated lags, and the demand conversion rates, which must be
documented.
</decisions>
