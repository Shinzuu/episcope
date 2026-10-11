// View 2: weekly time series for the selected district and disease, with focus + context brush.
// Observed counts, latest forecast (mean + q10-q90 band), alert threshold and alert weeks.
// Implements VIS-2 (view 2), VIS-3 (brush sets the shared time range), VIS-6 (uncertainty band always drawn,
// suppressed weeks hatched).
/* global d3 */
import { state, setState } from "../state.js";
import { isAlert } from "../data.js";
import { addHatch, showTip, hideTip, legendKey, fmtDate, fmtCount, fmtNum, esc, C } from "../util.js";

const THRESHOLD_METHOD = "ears_c2"; // count-scale threshold (mean + 3 sd), drawn on the case axis

export function renderTimeseries(D, defaultRange) {
  const el = d3.select("#ts");
  el.selectAll("*").remove();
  const { district, disease: dz, method, thrMult, t0, t1 } = state;
  const dist = D.districtByCode.get(district);
  document.getElementById("ts-title").textContent = `Weekly ${dz} cases: ${dist ? dist.name : district}`;

  const width = el.node().clientWidth || 600;
  const narrow = width < 520;
  const m = { top: 10, right: 12, bottom: 34, left: 46 };
  const fh = narrow ? 190 : 230;
  const ch = 46;
  const gap = 34;
  const height = m.top + fh + gap + ch + 20;
  const iw = width - m.left - m.right;

  const s = D.getSeries(district, dz);
  const al = D.getAlerts(district, dz, method);
  const thrSeries = D.methods.includes(THRESHOLD_METHOD) ? D.getAlerts(district, dz, THRESHOLD_METHOD) : null;
  const pts = D.weeks.map((w, i) => ({
    w, i, c: s[i]?.c ?? null, s: !!s[i]?.s, missing: !s[i],
    a: al[i], alert: isAlert(al[i], thrMult),
    thr: thrSeries?.[i] ? thrSeries[i].thr * thrMult : null,
  }));
  const fc = D.getForecast(district, dz);
  const lastObs = [...pts].reverse().find((p) => p.c != null);
  const fcPts = fc ? [
    ...(lastObs && +lastObs.w === +fc.originDate ? [{ t: fc.originDate, mean: lastObs.c, q10: lastObs.c, q90: lastObs.c, anchor: true }] : []),
    ...fc.rows.map((r) => ({ t: r.targetDate, mean: r.mean, q10: r.q10, q90: r.q90, h: r.h })),
  ] : [];

  const lastWeek = D.weeks[D.weeks.length - 1];
  const fullEnd = fcPts.length ? d3.max(fcPts, (p) => p.t) : lastWeek;
  const halfWeek = 3.5 * 864e5;
  const domainFor = (a, b) => [new Date(+a - halfWeek), new Date(+(+b >= +lastWeek ? fullEnd : b) + halfWeek)];

  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img").attr("aria-label", `Weekly ${dz} cases with forecast for ${dist?.name}`);
  const hatch = addHatch(svg, "hatch-ts");
  svg.append("defs").append("clipPath").attr("id", "ts-clip").append("rect").attr("width", iw).attr("height", fh);

  const x = d3.scaleUtc().range([0, iw]);
  const y = d3.scaleLinear().range([fh, 0]);
  const focus = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  const gx = focus.append("g").attr("class", "axis").attr("transform", `translate(0,${fh})`);
  const gy = focus.append("g").attr("class", "axis");
  const gGrid = focus.append("g").attr("class", "grid-line");
  const plot = focus.append("g").attr("clip-path", "url(#ts-clip)");
  focus.append("text").attr("class", "axis-label").attr("transform", "rotate(-90)").attr("x", -fh / 2).attr("y", -36)
    .attr("text-anchor", "middle").text("cases per week");
  focus.append("text").attr("class", "axis-label").attr("x", iw / 2).attr("y", fh + 29).attr("text-anchor", "middle")
    .text("week starting");

  function drawFocus(a, b) {
    x.domain(domainFor(a, b));
    const [x0, x1] = x.domain();
    const vis = pts.filter((p) => p.w >= x0 && p.w <= x1);
    const visF = fcPts.filter((p) => p.t >= x0 && p.t <= x1);
    const ymax = d3.max([D.threshold, d3.max(vis, (p) => p.c), d3.max(vis, (p) => p.thr), d3.max(visF, (p) => p.q90)]);
    y.domain([0, ymax * 1.08]).nice();
    gx.call(d3.axisBottom(x).ticks(narrow ? 4 : 7).tickSizeOuter(0));
    gy.call(d3.axisLeft(y).ticks(5).tickFormat(d3.format("~s")));
    gGrid.call(d3.axisLeft(y).ticks(5).tickSize(-iw).tickFormat(""));
    plot.selectAll("*").remove();
    const bw = Math.max(1, x(new Date(+x0 + 7 * 864e5)) - x(x0));

    plot.selectAll("rect.supp").data(vis.filter((p) => p.s)).join("rect").attr("class", "supp")
      .attr("x", (p) => x(p.w) - bw / 2).attr("width", bw).attr("y", y(D.threshold)).attr("height", y(0) - y(D.threshold))
      .attr("fill", hatch);
    if (visF.length) {
      plot.append("path").datum(fcPts).attr("fill", C.forecast).attr("opacity", 0.22)
        .attr("d", d3.area().x((p) => x(p.t)).y0((p) => y(p.q10)).y1((p) => y(p.q90)));
      plot.append("path").datum(fcPts).attr("fill", "none").attr("stroke", C.forecast).attr("stroke-width", 2)
        .attr("stroke-dasharray", "5 3").attr("d", d3.line().x((p) => x(p.t)).y((p) => y(p.mean)));
    }
    if (thrSeries) {
      plot.append("path").datum(vis).attr("fill", "none").attr("stroke", C.alert).attr("stroke-width", 1.4)
        .attr("stroke-dasharray", "2 3")
        .attr("d", d3.line().defined((p) => p.thr != null).curve(d3.curveStepAfter).x((p) => x(p.w) - bw / 2).y((p) => y(p.thr)));
    }
    plot.append("path").datum(vis).attr("fill", "none").attr("stroke", C.observed).attr("stroke-width", 1.6)
      .attr("d", d3.line().defined((p) => p.c != null).x((p) => x(p.w)).y((p) => y(p.c)));
    if (bw > 5) {
      plot.selectAll("circle.obs").data(vis.filter((p) => p.c != null)).join("circle").attr("class", "obs")
        .attr("cx", (p) => x(p.w)).attr("cy", (p) => y(p.c)).attr("r", 2).attr("fill", C.observed);
    }
    plot.selectAll("path.alert").data(vis.filter((p) => p.alert)).join("path").attr("class", "alert-marker alert")
      .attr("transform", (p) => `translate(${x(p.w)},${y(p.c ?? D.threshold) - 9})`)
      .attr("d", d3.symbol(d3.symbolTriangle, 60)());

    // Hover readout.
    const all = [...vis.map((p) => p.w), ...visF.filter((p) => !p.anchor).map((p) => p.t)];
    plot.append("rect").attr("width", iw).attr("height", fh).attr("fill", "transparent")
      .on("mousemove", (event) => {
        const t = x.invert(d3.pointer(event)[0]);
        const w = all[d3.minIndex(all, (d) => Math.abs(d - t))];
        if (!w) return;
        const p = pts.find((q) => +q.w === +w);
        const f = fcPts.find((q) => +q.t === +w && !q.anchor);
        let html = `<b>Week of ${fmtDate(w)}</b>`;
        if (p) {
          html += `<br>Observed: ${p.s ? "suppressed (<5)" : p.missing ? "no report" : fmtNum(p.c)}`;
          if (p.thr != null) html += `<br>Threshold (${THRESHOLD_METHOD}): ${fmtNum(p.thr, 1)}`;
          if (p.a) html += `<br>${esc(method)}: stat ${p.a.stat == null ? "hidden" : fmtNum(p.a.stat, 2)} / thr ${fmtNum(p.a.thr * thrMult, 2)}${p.alert ? " <b>ALERT</b>" : ""}`;
        }
        if (f) html += `<br>Forecast h${f.h}: ${fmtCount(f.mean, D.threshold)} (80% PI ${fmtCount(f.q10, D.threshold)}–${fmtCount(f.q90, D.threshold)})`;
        showTip(html, event);
      })
      .on("mouseleave", hideTip);
  }
  drawFocus(t0, t1);

  // Context strip with brush.
  const ctxTop = m.top + fh + gap;
  const xc = d3.scaleUtc().domain([new Date(+D.weeks[0] - halfWeek), new Date(+fullEnd + halfWeek)]).range([0, iw]);
  const yc = d3.scaleLinear().domain([0, d3.max(pts, (p) => p.c) || 1]).range([ch, 0]);
  const ctx = svg.append("g").attr("transform", `translate(${m.left},${ctxTop})`);
  ctx.append("path").datum(pts).attr("fill", "#c9d3df")
    .attr("d", d3.area().defined((p) => p.c != null).x((p) => xc(p.w)).y0(ch).y1((p) => yc(p.c)));
  ctx.selectAll("rect.ca").data(pts.filter((p) => p.alert)).join("rect").attr("class", "ca")
    .attr("x", (p) => xc(p.w) - 1).attr("width", 2).attr("y", ch - 6).attr("height", 6).attr("fill", C.alert);
  ctx.append("g").attr("class", "axis").attr("transform", `translate(0,${ch})`)
    .call(d3.axisBottom(xc).ticks(narrow ? 4 : 8).tickSizeOuter(0));

  const snap = ([a, b]) => {
    const da = xc.invert(a); const db = xc.invert(b);
    const inside = D.weeks.filter((w) => w >= new Date(+da - halfWeek) && w <= new Date(+db + halfWeek));
    return inside.length ? [inside[0], inside[inside.length - 1]] : null;
  };
  const brush = d3.brushX().extent([[0, 0], [iw, ch]])
    .on("brush", (event) => {
      if (!event.sourceEvent || !event.selection) return;
      const r = snap(event.selection);
      if (r) drawFocus(r[0], r[1]);
    })
    .on("end", (event) => {
      if (!event.sourceEvent) return;
      const r = event.selection ? snap(event.selection) : null;
      const [a, b] = r || defaultRange;
      setState({ t0: a, t1: b }, "ts");
    });
  const gb = ctx.append("g").attr("class", "brush").call(brush);
  gb.call(brush.move, [xc(new Date(+t0 - halfWeek)), xc(new Date(+t1 + halfWeek))]);
  gb.selectAll(".selection").attr("fill", C.forecast).attr("fill-opacity", 0.18).attr("stroke", C.forecast);

  const lg = d3.select("#ts-legend");
  lg.selectAll("*").remove();
  legendKey(lg, "line", "observed", { color: C.observed });
  legendKey(lg, "band", fc ? `forecast mean, 80% PI (q10–q90), origin ${fmtDate(fc.originDate)}` : "no forecast", { color: C.forecast });
  if (thrSeries) legendKey(lg, "line", `alert threshold (${THRESHOLD_METHOD}${thrMult !== 1 ? ` × ${thrMult}` : ""})`, { color: C.alert, dash: "2 3", width: 1.6 });
  legendKey(lg, "symbol", `${method} alert`, { color: C.alert });
  legendKey(lg, "hatch", "suppressed (<5)");
}
