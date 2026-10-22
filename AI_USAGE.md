# AI usage

Required by the brief (section 10). Each member must be able to explain any part of the code.

| Tool | Used for | Representative prompt |
|---|---|---|
| Claude Code | Reading the brief, primary-source research (docs/research.md), repository scaffold, proposal draft | "Analyse the CEP brief and list where the marks are and what can go wrong" |
| Claude Code (6 parallel agents, 2026-10-11) | First drafts of reference-data download, generator, quality/privacy libraries, detection/evaluation, forecast/optimiser/calendar, D3 dashboard. Directed by Shezan; each output reviewed before commit | The exact prompts sent are in `prompts/` (shared rules in `prompts/_shared.md`) |
| Claude Code | Video slides and script (github.com/Shinzuu/episcope-video), drafted with Claude Code and edited by the team | See that repository |
| Claude Code (2026-10-20 to 2026-10-21) | Submission gap audit (an internal working file, not in the repository), ING-3/6/7 and scaling harness code, silver profiling and the rule-reuse fix, report draft in report/ and an internal status pack for the team. Directed by Shezan; teammate sections are marked for each member to write or confirm | "Carry out BRIEF-SUBMIT.md: audit the gaps, close Shezan's, write the report" |
| Claude Code (3 subagents, 2026-10-21) | Simulated persona walkthroughs of the dashboard (district officer, store-keeper, ministry official), two rounds, reported in §7 as AI-agent walkthroughs, not real participants | Persona briefs and tasks in `docs/usability/README.md` |
| Claude Code (2026-10-21) | Ten review passes over the report, run with Claude Code, one lens each: errors, proofreading, punctuation, style, readability, and sweeps for wording that reads as machine-written ("humanizer" and "AI-detect" passes); then an examiner-style review. Directed by Shezan; numbers were checked against the result files | "Review the report with one lens per pass and fix what you find; never change a number without its source" |
| Claude Code (2026-10-22) | A fresh-reviewer pass over the repository, report, slides and script (31 issues), and the fixes: contribution text, video parts in each presenter's lead area, repository clean-up, wording and rounding. Directed by Shezan | "Fix what a fresh reviewer finds, repeat until clean" |
