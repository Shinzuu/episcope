// View 4: allocation for the scenario picked by the what-if sliders: demand vs stock vs recommended allocation
// per district, plus the efficiency-equity curve (unmet demand vs alpha) for the chosen budget.
// Implements VIS-2 (view 4), VIS-3 (bar click selects district; selected district always shown and outlined),
// VIS-5 (sliders read the precomputed scenario grid in allocation.json; threshold slider rescales alerts).
/* global d3 */
import { state, setState } from "../state.js";
import { showTip, hideTip, legendKey, fmtNum, esc, C } from "../util.js";

const DISEASE_RESOURCE = { dengue: "ns1_kits", diarrhoea: "ors", malaria: "antimalarials", ili: "beds" };
export const defaultResource = (D, dz) => (D.resources.includes(DISEASE_RESOURCE[dz]) ? DISEASE_RESOURCE[dz] : D.resources[0]);

const scenarioFor = (D, budget, alpha) => D.scenarios.find((s) => s.budget === budget && s.alpha === alpha)
  || D.scenarios.reduce((best, s) => (Math.abs(s.budget - budget) + Math.abs(s.alpha - alpha)
    < Math.abs(best.budget - budget) + Math.abs(best.alpha - alpha) ? s : best));

export function buildWhatIf(D) {
  const box = d3.select("#whatif");
  box.selectAll("*").remove();
  const slider = (id, label, values, current, fmt, onInput) => {
    const l = box.append("label");
    l.append("span").text(`${label}: `).append("output").attr("id", `${id}-out`).text(fmt(current));
    l.append("input").attr("type", "range").attr("id", id)
      .attr("min", 0).attr("max", values.length - 1).attr("step", 1)
      .property("value", Math.max(0, values.indexOf(current)))
      .attr("aria-valuetext", fmt(current))
      .on("input", function () {
        const v = values[+this.value];
        d3.select(`#${id}-out`).text(fmt(v));
        this.setAttribute("aria-valuetext", fmt(v));
        onInput(v);
      });
  };
  slider("sl-budget", "Budget", D.budgets, state.budget, (v) => fmtNum(v, v < 10 ? 2 : 0), (v) => setState({ budget: v }, "whatif"));
  slider("sl-alpha", "Equity floor α", D.alphas, state.alpha, (v) => fmtNum(v, 2), (v) => setState({ alpha: v }, "whatif"));
  const mults = d3.range(5, 21).map((v) => v / 10);
  slider("sl-thr", "Detection threshold", mults, state.thrMult, (v) => `× ${v.toFixed(1)}`, (v) => setState({ thrMult: v }, "whatif"));
  const l = box.append("label");
  l.append("span").text("Resource");
  l.append("select").attr("id", "resource-select")
    .on("change", function () { setState({ resource: this.value }, "whatif"); })
    .selectAll("option").data(D.resources).join("option").attr("value", (r) => r).text((r) => r.replace(/_/g, " "));
  d3.select("#resource-select").property("value", state.resource);
}

export function renderAllocation(D) {
  d3.select("#resource-select").property("value", state.resource);
  const el = d3.select("#alloc");
  el.selectAll("*").remove();
  const sc = scenarioFor(D, state.budget, state.alpha);
  const rows = D.getAlloc(sc.id, state.resource).map((r) => ({ ...r, name: D.districtByCode.get(r.d)?.name || r.d }));
  const width = el.node().clientWidth || 600;
  const narrow = width < 520;
  const topN = narrow ? 10 : 14;
  const ranked = [...rows].sort((a, b) => d3.descending(a.demand, b.demand) || d3.descending(a.unmet, b.unmet));
  let shown = ranked.slice(0, topN);
  const sel = rows.find((r) => r.d === state.district);
  if (sel && !shown.includes(sel)) shown = [...shown.slice(0, topN - 1), sel];

  const tot = (k) => d3.sum(rows, (r) => r[k]);
  const need = d3.sum(rows, (r) => Math.max(r.demand - r.stock, 0));
  el.append("p").attr("class", "summary").html(
    `Scenario #${esc(sc.id)} · budget <b>${fmtNum(sc.budget, sc.budget < 10 ? 2 : 0)}</b> · α <b>${fmtNum(sc.alpha, 2)}</b> · `
    + `${esc(state.resource.replace(/_/g, " "))}: demand <b>${fmtNum(tot("demand"))}</b>, stock <b>${fmtNum(tot("stock"))}</b>, `
    + `allocated <b>${fmtNum(tot("alloc"))}</b>, unmet <b>${fmtNum(tot("unmet"))}</b>`
    + (need > 0 ? ` (${fmtNum(100 * (1 - tot("unmet") / need), 0)}% of shortfall covered)` : ""),
  );

  const m = { top: 6, right: 14, bottom: 34, left: narrow ? 92 : 112 };
  const rowH = 20;
  const ih = shown.length * rowH;
  const iw = width - m.left - m.right;
  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${ih + m.top + m.bottom}`)
    .attr("role", "img").attr("aria-label", `Demand, stock and allocation of ${state.resource} by district`);
  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  const x = d3.scaleLinear().domain([0, d3.max(shown, (r) => Math.max(r.demand, r.stock + r.alloc)) || 1]).nice().range([0, iw]);
  const y = d3.scaleBand().domain(shown.map((r) => r.d)).range([0, ih]).padding(0.22);

  g.append("g").attr("class", "grid-line").call(d3.axisBottom(x).ticks(narrow ? 3 : 5).tickSize(ih).tickFormat(""));
  const row = g.selectAll("g.row").data(shown).join("g").attr("class", "row")
    .attr("transform", (r) => `translate(0,${y(r.d)})`).style("cursor", "pointer")
    .on("click", (event, r) => setState({ district: r.d }, "alloc"))
    .on("mousemove", (event, r) => showTip(
      `<b>${esc(r.name)}</b><br>Demand (q90): ${fmtNum(r.demand)}<br>Stock: ${fmtNum(r.stock)}`
      + `<br>Recommended allocation: ${fmtNum(r.alloc)}<br>Unmet after allocation: ${fmtNum(r.unmet)}`, event))
    .on("mouseleave", hideTip);
  row.append("rect").attr("width", iw).attr("height", y.bandwidth()).attr("fill", (r) => (r.d === state.district ? "#e3eefb" : "transparent"));
  row.append("rect").attr("width", (r) => x(r.stock)).attr("height", y.bandwidth()).attr("fill", C.stock);
  row.append("rect").attr("x", (r) => x(r.stock)).attr("width", (r) => x(r.stock + r.alloc) - x(r.stock)).attr("height", y.bandwidth()).attr("fill", C.alloc);
  row.append("line").attr("x1", (r) => x(r.demand)).attr("x2", (r) => x(r.demand)).attr("y1", -2).attr("y2", y.bandwidth() + 2)
    .attr("stroke", C.demand).attr("stroke-width", 2.2);
  row.filter((r) => r.d === state.district).append("rect").attr("width", iw).attr("height", y.bandwidth())
    .attr("fill", "none").attr("stroke", "#000").attr("stroke-width", 1.5);

  g.append("g").attr("class", "axis").call(d3.axisLeft(y).tickSize(0).tickFormat((d) => D.districtByCode.get(d)?.name || d))
    .select(".domain").remove();
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${ih})`).call(d3.axisBottom(x).ticks(narrow ? 3 : 5).tickFormat(d3.format("~s")));
  g.append("text").attr("class", "axis-label").attr("x", iw / 2).attr("y", ih + 29).attr("text-anchor", "middle")
    .text(`units of ${state.resource.replace(/_/g, " ")} (top ${topN} districts by demand)`);

  const lg = d3.select("#alloc-legend");
  lg.selectAll("*").remove();
  legendKey(lg, "rect", "current stock", { color: C.stock });
  legendKey(lg, "rect", "recommended allocation", { color: C.alloc });
  legendKey(lg, "line", "forecast demand (q90)", { color: C.demand, width: 2.5 });

  renderEquity(D, sc, width);
}

