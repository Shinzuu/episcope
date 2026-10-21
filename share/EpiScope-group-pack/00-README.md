# EpiScope group pack

Status, 21 Oct 2026. From Shezan, for Tahsin Ahmed Rafi, Rimjhim Dey and Samira.

## What is in the pack

| File | What it is |
|---|---|
| `01-Status-and-tasks.pdf` | Where we are, the headline results, every requirement with its status, and what each of us still has to do |
| `02-Report-draft.pdf` | The report in IEEE format, written from the final SF=1 run. Yellow boxes mark the team items |

Slides and script:

- Slides: https://shinzuu.github.io/episcope-video/
- Script: https://shinzuu.github.io/episcope-video/script.html

## Where we are

The code, the result files and the report draft are finished. What is left is personal: each of us writes a
half-page reflection, reads their own part of the video script, and records their segment. A few team items
close it off.

Checks on the final code (`make check`, commit 9c8fa53): all five gates pass: 156 unit tests, brief coverage (all 23 Must requirements traced), hidden-drop drill, limits and privacy, and performance (SF=1 in 28.3 minutes against a 45-minute limit).

Headline results, all from the final clean SF=1 build (34.1 million encounters); every number and its source
file is in `docs/FINAL-NUMBERS.md`:

1. **Speed:** the full pipeline runs in 25.0 minutes at SF=1 (gate 5 timing run: 28.3 minutes, limit 45). Two Spark optimisations were measured with unchanged outputs: silver 325 s to 214 s and the backtest 325 s to 165 s at SF=0.25.
2. **Detection:** EARS C2 finds 33 of 35 planted outbreaks, median 8 days, 1.89 false alerts per district-year; CUSUM finds 32.
3. **Forecasts:** relative WIS 0.84 against the seasonal-naive baseline (dengue 0.89, diarrhoea 0.72).
4. **Allocation:** fixed. At half the money needed it meets 68% of the need against 34% for a population split, and no district is ever sent more than it needs.
5. **Other:** quarantine 4.0%; patient de-duplication precision 0.999; streaming refresh in 219 s; hidden-drop drill passed on the final code; district-user view (PRV-3); SEIR scenarios (AN-10).
6. **Usability:** two rounds of *simulated* persona walkthroughs run with AI agents (not real users; the report says so), with the problems they found fixed.
7. **Report:** every section written from the SF=1 run, ten review passes and an examiner review, 11 pages, 0 warnings.

Every requirement (ING, DQ, PRV, AN, VIS, ENG) is done, except ING-5, which is skipped on purpose (local filesystem to save RAM; the report says why).

## What each of us still has to do

The script gives each of us a segment of at least four minutes on our lead area, in our own voice and with our name on screen (brief H3).

- **Shezan:** write your half-page reflection (report Appendix C); read your part of the script (data engineering); record your segment.
- **Rafi:** write your half-page reflection; read your part of the script (analytics); record your segment.
- **Rimjhim:** write your half-page reflection; read your part of the script (dashboard and privacy); record your segment.
- **Samira:** write your half-page reflection; read your part of the script (platform, evaluation and scaling); record your segment.
- **Together:** add the group number and student IDs to the report's title block; agree and sign the contribution matrix (Appendix B), including the video row and shares totalling 100; add the video link to the report and README once it is uploaded; tag `v1.0` and push only when everyone agrees the code is frozen.

## Rules for everyone

- Every number you say in the video comes from `docs/FINAL-NUMBERS.md`.
- Say "simulated persona walkthroughs run with AI agents" for the usability check; never call them user tests.
- Never commit `.env`, a salt or a key.
