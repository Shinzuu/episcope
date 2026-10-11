// View 5: ranked early-warning table for the selected disease over the selected time range.
// Rank = alert weeks in range, then peak stat/threshold ratio. Shows the latest forecast with its 80% interval.
// Implements VIS-2 (view 5), VIS-3 (row click selects district; selected row highlighted), VIS-6 (uncertainty
// shown as an interval, suppressed values hatched).
/* global d3 */
import { state, setState } from "../state.js";
import { isAlert } from "../data.js";
import { districtStats } from "./map.js";
import { fmtNum, fmtCount, fmtShort, inRange, esc } from "../util.js";

export function rankDistricts(D) {
  const { disease: dz, t0, t1, method, thrMult } = state;
  const stats = districtStats(D, dz, t0, t1, method, thrMult);
  const lastIn = D.weeks.reduce((acc, w, i) => (inRange(w, t0, t1) ? i : acc), -1);
  const rows = D.districts.map((d) => {
    const al = D.getAlerts(d.code, dz, method);
    let peak = 0;
    D.weeks.forEach((w, i) => {
      const a = al[i];
      if (inRange(w, t0, t1) && a && a.stat != null && a.thr > 0) peak = Math.max(peak, a.stat / (a.thr * thrMult));
    });
    const latest = lastIn >= 0 ? D.getSeries(d.code, dz)[lastIn] : null;
    const fc = D.getForecast(d.code, dz);
    const st = stats.get(d.code);
    return { ...d, ...st, peak, latest, f1: fc?.rows[0] || null, alertNow: lastIn >= 0 && isAlert(al[lastIn], thrMult) };
  });
  rows.sort((a, b) => d3.descending(a.alerts, b.alerts) || d3.descending(a.peak, b.peak) || d3.ascending(a.name, b.name));
  return rows;
}

export function renderTable(D) {
  const rows = rankDistricts(D);
  const { t0, t1, method, disease: dz } = state;
  document.getElementById("table-sub").textContent =
    `${dz}, ${fmtShort(t0)} – ${fmtShort(t1)}, ${method}. Ranked by alert weeks, then peak statistic ÷ threshold.`;
  const thr = D.threshold;
  const maxPeak = d3.max(rows, (r) => r.peak) || 1;

  const wrap = d3.select("#table");
  wrap.selectAll("*").remove();
  const table = wrap.append("table");
  table.append("caption").attr("class", "sr-only").style("position", "absolute").style("left", "-9999px")
    .text(`Early warning ranking for ${dz}`);
  const head = ["#", "District", "Alert wks", "Peak ÷ thr", "Latest wk", "Mean /100k", "Next wk forecast"];
  const wide = [false, false, false, true, false, true, false];
  table.append("thead").append("tr").selectAll("th").data(head).join("th")
    .attr("scope", "col").attr("class", (_, i) => (wide[i] ? "col-wide" : null)).text((h) => h);
  const tr = table.append("tbody").selectAll("tr").data(rows).join("tr")
    .attr("class", (r) => (r.code === state.district ? "selected" : null))
    .attr("data-code", (r) => r.code)
    .attr("tabindex", 0)
    .attr("aria-selected", (r) => r.code === state.district)
    .on("click", (event, r) => setState({ district: r.code }, "table"))
    .on("keydown", (event, r) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setState({ district: r.code }, "table"); } });

  tr.append("td").text((r, i) => i + 1);
  tr.append("td").text((r) => r.name);
  tr.append("td").html((r) => `<span class="badge ${r.alerts ? "on" : "off"}">${r.alerts}</span>${r.alertNow ? " now" : ""}`);
  tr.append("td").attr("class", "col-wide")
    .html((r) => `${fmtNum(r.peak, 2)}<span class="score-bar" style="width:${Math.round(24 * r.peak / maxPeak)}px"></span>`);
  tr.append("td").attr("class", (r) => (r.latest?.s ? "supp" : null))
    .attr("title", (r) => (r.latest?.s ? "suppressed (<5)" : null))
    .text((r) => (!r.latest ? "–" : r.latest.s ? `<${thr}` : fmtNum(r.latest.c)));
  tr.append("td").attr("class", (r) => `col-wide${r.n === 0 && r.supp ? " supp" : ""}`)
    .text((r) => (r.mean == null ? (r.supp ? "supp." : "–") : fmtNum(r.mean, 2)));
  tr.append("td").html((r) => (r.f1
    ? `${esc(fmtCount(r.f1.mean, thr))} <br><span class="pi">80% PI ${esc(fmtCount(r.f1.q10, thr))}–${esc(fmtCount(r.f1.q90, thr))}</span>`
    : "–"));

  // Keep the selected row visible.
  const selRow = wrap.select("tr.selected").node();
  if (selRow) {
    const box = wrap.node();
    const top = selRow.offsetTop - box.clientHeight / 2;
    box.scrollTop = Math.max(0, top);
  }
}
