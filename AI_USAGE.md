# AI usage

Required by the brief (section 10). Each member must be able to explain any part of the code.

| Tool | Used for | Representative prompt |
|---|---|---|
| Claude Code | Reading the brief, primary-source research (docs/research.md), repository scaffold, proposal draft | "Analyse the CEP brief and list where the marks are and what can go wrong" |
| Claude Code (6 parallel agents, 2026-10-11) | First drafts of reference-data download, generator, quality/privacy libraries, detection/evaluation, forecast/optimiser/calendar, D3 dashboard. Directed by Shezan; each output reviewed before commit | The exact prompts sent are in `prompts/` (shared rules in `prompts/_shared.md`) |
| Claude Code | Video slides and script (github.com/Shinzuu/episcope-video), drafted with Claude Code and edited by the team | See that repository |
| Claude Code (2026-10-20 to 2026-10-21) | Submission audit (SUBMIT-AUDIT.md), ING-3/6/7 and scaling harness code, silver profiling and the rule-reuse fix, report draft in report/ and the group pack. Directed by Shezan; teammate sections are marked for each member to write or confirm | "Carry out BRIEF-SUBMIT.md: audit the gaps, close Shezan's, write the report" |
| Claude Code (3 subagents, 2026-10-21) | Simulated persona walkthroughs of the dashboard (district officer, store-keeper, ministry official), two rounds, reported in §7 as AI-agent walkthroughs, not real participants | Persona briefs and tasks in `docs/usability/README.md` |
