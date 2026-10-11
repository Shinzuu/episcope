<task>
Build dashboard/: the D3.js v7 decision dashboard (VIS-1 to VIS-6). It reads only dashboard/data/*.json in
the shapes given in docs/contracts.md "Serving".
</task>

<requirements>
1. **Mock data.** The real exports don't exist yet. Write serving/mock_data.py, which generates realistic
   mock JSON in exactly the contract shapes for 64 districts and 4 diseases, under 10 MB, with some
   suppressed cells. Build the dashboard against that. The TopoJSON map file is being produced by another
   worker at dashboard/data/bd_districts.topo.json. Until it exists, draw a placeholder grid map that uses
   the same district codes.
2. **Libraries.** D3 v7 only, plus topojson-client and d3-sankey if needed, loaded from cdn.jsdelivr.net
   with pinned versions and an SRI hash, or vendored into dashboard/vendor/. Plotly, Chart.js and
   Vega-Lite are not allowed.
3. **Five coordinated views (VIS-2):**
   1. a choropleth of incidence per 100k, with alert markers;
   2. a time series with brush/zoom showing observed counts, the forecast with q10–q90 bands, and the alert
      threshold;
   3. a seasonal heatmap of epi-week × year;
   4. an allocation view comparing demand, stock and the recommended allocation;
   5. a ranked early-warning table.
4. **Linked interaction (VIS-3).** Selecting a district, disease or time range in any view filters or
   highlights all the others. Every view has tooltips, legends and axis labels.
5. **What-if panel (VIS-5).** Sliders for budget, detection threshold and equity floor α. They read from
   the precomputed scenario grid in allocation.json.
6. **Honesty (VIS-6):**
   - a colour-blind-safe palette;
   - uncertainty always shown;
   - suppressed cells hatched, with a "suppressed (<5)" legend;
   - a data-freshness timestamp from meta.json on every view.
7. **Running it.** `python3 -m http.server` from dashboard/ (a Makefile target exists). It works on laptop
   and phone widths, and the mobile district view (VIS-7) is a bonus.
8. **Verification.** Headless Chrome screenshots at 390 px and 1366 px wide, with no console errors, saved
   to /tmp/claude-1000/dash/. Check that a selection in the map changes the other views.
</requirements>

<decisions>
Already decided: D3 v7 and the JSON contract.
Your call: layout, palette (colour-blind-safe), and module structure (ES modules, no build step).
Ask me before: adding any build tooling such as npm bundlers.
</decisions>
