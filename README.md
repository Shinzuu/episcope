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
| ING-1 | generator/, pipeline/bronze/ | §4 |

_Filled in as each requirement lands._

## Docs

- `docs/proposal.md`: M1 proposal
- `docs/ANALYSIS.md`: our reading of the brief, marks, traps, plan
- `docs/research.md`: primary-source research for every method, dataset and tool
- `generator/schema.json`: schema contract for S4–S6
