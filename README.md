# EpiScope

Big data surveillance, forecasting and response-planning platform (CSE 4345 CEP, Spring 2026).

**Video:** _link added at M3_

## Run

```bash
make setup          # Java 17 + Python 3.12 via mise, pinned deps, .env from .env.example
make all SF=0.25    # generate -> bronze -> silver -> gold -> analytics -> dashboard data
make dashboard      # http://localhost:8000
make check          # five verification gates (scripts/verify.py)
```

## Requirement traceability

| ID | File | Report section |
|---|---|---|
| ING-1 | generator/generate.py | §4 |
| DQ-1 | pipeline/quality/rules.py | §4 |
| DQ-2 | pipeline/quality/engine.py | §4 |
| DQ-3 | pipeline/quality/engine.py | §4 |
| PRV-1 | pipeline/privacy/deid.py | §4 |
| PRV-2 | pipeline/privacy/suppression.py | §4 |
| PRV-4 | pipeline/privacy/kanon.py | §4, §8 |
| AN-1 | analytics/detection/methods.py | §5 |
| AN-2 | analytics/evaluation/run.py | §6 |
| AN-3 | analytics/detection/prepare.py | §5, §6 |
| AN-4 | analytics/forecast/forecast.py | §5 |
| AN-5 | analytics/forecast/backtest.py | §6 |
| AN-6 | analytics/forecast/demand.py | §5 |
| AN-7 | analytics/optimise/lp.py | §5 |
| AN-8 | analytics/optimise/curve.py | §6 |
| AN-9 | analytics/calendar/stl_calendar.py | §5 |
| VIS-1 | dashboard/index.html | §7 |
| VIS-2 | dashboard/js/views/ | §7 |
| VIS-3 | dashboard/js/state.js | §7 |
| VIS-4 | serving/mock_data.py | §7 |
| VIS-5 | dashboard/js/views/allocation.js | §7 |
| VIS-6 | dashboard/css/style.css | §7 |
| VIS-7 | dashboard/css/style.css | §7 |
| ENG-1 | Makefile | §3 |
| ENG-4 | tests/ | §6 |

_Filled in as each requirement lands._

## Docs

- `docs/proposal.md`: M1 proposal
- `docs/ANALYSIS.md`: our reading of the brief, marks, traps, plan
- `docs/research.md`: primary-source research for every method, dataset and tool
- `generator/schema.json`: schema contract for S4–S6