// Efficiency-equity curve: total unmet demand against alpha, one line per budget, current scenario marked.
function renderEquity(D, sc, width) {
  const el = d3.select("#equity");
  el.selectAll("*").remove();
  const series = D.budgets.map((b) => ({
    b,
    pts: D.alphas.map((a) => {
      const s = D.scenarios.find((q) => q.budget === b && q.alpha === a);
      return { a, unmet: s ? d3.sum(D.getAlloc(s.id, state.resource), (r) => r.unmet) : null };
    }),
  }));
  const m = { top: 22, right: 64, bottom: 32, left: 50 };
  const h = 150;
  const iw = width - m.left - m.right;
  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${h + m.top + m.bottom}`)
    .attr("role", "img").attr("aria-label", "Unmet demand against equity floor alpha, one line per budget");
  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  svg.append("text").attr("x", 0).attr("y", 13).attr("font-size", 12).attr("font-weight", 600)
    .text("Cost of equity: unmet demand vs α");
  const x = d3.scaleLinear().domain(d3.extent(D.alphas)).range([0, iw]);
  const y = d3.scaleLinear().domain([0, d3.max(series, (s) => d3.max(s.pts, (p) => p.unmet)) || 1]).nice().range([h, 0]);
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(5));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(4).tickFormat(d3.format("~s")));
  g.append("text").attr("class", "axis-label").attr("x", iw / 2).attr("y", h + 28).attr("text-anchor", "middle").text("equity floor α");
  g.append("text").attr("class", "axis-label").attr("transform", "rotate(-90)").attr("x", -h / 2).attr("y", -40)
    .attr("text-anchor", "middle").text("total unmet");
  const line = d3.line().defined((p) => p.unmet != null).x((p) => x(p.a)).y((p) => y(p.unmet));
  for (const s of series) {
    const cur = s.b === sc.budget;
    g.append("path").datum(s.pts).attr("fill", "none").attr("stroke", cur ? C.forecast : "#b8c1cc")
      .attr("stroke-width", cur ? 2.4 : 1.2).attr("d", line);
    const last = s.pts[s.pts.length - 1];
    if (last.unmet != null) {
      g.append("text").attr("x", iw + 4).attr("y", y(last.unmet) + 4).attr("font-size", 10)
        .attr("fill", cur ? C.forecast : C.muted).text(`B=${fmtNum(s.b, s.b < 10 ? 2 : 0)}`);
    }
  }
  const curPt = series.find((s) => s.b === sc.budget)?.pts.find((p) => p.a === sc.alpha);
  if (curPt?.unmet != null) {
    g.append("circle").attr("cx", x(curPt.a)).attr("cy", y(curPt.unmet)).attr("r", 5).attr("fill", C.forecast).attr("stroke", "#fff")
      .on("mousemove", (event) => showTip(`Budget ${fmtNum(sc.budget, 2)}, α ${fmtNum(sc.alpha, 2)}<br>Total unmet: ${fmtNum(curPt.unmet)}`, event))
      .on("mouseleave", hideTip);
  }
}
