// Shared helpers: tooltip, hatch pattern, palettes, formatting. Implements VIS-3 (tooltips) and VIS-6
// (colour-blind-safe palettes, suppressed-cell hatching, freshness stamp).
/* global d3 */

export const C = {
  observed: "#1d232b",
  forecast: "#0072B2",   // Okabe-Ito blue
  alert: "#D55E00",      // Okabe-Ito vermillion
  stock: "#009E73",      // Okabe-Ito bluish green
  alloc: "#56B4E9",      // Okabe-Ito sky blue
  demand: "#1d232b",
  campaign: "#CC79A7",   // Okabe-Ito reddish purple
  preposition: "#E69F00",// Okabe-Ito orange
  nodata: "#e6e8ec",
  muted: "#5a6573",
};

// Reversed viridis: perceptually uniform, colour-blind safe, darker = more cases.
export const seqInterp = (t) => d3.interpolateViridis(1 - t);

export const fmtDate = d3.utcFormat("%d %b %Y");
export const fmtShort = d3.utcFormat("%d %b %y");
export const isoWeek = (d) => +d3.utcFormat("%V")(d);
export const isoYear = (d) => +d3.utcFormat("%G")(d);
export const parseDay = d3.utcParse("%Y-%m-%d");
export const fmtNum = (v, digits = 0) => (v == null || Number.isNaN(v) ? "–" : d3.format(`,.${digits}f`)(v));
// Forecast quantities below the suppression threshold are shown as "<5" (research.md section 9, rule 5).
export const fmtCount = (v, thr) => (v == null ? "–" : v > 0 && v < thr ? `<${thr}` : fmtNum(v));

const tip = () => document.getElementById("tooltip");
export function showTip(html, event) {
  const t = tip();
  t.innerHTML = html;
  t.hidden = false;
  const pad = 14;
  const { innerWidth: W, innerHeight: H } = window;
  const r = t.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + r.width > W - 4) x = Math.max(4, event.clientX - r.width - pad);
  if (y + r.height > H - 4) y = Math.max(4, event.clientY - r.height - pad);
  t.style.left = `${x}px`;
  t.style.top = `${y}px`;
}
export function hideTip() { tip().hidden = true; }

// Diagonal hatch used for every suppressed (<5) cell, in every view.
export function addHatch(svg, id) {
  const p = svg.append("defs").append("pattern")
    .attr("id", id).attr("patternUnits", "userSpaceOnUse").attr("width", 5).attr("height", 5)
    .attr("patternTransform", "rotate(45)");
  p.append("rect").attr("width", 5).attr("height", 5).attr("fill", "#f1f2f4");
  p.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "#8a939e").attr("stroke-width", 1.6);
  return `url(#${id})`;
}

// Small inline legend swatches.
export function legendKey(container, kind, label, opts = {}) {
  const key = container.append("span").attr("class", "key");
  const s = key.append("svg").attr("width", 22).attr("height", 12).attr("aria-hidden", "true");
  if (kind === "hatch") {
    const fill = addHatch(s, `lg-hatch-${Math.random().toString(36).slice(2, 8)}`);
    s.append("rect").attr("width", 22).attr("height", 12).attr("fill", fill).attr("stroke", "#8a939e");
  } else if (kind === "line") {
    s.append("line").attr("x1", 1).attr("x2", 21).attr("y1", 6).attr("y2", 6)
      .attr("stroke", opts.color).attr("stroke-width", opts.width || 2).attr("stroke-dasharray", opts.dash || null);
  } else if (kind === "band") {
    s.append("rect").attr("y", 1).attr("width", 22).attr("height", 10).attr("fill", opts.color).attr("opacity", opts.opacity ?? 0.25);
    s.append("line").attr("x1", 0).attr("x2", 22).attr("y1", 6).attr("y2", 6).attr("stroke", opts.color).attr("stroke-dasharray", "4 2");
  } else if (kind === "symbol") {
    s.append("path").attr("transform", "translate(11,6)")
      .attr("d", d3.symbol(opts.symbol || d3.symbolTriangle, opts.size || 50)())
      .attr("fill", opts.color).attr("stroke", "#fff");
  } else {
    s.append("rect").attr("y", 1).attr("width", 22).attr("height", 10).attr("fill", opts.color).attr("stroke", opts.stroke || "none");
  }
  key.append("span").text(label);
  return key;
}

// Continuous colour ramp legend with an axis label.
export function rampLegend(container, scale, label, width = 220) {
  const svg = container.append("svg").attr("width", width).attr("height", 46).attr("role", "img").attr("aria-label", label);
  const id = `ramp-${Math.random().toString(36).slice(2, 8)}`;
  const g = svg.append("defs").append("linearGradient").attr("id", id);
  const [a, b] = scale.domain();
  d3.range(0, 1.01, 0.1).forEach((t) => g.append("stop").attr("offset", `${t * 100}%`).attr("stop-color", scale(a + t * (b - a))));
  svg.append("rect").attr("x", 12).attr("y", 4).attr("width", width - 24).attr("height", 10).attr("fill", `url(#${id})`);
  const x = d3.scaleLinear().domain([a, b]).range([12, width - 12]);
  svg.append("g").attr("class", "axis").attr("transform", "translate(0,14)").call(d3.axisBottom(x).ticks(4).tickSize(3));
  svg.append("text").attr("class", "axis-label").attr("x", width / 2).attr("y", 44).attr("text-anchor", "middle").text(label);
}

export const inRange = (date, t0, t1) => date >= t0 && date <= t1;
export const debounce = (fn, ms) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
