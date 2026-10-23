// View 3: seasonal heatmap, ISO epi-week x epi-year, for the selected district and disease, with the
// division's preparedness calendar (calendar.json) as a marker row. Implements VIS-2 (view 3), VIS-3 (cell
// click sets the time range; the current range is outlined), VIS-6 (viridis, hatched suppression).
/* global d3 */
import { state, setState } from "../state.js";
import { addHatch, seqInterp, showTip, hideTip, rampLegend, legendKey, isoWeek, isoYear, fmtDate, fmtNum, inRange, esc, C } from "../util.js";

export function renderHeatmap(D) {
  const el = d3.select("#heat");
  el.selectAll("*").remove();
  const { district, disease: dz, t0, t1 } = state;
  const dist = D.districtByCode.get(district);
  document.getElementById("heat-title").textContent = `Seasonal pattern: ${dz}, ${dist ? dist.name : district}`;

  const s = D.getSeries(district, dz);
  const cells = D.weeks.map((w, i) => ({ w, i, wk: isoWeek(w), yr: isoYear(w), v: s[i] }));
  const years = Array.from(new Set(cells.map((c) => c.yr))).sort();
  const maxWk = d3.max(cells, (c) => c.wk) >= 53 ? 53 : 52;

  const width = el.node().clientWidth || 600;
  const m = { top: 26, right: 8, bottom: 30, left: 40 };
  const iw = width - m.left - m.right;
  const cw = iw / maxWk;
  const chh = Math.max(14, Math.min(26, cw * 1.6));
  const height = m.top + chh * years.length + m.bottom;

  const vals = cells.map((c) => c.v?.inc).filter((v) => v != null).sort(d3.ascending);
  const color = d3.scaleSequential(seqInterp).domain([0, d3.quantile(vals, 0.98) || 1]).clamp(true);

  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img").attr("aria-label", `Heatmap of weekly ${dz} incidence by epi-week and year`);
  const hatch = addHatch(svg, "hatch-heat");
  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  const x = (wk) => (wk - 1) * cw;
  const y = d3.scaleBand().domain(years).range([0, chh * years.length]).paddingInner(0.08);

  g.selectAll("rect.cell").data(cells).join("rect").attr("class", "cell")
    .attr("x", (c) => x(c.wk)).attr("y", (c) => y(c.yr)).attr("width", Math.max(1, cw - 1)).attr("height", y.bandwidth())
    .attr("fill", (c) => (!c.v ? C.nodata : c.v.s ? hatch : color(c.v.inc ?? 0)))
    .attr("stroke", (c) => (inRange(c.w, t0, t1) ? "#000" : "none")).attr("stroke-width", 1)
    .style("cursor", "pointer")
    .on("mousemove", (event, c) => {
      const val = !c.v ? "no report" : c.v.s ? "hidden for privacy" : `${fmtNum(c.v.c)} cases, ${fmtNum(c.v.inc, 2)} per 100,000 people`;
      showTip(`<b>${c.yr} W${c.wk}</b> (${fmtDate(c.w)})<br>${esc(val)}`, event);
    })
    .on("mouseleave", hideTip)
    .on("click", (event, c) => setState({ t0: c.w, t1: c.w }, "heat"));

  g.append("g").attr("class", "axis").attr("transform", `translate(0,${chh * years.length})`)
    .call(d3.axisBottom(d3.scaleLinear().domain([0.5, maxWk + 0.5]).range([0, iw]))
      .tickValues(d3.range(1, maxWk + 1).filter((w) => w === 1 || w % (cw < 9 ? 13 : 4) === 0)).tickSizeOuter(0));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).tickSize(0)).select(".domain").remove();
  g.append("text").attr("class", "axis-label").attr("x", iw / 2).attr("y", chh * years.length + 27).attr("text-anchor", "middle")
    .text("ISO epi-week");

  // Preparedness calendar for this district's division.
  const cal = D.getCalendar(dist?.div, dz).filter((c) => c.action && c.action !== "none");
  const sym = { preposition: d3.symbolTriangle, campaign: d3.symbolCircle };
  g.selectAll("path.cal").data(cal).join("path").attr("class", "cal")
    .attr("transform", (c) => `translate(${x(c.w) + cw / 2},-9)`)
    .attr("d", (c) => d3.symbol(sym[c.action] || d3.symbolSquare, 42)())
    .attr("fill", (c) => C[c.action] || C.muted).attr("stroke", "#fff")
    .on("mousemove", (event, c) => showTip(`<b>Week ${c.w}</b>: ${esc(c.action)}<br>seasonal index ${fmtNum(c.idx, 2)} (${esc(c.div)})`, event))
    .on("mouseleave", hideTip);
  if (!cal.length && D.getCalendar(dist?.div, dz).length) {   // calendar's low-signal rule: no season to time
    g.append("text").attr("class", "axis-label").attr("x", 0).attr("y", -5)
      .text("No preparedness weeks: too few cases in this division for a season");
  }

  const lg = d3.select("#heat-legend");
  lg.selectAll("*").remove();
  rampLegend(lg, color, "weekly cases per 100,000 people");
  legendKey(lg, "hatch", "hidden for privacy (small count, or a cell that would reveal one)");
  legendKey(lg, "symbol", "pre-position supplies", { color: C.preposition, symbol: d3.symbolTriangle });
  legendKey(lg, "symbol", "start campaign", { color: C.campaign, symbol: d3.symbolCircle });
  legendKey(lg, "rect", "selected range", { color: "#fff", stroke: "#000" });
}
