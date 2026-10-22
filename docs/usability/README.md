# Simulated persona walkthroughs (VIS usability check, report §7)

These are **simulated persona walkthroughs run with AI agents**, not tests with real people. No human
participant took part, and the quotes in the JSON files are written by the agents in each persona's voice.

## Protocol

- Three AI agents (Claude Code subagents), each briefed as one non-technical persona:
  - **a**: district health officer, Cox's Bazar; daily computer use, no statistics; opens the district view
    (`?district=BD2022`, PRV-3).
  - **b**: hospital store-keeper, Chattogram; plans orders in Excel, dislikes statistical terms; national view.
  - **c**: senior non-technical decision-maker at the Ministry; five minutes, defends budgets in meetings;
    national view.
- They used the real dashboard of the final clean SF=1 build, one headless Chrome at a time under a lock,
  through `dashboard/tools/walk.mjs` (clicks, selections, hovers, screenshots, visible text). They could not
  read or change the code.
- Five tasks each: (1) find this week's alerts for their district, (2) read a forecast with its range,
  (3) read the allocation and say why the district got what it got, (4) use the what-if panel,
  (5) read the policy brief.
- Recorded per task: success (yes / partly / no), browser runs and steps, the agent's estimate of a real
  user's time in seconds, where it got stuck, and short quotes. Then every problem, with a severity.
- Round 1, then fixes (commit ff8beaf), then a shorter confirm round 2 with the same agents, which also rated
  each round-1 problem fixed, partly fixed or not fixed. Problems found in round 2 were fixed afterwards
  (commit 62791b4) but not tested again.

Files: `persona-<a|b|c>-round<1|2>.json`.

## Real non-CS testers (to do)

Slot for two real non-CS testers, on the same five tasks, 20 to 30 minutes each. Not done yet. Records go in
`human-1.md` and `human-2.md` (task, done / partly / not done, problems seen, quotes), and into report §7
(Table VIII).
