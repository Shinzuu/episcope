// View 1: choropleth of mean weekly incidence per 100k over the selected range, with alert markers.
// Implements VIS-1, VIS-2 (view 1), VIS-3 (click selects district), VIS-6 (viridis, hatched suppression).
// Uses data/bd_districts.topo.json when present (equal-area conic, research.md section 13), else a tile grid.
/* global d3 */
import { state, setState } from "../state.js";
import { isAlert } from "../data.js";
import { addHatch, seqInterp, showTip, hideTip, rampLegend, legendKey, fmtNum, inRange, esc, C } from "../util.js";
import { gridLayout } from "../districts.js";

let zoomTransform = d3.zoomIdentity;

export function districtStats(D, dz, t0, t1, method, mult) {
  const out = new Map();
  for (const d of D.districts) {
    const s = D.getSeries(d.code, dz);
    const al = D.getAlerts(d.code, dz, method);
    let sum = 0; let n = 0; let supp = 0; let cases = 0; let alerts = 0; let lastAlert = null;
    D.weeks.forEach((w, i) => {
      if (!inRange(w, t0, t1)) return;
      const c = s[i];
      if (c) {
        if (c.s) supp += 1;
        else if (c.inc != null) { sum += c.inc; n += 1; cases += c.c; }
      }
      if (isAlert(al[i], mult)) { alerts += 1; lastAlert = w; }
    });
    out.set(d.code, { mean: n ? sum / n : null, n, supp, cases, alerts, lastAlert });
  }
  return out;
}

