# Check of TESTER-GUIDE.md against the live dashboard (2026-10-23)

Every step of `TESTER-GUIDE.md` was walked in one headless Chrome with `dashboard/tools/walk.mjs`. The tool
serves `dashboard/` with `python3 -m http.server` (the same server as `make dashboard`) on a free local port,
opens one tab at 1366 px width, and closes Chrome and the server when it ends. Steps file:
`guide-check/steps.json`. Page errors: none.

The dashboard data in this checkout is an SF=0.25 export from 21 Oct 2026 06:51 UTC, older than the allocation
fix. Labels and layout come from the code at HEAD, so they match what testers will see; the numbers will differ
on the copy that is hosted for testers (see "Data to host" below).

| Guide step | What the walk did | What was seen | Screenshot |
|---|---|---|---|
| Start 1-3 | Opened the page | Title **EpiScope**; boxes **Disease**, **District**, **Alert method**, **Time range**; map drawn | `guide-check/t0-open.png` |
| 1.1 | Read the line under the boxes | "On alert in the last full week (week of 22 Dec 25, EARS C2 (fast rises)): ili: Bandarban." | `guide-check/t1-controls.png` |
| 1.2-1.3 | Clicked **dengue**; chose **Chattogram** in **District** | Both applied; panel titles follow the choice | `guide-check/t1-controls.png` |
| 1.4-1.5 | Read the **Early warning ranking** table | Chattogram row highlighted blue and scrolled into view; columns **Alert now** (no) and **Alert weeks** (2) | `guide-check/t1-table.png` |
| 1.6 | Looked at the map | Panel **Cases per 100,000 people**; triangles on districts; key "on alert in the last full week" (solid) and "alert earlier in the range" (outline); Chattogram has a thick black outline | `guide-check/t1-map.png` |
| 2.2 | Read the weekly panel | Title "Weekly dengue cases: Chattogram"; line chart | `guide-check/t2-weekly.png` |
| 2.3 | Read the forecast table | "Forecast for Chattogram, made on 29 Dec 2025"; columns **Week starting**, **Expected cases**, **Likely range**; last row **Next 4 weeks** ("54, up to 80 (plan on this)") | `guide-check/t2-weekly.png` |
| 3.1-3.2 | Chose **1-litre bags of IV saline** in **Supply shown** | Option exists and applies | `guide-check/t3-alloc.png` |
| 3.3-3.4 | Read the bold lines | **All districts** ..., **All supplies** ..., **Chattogram**: "needs 1,051, has 72,236, short by 0; receives 0, still short by 0. Its stock already covers its need." | `guide-check/t3-alloc.png` |
| 3.5 | Looked at the bar chart | One bar per district, Chattogram outlined; key **in stock**, **sent by this plan**, **need, next 4 weeks (high estimate)** | `guide-check/t3-alloc.png` |
| 4.2-4.3 | Moved **Budget for all supplies** to the right end | Label value changed (3,300 to 6,600 BDT on this old export) | `guide-check/t4-budget-max.png` |
| 4.5 | Moved **Minimum share for every district (equity floor α)** to the right end (budget at the left end) | Label 1.0; **All supplies** line changed (47% to 0%); chart **Cost of a minimum share** present | `guide-check/t4-floor-max.png` |
| 5.1-5.2 | Fetched the report link (public repo) | HTTP 200; PDF page 10, right column, "Appendix A, Policy Brief for the Director", continues on page 11 | (text check with `pdftotext`) |
| 5 answers | Read the brief | Decision: approve the four-week supply plan and whether to set a minimum share; full cost about 7.8 million taka; Dhaka division dengue supplies in place by 13 Jul | (text check) |

## Answer key for Rimjhim (from the hosted data, fill in after the export)

Task 1-4 numbers depend on the export that is hosted. Read them off the hosted copy once, before the testers start,
and write them here. Task 5 answers come from the report and do not change.

## Data to host

- On this old export, Chattogram has no IV saline shortfall, so task 3 shows "receives 0" and task 4 changes
  little for saline. The hosted copy needs a fresh export from the current code (the final SF=1 data shown in the
  report was deleted with its workspace).
- The dashboard is fully static: it reads only relative `data/*.json` files and two local scripts in `vendor/`,
  and it ran from `python3 -m http.server` with no other service. A GitHub Pages copy of `dashboard/` would work.
- Size: `dashboard/` 56 MB in all; `data/` 9.4 MB, `roles/` 46 MB (district views, not used by the guide),
  code and libraries under 0.5 MB. Without `roles/`, about 10 MB.
