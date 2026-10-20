# EpiScope group pack

Draft v0.1, 20 Oct 2026. From Shezan, for Rafi, Rimjhim and Samira.

## What is in the pack

| File | What it is |
|---|---|
| `01-Status-and-tasks.pdf` | Every requirement with its status, then one section per member with open tasks, effort and the files to touch |
| `02-Report-draft.pdf` | The report skeleton in IEEE format with every required section. Data engineering is filled in; yellow boxes mark what each of you writes |

Slides and script:

- Slides: https://shinzuu.github.io/episcope-video/
- Script: https://shinzuu.github.io/episcope-video/script.html

## Where the project stands

1. The pipeline runs end to end with one command (`make setup && make all`) and passes 141 automated tests.
2. The hidden-drop drill passed from a clean clone (SF=0.05): new 14-day drop ingested unchanged, no duplicates on a re-run, export 9.64 MB.
3. Missing evidence: the scaling study (SF 0.25, 0.5, 1), Spark optimisation before and after, detection and backtest result files, the usability test.
4. Missing writing: most of the report, the policy brief, the contribution matrix and the reflections.
5. Every commit so far is Shezan's, all on one day. R9 grades your own Git share, so please commit your own work from now on.

## What we need from each of you

- **Rafi:** check the allocation (in the test run the LP had almost no need to allocate), the equity curve's recommendation, the artefact figure; report §5 and the analytics results.
- **Rimjhim:** usability test with two non-CS users, the role-based view (PRV-3), dashboard screenshots; report §7.
- **Samira:** scaling write-up, two Spark optimisations with Spark UI screenshots, a DAG with retries, the detection and backtest result files; report lead, policy brief, contribution matrix.
- **Everyone:** your own half-page reflection, small commits under your own name with the requirement ID first (`AN-8: ...`), and no `.env`, salts or keys in Git.

The deadline has not been announced yet. Please plan as if it is a few days away.
