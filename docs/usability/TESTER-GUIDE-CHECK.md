# Check of TESTER-GUIDE.md against the hosted dashboard (2026-10-23)

## What was checked

- **Data:** a clean SF=0.25 build (seed 42) from a fresh clone of v1.0 (`make all`, all stages ok), exported
  23 Oct 2026 15:52 UTC. The hosted copy is `dashboard/` without `roles/` (district views): 9.7 MB, on the
  `gh-pages` branch, served at https://shinzuu.github.io/episcope/.
- **Walks:** `dashboard/tools/walk.mjs` in one headless Chrome tab at 1366 px, closed after each run. First on
  the fresh data locally (`python3 -m http.server`, as `make dashboard`), then on the live Pages URL (same tool,
  navigating to the URL instead of a local server). Steps: `guide-check/steps.json` (all tasks) and
  `guide-check/steps-task4.json` (task 4 exactly as the guide says). Page errors: none in any run.
- **Result:** every label the guide names exists and every step works on the live site. One guide change came
  out of it: step 4.4 said "back to the middle", but the slider starts at its second of five stops, so it now
  says "back to where it started, at 50% of the cost of all need".
- The SF=0.25 numbers differ from the SF=1 numbers in the report (for example the plan's suggested minimum share
  is 0.9 here and 0.0 in the report). The guide asks only for what is on screen, so this does not affect the test.

## Steps, live site

| Guide step | What the walk did | What was seen | Screenshot |
|---|---|---|---|
| Start 1-3 | Opened https://shinzuu.github.io/episcope/ | Title **EpiScope**; boxes **Disease**, **District**, **Alert method**, **Time range**; map drawn | `guide-check/t0-open.png` |
| 1.1 | Read the line under the boxes | "On alert in the last full week (week of 22 Dec 25, EARS C2 (fast rises)): none." | `guide-check/t1-controls.png` |
| 1.2-1.3 | Clicked **dengue**; chose **Chattogram** in **District** | Both applied | `guide-check/t1-controls.png` |
| 1.4-1.5 | Read **Early warning ranking** | Chattogram row blue and scrolled into view; **Alert now** no, **Alert weeks** 0 | `guide-check/t1-table.png` |
| 1.6 | Looked at the map | **Cases per 100,000 people**; triangle key; Chattogram outlined in black | `guide-check/t1-map.png` |
| 2.2-2.3 | Read the weekly panel and forecast table | "Weekly dengue cases: Chattogram"; "Forecast for Chattogram, made on 29 Dec 2025"; columns **Week starting**, **Expected cases**, **Likely range**; row **Next 4 weeks** | `guide-check/t2-weekly.png` |
| 3.1-3.5 | Chose **1-litre bags of IV saline**; read the bold lines and the bars | **All districts**, **All supplies**, **Chattogram** lines; Chattogram bar outlined; key **in stock**, **sent by this plan**, **need, next 4 weeks (high estimate)** | `guide-check/t3-alloc.png` |
| 4.2-4.3 | **Budget for all supplies** to the right end | 2.93 million BDT (150%); **All supplies** 100%; Chattogram receives 575 | `guide-check/t4-budget-max.png` |
| 4.4-4.5 | Budget back to 50%; **Minimum share for every district (equity floor α)** to the right end | 1.0; **All supplies** 75%; Chattogram receives 221; chart **Cost of a minimum share** shown | `guide-check/t4-guide-path.png` |
| 5.1-5.2 | Fetched the report link | HTTP 200; PDF page 10, right column, "Appendix A, Policy Brief for the Director", continues on page 11 | (text check with `pdftotext`) |

## Answer key for Rimjhim (live site, export of 23 Oct 2026)

1. Chattogram is not on alert for dengue this week; 0 alert weeks in the shown range. The top line says no
   district is on alert this week for any disease ("none").
2. Week of 05 Jan 2026: 19 expected, likely 11 to 27. Next 4 weeks: 54 expected, plan on up to 80.
3. Chattogram needs 1,051 bags, has 476, is short by 575, and receives 288 (still short by 288) because the
   budget is half of what all need costs, so no district gets its whole shortfall. A good answer mentions the
   shortfall, not population.
4. More money (150%): every shortfall covered (100%), Chattogram gets all 575. Minimum share 1.0 at 50% budget:
   the average cover goes from 74% to 75% and Chattogram gets 221 instead of 288.
5. Approve the four-week supply plan, and decide whether every district gets a minimum share; about 7.8 million
   taka covers every shortfall; dengue supplies in Dhaka division by 13 July.