export function renderMap(D) {
  const el = d3.select("#map");
  el.selectAll("*").remove();
  const width = el.node().clientWidth || 400;
  const height = Math.round(width * (D.geo ? 1.18 : 1.05));
  const { disease: dz, t0, t1, method, thrMult } = state;
  const stats = districtStats(D, dz, t0, t1, method, thrMult);

  const vals = Array.from(stats.values()).map((s) => s.mean).filter((v) => v != null);
  const hi = d3.quantile(vals.sort(d3.ascending), 0.98) || 1;
  const color = d3.scaleSequential(seqInterp).domain([0, hi]).clamp(true);

  const svg = el.append("svg").attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img").attr("aria-label", `Map of ${dz} incidence per 100k by district`);
  const hatch = addHatch(svg, "hatch-map");
  const g = svg.append("g");

  const fillFor = (code) => {
    const s = stats.get(code);
    if (!s || (s.n === 0 && s.supp === 0)) return C.nodata;
    if (s.n === 0) return hatch;
    return color(s.mean);
  };
  const tipHtml = (code) => {
    const d = D.districtByCode.get(code);
    const s = stats.get(code);
    if (!d || !s) return esc(code);
    const meanTxt = s.n ? `${fmtNum(s.mean, 2)} per 100k / week` : "suppressed (<5) in every week";
    return `<b>${esc(d.name)}</b><br>${meanTxt}`
      + `<br>${fmtNum(s.cases)} visible cases in range`
      + (s.supp ? `<br>${s.supp} suppressed week(s) (<5 each) not in the mean` : "")
      + `<br>${s.alerts} ${esc(method)} alert week(s)`;
  };
  const select = (code) => setState({ district: code }, "map");

  const centroids = new Map();
  if (D.geo) {
    const proj = d3.geoConicEqualArea().parallels([21.5, 25.5]).rotate([-90.3, 0])
      .fitExtent([[8, 8], [width - 8, height - 8]], D.geo.fc);
    const path = d3.geoPath(proj);
    g.selectAll("path.district").data(D.geo.fc.features).join("path")
      .attr("class", (f) => `district${f.properties.district_code === state.district ? " selected" : ""}`)
      .attr("data-code", (f) => f.properties.district_code)
      .attr("d", path)
      .attr("fill", (f) => fillFor(f.properties.district_code));
    D.geo.fc.features.forEach((f) => centroids.set(f.properties.district_code, path.centroid(f)));
  } else {
    const layout = gridLayout(D.districts.map((d) => d.code));
    const cols = d3.extent(Array.from(layout.values()), (p) => p.col);
    const rows = d3.extent(Array.from(layout.values()), (p) => p.row);
    const cell = Math.min((width - 16) / (cols[1] - cols[0] + 1), (height - 16) / (rows[1] - rows[0] + 1));
    const ox = (width - cell * (cols[1] - cols[0] + 1)) / 2;
    const oy = (height - cell * (rows[1] - rows[0] + 1)) / 2;
    const tiles = g.selectAll("g.tile").data(D.districts).join("g").attr("class", "tile")
      .attr("transform", (d) => {
        const p = layout.get(d.code);
        return `translate(${ox + (p.col - cols[0]) * cell},${oy + (p.row - rows[0]) * cell})`;
      });
    tiles.append("rect").attr("class", (d) => `district${d.code === state.district ? " selected" : ""}`)
      .attr("data-code", (d) => d.code)
      .attr("width", cell - 2).attr("height", cell - 2).attr("rx", 3)
      .attr("fill", (d) => fillFor(d.code));
    tiles.append("text").attr("x", (cell - 2) / 2).attr("y", (cell - 2) / 2 + 3).attr("text-anchor", "middle")
      .attr("font-size", Math.max(7, cell * 0.22)).attr("pointer-events", "none")
      .attr("fill", (d) => ((stats.get(d.code)?.mean ?? 0) > hi * 0.45 ? "#fff" : "#1d232b"))
      .text((d) => d.name.slice(0, 4));
    D.districts.forEach((d) => {
      const p = layout.get(d.code);
      centroids.set(d.code, [ox + (p.col - cols[0]) * cell + cell * 0.8, oy + (p.row - rows[0]) * cell + cell * 0.2]);
    });
    svg.append("text").attr("x", 8).attr("y", height - 6).attr("class", "axis-label")
      .text("Placeholder tile map (TopoJSON not found)");
  }

  g.selectAll(".district")
    .attr("tabindex", 0).attr("role", "button")
    .attr("aria-label", function () { const c = this.getAttribute("data-code"); return D.districtByCode.get(c)?.name || c; })
    .on("mousemove", function (event) { showTip(tipHtml(this.getAttribute("data-code")), event); })
    .on("mouseleave", hideTip)
    .on("click", function () { select(this.getAttribute("data-code")); })
    .on("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(this.getAttribute("data-code")); } });
  g.selectAll(".district.selected").raise();
  if (D.geo) g.append("path").datum(D.geo.mesh).attr("fill", "none").attr("stroke", "#fff").attr("stroke-width", 0.4).attr("pointer-events", "none");
  g.selectAll(".district.selected").raise();

  // Alert markers: districts with at least one alert week in range.
  const flagged = D.districts.filter((d) => stats.get(d.code)?.alerts > 0);
  g.selectAll("path.alert-marker").data(flagged).join("path").attr("class", "alert-marker")
    .attr("transform", (d) => `translate(${centroids.get(d.code)})`)
    .attr("d", (d) => d3.symbol(d3.symbolTriangle, 30 + 18 * Math.min(stats.get(d.code).alerts, 6))());

  const zoom = d3.zoom().scaleExtent([1, 8]).translateExtent([[0, 0], [width, height]])
    .filter((event) => {
      if (event.type === "dblclick") return false;
      if (event.type === "wheel") return event.ctrlKey || event.metaKey;
      if (event.type.startsWith("touch")) return event.touches.length > 1; // one finger scrolls the page
      return !event.button;
    })
    .on("zoom", (event) => { zoomTransform = event.transform; g.attr("transform", zoomTransform); });
  svg.call(zoom).call(zoom.transform, zoomTransform).style("touch-action", "pan-x pan-y");

  const sub = document.getElementById("map-sub");
  sub.textContent = `${dz}: mean weekly cases per 100k, ${D.weeks.filter((w) => inRange(w, t0, t1)).length} weeks in range. `
    + "Ctrl+scroll or pinch to zoom.";

  const lg = d3.select("#map-legend");
  lg.selectAll("*").remove();
  rampLegend(lg, color, "mean weekly cases per 100k");
  legendKey(lg, "hatch", "suppressed (<5)");
  legendKey(lg, "rect", "no data", { color: C.nodata, stroke: "#ccc" });
  legendKey(lg, "symbol", `${method} alert in range`, { color: C.alert });
}
