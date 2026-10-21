// View 4: allocation for the scenario picked by the what-if sliders: demand vs stock vs recommended allocation
// per district, plus the efficiency-equity curve (unmet demand vs alpha) for the chosen budget.
// Implements VIS-2 (view 4), VIS-3 (bar click selects district; selected district always shown and outlined),
// VIS-5 (sliders read the precomputed scenario grid in allocation.json; threshold slider rescales alerts).
/* global d3 */
import { state, setState } from "../state.js";
import { showTip, hideTip, legendKey, fmtNum, esc, resourceLabel, C } from "../util.js";

const DISEASE_RESOURCE = { dengue: "ns1_kits", diarrhoea: "ors", malaria: "antimalarials", ili: "beds" };
const ORDER = ["iv_saline", "ors", "ns1_kits", "antimalarials", "beds"];
const shortfall = (rows) => d3.sum(rows, (r) => Math.max(r.demand - r.stock, 0));
// The disease's own supply if anything is short nationally, else the supply with a shortfall (persona
// walkthroughs: opening on a supply with nothing to allocate looked like an empty panel).
export function defaultResource(D, dz) {
  const sc = D.scenarios[0];
  const short = (r) => (sc ? shortfall(D.getAlloc(sc.id, r)) > 0 : true);
  const own = DISEASE_RESOURCE[dz];
  if (D.resources.includes(own) && short(own)) return own;
  return ORDER.find((r) => D.resources.includes(r) && short(r)) || (D.resources.includes(own) ? own : D.resources[0]);
}
const budgetText = (D, v) => (D.bRef
  ? `${fmtNum(v / 1e6, 2)} million BDT (${fmtNum(100 * v / D.bRef, 0)}% of the cost of all need)`
  : `${fmtNum(v, v < 10 ? 2 : 0)} BDT`);

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
  slider("sl-budget", "Budget for all supplies", D.budgets, state.budget, (v) => budgetText(D, v), (v) => setState({ budget: v }, "whatif"));
  slider("sl-alpha", "Minimum share for every district (equity floor α)", D.alphas, state.alpha,
    (v) => `${fmtNum(v, 1)}${v === D.recommendedAlpha ? " (the plan's suggestion)" : ""}`, (v) => setState({ alpha: v }, "whatif"));
  const mults = d3.range(5, 21).map((v) => v / 10);
  slider("sl-thr", "Alert level (higher means fewer alerts)", mults, state.thrMult, (v) => `× ${v.toFixed(1)}`, (v) => setState({ thrMult: v }, "whatif"));
  const l = box.append("label");
  l.append("span").text("Supply shown");
  l.append("select").attr("id", "resource-select")
    .on("change", function () { setState({ resource: this.value }, "whatif"); })
    .selectAll("option").data(D.resources).join("option").attr("value", (r) => r).text((r) => resourceLabel(r));
  d3.select("#resource-select").property("value", state.resource);
  box.append("p").attr("class", "hint").text(
    "The plan shares one budget across all supplies for the next four weeks and never sends a district more "
    + "than its shortfall. A floor of 0 sends supplies only where they cover the most need; 1 gives every "
    + "district its population share of what is sent, or its whole shortfall if that is less; in between, "
    + "every district first gets that fraction of its population share. Because the plan balances all supplies "
    + "at once, raising the floor can lower one supply for a district and raise another.");
}
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
  const need = shortfall(rows);
  const unit = resourceLabel(state.resource);
  el.append("p").attr("class", "summary").html(
    `<b>All districts</b>, ${esc(unit)}, next 4 weeks: need ${fmtNum(tot("demand"))} (high estimate), in stock `
    + `${fmtNum(tot("stock"))}, shortfall ${fmtNum(need)}; this plan sends <b>${fmtNum(tot("alloc"))}</b>`
    + (need > 0 ? ` and covers ${fmtNum(100 * (1 - tot("unmet") / need), 0)}% of the shortfall.` : "; nothing is short."),
  );
  // All supplies together: the average share of each supply's shortfall covered (the figure the report and the
  // policy brief quote), so a single supply is not mistaken for the whole plan.
  const covered = D.resources.map((r) => {
    const rr = D.getAlloc(sc.id, r);
    const n = shortfall(rr);
    return n > 0 ? 1 - d3.sum(rr, (x) => x.unmet) / n : null;
  }).filter((v) => v != null);
  if (covered.length) {
    el.append("p").attr("class", "summary").html(`<b>All supplies</b>: this plan covers `
      + `<b>${fmtNum(100 * d3.mean(covered), 0)}%</b> of the shortfall on average over the ${covered.length} supplies `
      + "that are short. It sends new supplies only; it does not move stock between districts.");
  }
  if (sel) {
    const short = Math.max(sel.demand - sel.stock, 0);
    const why = short === 0 && sel.alloc > 0.5 ? " Its stock already covers its need; it receives supplies only because of the minimum share."
      : short === 0 ? " Its stock already covers its need." : sel.alloc + 0.5 < short ? " The budget is not enough to cover every shortfall." : "";
    el.append("p").attr("class", "summary").html(
      `<b>${esc(sel.name)}</b>: needs ${fmtNum(sel.demand)}, has ${fmtNum(sel.stock)}, short by ${fmtNum(short)}; `
      + `receives <b>${fmtNum(sel.alloc)}</b>, still short by ${fmtNum(sel.unmet)}.${why}`);
  }

  const m = { top: 6, right: 14, bottom: 34, left: narrow ? 92 : 112 };
  const rowH = 20;
  const ih = shown.length * rowH;
  const iw = width - m.left - m.right;
  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${ih + m.top + m.bottom}`)
    .attr("role", "img").attr("aria-label", `Demand, stock and allocation of ${state.resource} by district`);
  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  // Scale on need and allocation; a stock far above need is cut at the edge and labelled, so one district's
  // large stock cannot flatten every other bar (persona walkthroughs).
  const xmax = 1.25 * (d3.max(shown, (r) => Math.max(r.demand, r.alloc)) || d3.max(shown, (r) => r.stock) || 1);
  const x = d3.scaleLinear().domain([0, xmax]).nice().range([0, iw]).clamp(true);
  const y = d3.scaleBand().domain(shown.map((r) => r.d)).range([0, ih]).padding(0.22);

  g.append("g").attr("class", "grid-line").call(d3.axisBottom(x).ticks(narrow ? 3 : 5).tickSize(ih).tickFormat(""));
  const row = g.selectAll("g.row").data(shown).join("g").attr("class", "row")
    .attr("transform", (r) => `translate(0,${y(r.d)})`).style("cursor", "pointer")
    .on("click", (event, r) => setState({ district: r.d }, "alloc"))
    .on("mousemove", (event, r) => showTip(
      `<b>${esc(r.name)}</b><br>Need, next 4 weeks (high estimate): ${fmtNum(r.demand)}<br>In stock: ${fmtNum(r.stock)}`
      + `<br>This plan sends: ${fmtNum(r.alloc)}<br>Still short after it: ${fmtNum(r.unmet)}`, event))
    .on("mouseleave", hideTip);
  row.append("rect").attr("width", iw).attr("height", y.bandwidth()).attr("fill", "transparent");
  row.append("rect").attr("width", (r) => x(r.stock)).attr("height", y.bandwidth()).attr("fill", C.stock);
  row.filter((r) => r.stock > x.domain()[1]).append("text").attr("x", iw - 2).attr("y", y.bandwidth() / 2 + 4)
    .attr("text-anchor", "end").attr("font-size", 10).attr("fill", "#fff").text((r) => `stock ${fmtNum(r.stock)} ▸`);
  row.append("rect").attr("x", (r) => x(r.stock)).attr("width", (r) => x(r.stock + r.alloc) - x(r.stock)).attr("height", y.bandwidth()).attr("fill", C.alloc);
  row.append("line").attr("x1", (r) => x(r.demand)).attr("x2", (r) => x(r.demand)).attr("y1", -2).attr("y2", y.bandwidth() + 2)
    .attr("stroke", C.demand).attr("stroke-width", 2.2);
  row.filter((r) => r.d === state.district).append("rect").attr("width", iw).attr("height", y.bandwidth())
    .attr("fill", "none").attr("stroke", "#000").attr("stroke-width", 1.5);

  g.append("g").attr("class", "axis").call(d3.axisLeft(y).tickSize(0).tickFormat((d) => D.districtByCode.get(d)?.name || d))
    .select(".domain").remove();
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${ih})`).call(d3.axisBottom(x).ticks(narrow ? 3 : 5).tickFormat(d3.format("~s")));
  g.append("text").attr("class", "axis-label").attr("x", iw / 2).attr("y", ih + 29).attr("text-anchor", "middle")
    .text(`${resourceLabel(state.resource)} (top ${topN} areas by need; selected one outlined)`);

  const lg = d3.select("#alloc-legend");
  lg.selectAll("*").remove();
  legendKey(lg, "rect", "in stock", { color: C.stock });
  legendKey(lg, "rect", "sent by this plan", { color: C.alloc });
  legendKey(lg, "line", "need, next 4 weeks (high estimate)", { color: C.demand, width: 2.5 });

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
      const rows = s ? D.getAlloc(s.id, state.resource) : [];
      const need = shortfall(rows);
      return { a, unmet: s && need > 0 ? d3.sum(rows, (r) => r.unmet) / need : null };
    }),
  }));
  const m = { top: 22, right: 64, bottom: 32, left: 50 };
  const h = 150;
  const iw = width - m.left - m.right;
  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${h + m.top + m.bottom}`)
    .attr("role", "img").attr("aria-label", "Unmet demand against equity floor alpha, one line per budget");
  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  svg.append("text").attr("x", 0).attr("y", 13).attr("font-size", 12).attr("font-weight", 600)
    .text(`Cost of a minimum share: ${resourceLabel(state.resource)} still short`);
  const x = d3.scaleLinear().domain(d3.extent(D.alphas)).range([0, iw]);
  const y = d3.scaleLinear().domain([0, 1]).range([h, 0]);
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(5));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(4).tickFormat(d3.format(".0%")));
  g.append("text").attr("class", "axis-label").attr("x", iw / 2).attr("y", h + 28).attr("text-anchor", "middle").text("minimum share for every district (α)");
  g.append("text").attr("class", "axis-label").attr("transform", "rotate(-90)").attr("x", -h / 2).attr("y", -40)
    .attr("text-anchor", "middle").text("share of shortfall left");
  const line = d3.line().defined((p) => p.unmet != null).x((p) => x(p.a)).y((p) => y(p.unmet));
  for (const s of series) {
    const cur = s.b === sc.budget;
    g.append("path").datum(s.pts).attr("fill", "none").attr("stroke", cur ? C.forecast : "#b8c1cc")
      .attr("stroke-width", cur ? 2.4 : 1.2).attr("d", line);
    const last = s.pts[s.pts.length - 1];
    if (cur && last.unmet != null) {
      g.append("text").attr("x", iw + 4).attr("y", y(last.unmet) + 4).attr("font-size", 10)
        .attr("fill", C.forecast).text("this budget");
    }
  }
  const curPt = series.find((s) => s.b === sc.budget)?.pts.find((p) => p.a === sc.alpha);
  if (curPt?.unmet != null) {
    g.append("circle").attr("cx", x(curPt.a)).attr("cy", y(curPt.unmet)).attr("r", 5).attr("fill", C.forecast).attr("stroke", "#fff")
      .on("mousemove", (event) => showTip(`${budgetText(D, sc.budget)}, minimum share ${fmtNum(sc.alpha, 1)}`
        + `<br>Shortfall left: ${fmtNum(100 * curPt.unmet, 0)}%`, event))
      .on("mouseleave", hideTip);
  }
}
