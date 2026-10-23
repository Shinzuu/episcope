// EpiScope dashboard entry point. Implements VIS-1 (D3 v7 dashboard), VIS-2 (five views), VIS-3 (one shared
// state + dispatch bus), VIS-5 (what-if panel), VIS-6 (freshness stamp on every view), VIS-7 (phone layout),
// PRV-3 (?district=<code> opens a district user's bundle).
/* global d3 */
import { loadAll } from "./data.js";
import { state, setState, bus } from "./state.js";
import { fmtShort, debounce, methodLabel } from "./util.js";
import { isAlert } from "./data.js";
import { renderMap } from "./views/map.js";
import { renderTimeseries } from "./views/timeseries.js";
import { renderHeatmap } from "./views/heatmap.js";
import { renderAllocation, buildWhatIf, defaultResource } from "./views/allocation.js";
import { renderTable, rankDistricts } from "./views/table.js";

const DEFAULT_WEEKS = 26;

function fillControls(D, defaultRange) {
  const btns = d3.select("#disease-buttons").selectAll("button").data(D.diseases).join("button")
    .attr("type", "button").attr("role", "radio").text((d) => d)
    .on("click", (event, d) => setState({ disease: d, resource: defaultResource(D, d) }, "controls"));
  const syncButtons = () => btns.attr("aria-checked", (d) => d === state.disease);

  const sel = d3.select("#district-select").on("change", function () { setState({ district: this.value }, "controls"); });
  sel.selectAll("option").data([...D.districts].sort((a, b) => d3.ascending(a.name, b.name))).join("option")
    .attr("value", (d) => d.code).text((d) => d.name);

  d3.select("#method-select").on("change", function () { setState({ method: this.value }, "controls"); })
    .selectAll("option").data(D.methods).join("option").attr("value", (m) => m).text((m) => methodLabel(m));

  d3.select("#range-reset").on("click", () => setState({ t0: defaultRange[0], t1: defaultRange[1] }, "controls"));

  return () => {
    syncButtons();
    sel.property("value", state.district);
    d3.select("#method-select").property("value", state.method);
    d3.select("#range-label").text(`${fmtShort(state.t0)} – ${fmtShort(state.t1)}`);
  };
}

// PRV-3: say which view this is, so a district user knows the other areas are aggregates.
function roleBanner(D) {
  const el = document.getElementById("role-banner");
  if (!D.role) return;
  const own = D.districtByCode.get(D.role.district)?.name || D.role.district;
  el.textContent = `District view: ${own} in detail; other areas as division totals only.`;
  el.hidden = false;
}

// "Who is on alert now?" across every disease, for the last complete week (persona walkthroughs: the map and
// table count alerts over the whole range). Follows the alert method and level the user picked.
function nowSummary(D) {
  const el = document.getElementById("now-summary");
  const i = D.lastComplete;
  const parts = D.diseases.map((dz) => {
    const on = D.districts.filter((d) => isAlert(D.getAlerts(d.code, dz, state.method)[i], state.thrMult));
    const names = on.slice(0, 3).map((d) => d.name).join(", ") + (on.length > 3 ? ` and ${on.length - 3} more` : "");
    return on.length ? `${dz}: ${names}` : null;
  }).filter(Boolean);
  el.textContent = `On alert in the last full week (week of ${fmtShort(D.weeks[i])}, ${methodLabel(state.method)}): `
    + (parts.length ? parts.join("; ") : "none") + ".";
}

function freshness(D) {
  const gen = new Date(D.meta.generated_at);
  const genTxt = Number.isNaN(+gen) ? D.meta.generated_at : d3.utcFormat("%d %b %Y %H:%M UTC")(gen);
  const txt = `Data through ${D.meta.data_through} · exported ${genTxt}`;
  document.querySelectorAll("[data-fresh]").forEach((p) => { p.textContent = txt; });
}

async function init() {
  let D;
  try {
    D = await loadAll();
  } catch (e) {
    document.querySelector("main").innerHTML = "";
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = `Could not load dashboard data (${e.message}). Run "make serve" and open via "make dashboard".`;
    document.querySelector("main").append(p);
    throw e;
  }
  window.__episcope = { D, state };
  const n = D.weeks.length;
  const defaultRange = [D.weeks[Math.max(0, n - DEFAULT_WEEKS)], D.weeks[n - 1]];
  const disease = D.diseases.includes("dengue") ? "dengue" : D.diseases[0];
  Object.assign(state, {
    disease,
    method: D.methods.includes("ears_c2") ? "ears_c2" : D.methods[0],  // the recommended detector (report §6)
    t0: defaultRange[0],
    t1: defaultRange[1],
    // Open on half of the money needed and the recommended floor, the scenario the report and brief discuss.
    budget: D.bRef ? D.budgets[d3.minIndex(D.budgets, (b) => Math.abs(b - 0.5 * D.bRef))]
      : D.budgets[Math.floor(D.budgets.length / 2)],
    alpha: D.alphas.includes(D.recommendedAlpha) ? D.recommendedAlpha
      : D.alphas[Math.min(D.alphas.length - 1, Math.round(D.alphas.length * 0.2))],
    resource: defaultResource(D, disease),
  });
  state.district = D.role?.district || rankDistricts(D)[0]?.code || D.districts[0].code;

  const syncControls = fillControls(D, defaultRange);
  buildWhatIf(D);
  freshness(D);
  roleBanner(D);
  nowSummary(D);

  const views = {
    map: () => renderMap(D),
    ts: () => renderTimeseries(D, defaultRange),
    heat: () => renderHeatmap(D),
    alloc: () => renderAllocation(D),
    table: () => renderTable(D),
  };
  // Which state keys each view depends on.
  const deps = {
    map: ["district", "disease", "t0", "t1", "method", "thrMult"],
    ts: ["district", "disease", "t0", "t1", "method", "thrMult"],
    heat: ["district", "disease", "t0", "t1"],
    alloc: ["district", "budget", "alpha", "resource"],
    table: ["district", "disease", "t0", "t1", "method", "thrMult"],
  };
  const renderAll = () => { syncControls(); Object.values(views).forEach((f) => f()); };

  bus.on("change.main", ({ changed }) => {
    syncControls();
    if (changed.includes("method") || changed.includes("thrMult")) nowSummary(D);
    for (const [name, keys] of Object.entries(deps)) {
      if (keys.some((k) => changed.includes(k))) views[name]();
    }
  });
  renderAll();

  let lastW = window.innerWidth;
  window.addEventListener("resize", debounce(() => {
    if (window.innerWidth === lastW) return; // ignore mobile URL-bar height changes
    lastW = window.innerWidth;
    renderAll();
  }, 150));
  document.body.dataset.ready = "1";
}

init();
