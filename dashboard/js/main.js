// EpiScope dashboard entry point. Implements VIS-1 (D3 v7 dashboard), VIS-2 (five views), VIS-3 (one shared
// state + dispatch bus), VIS-5 (what-if panel), VIS-6 (freshness stamp on every view), VIS-7 (phone layout).
/* global d3 */
import { loadAll } from "./data.js";
import { state, setState, bus } from "./state.js";
import { fmtShort, debounce } from "./util.js";
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
    .selectAll("option").data(D.methods).join("option").attr("value", (m) => m).text((m) => m.replace("_", " ").toUpperCase());

  d3.select("#range-reset").on("click", () => setState({ t0: defaultRange[0], t1: defaultRange[1] }, "controls"));

  return () => {
    syncButtons();
    sel.property("value", state.district);
    d3.select("#method-select").property("value", state.method);
    d3.select("#range-label").text(`${fmtShort(state.t0)} – ${fmtShort(state.t1)}`);
  };
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
    method: D.methods[0],
    t0: defaultRange[0],
    t1: defaultRange[1],
    budget: D.budgets[Math.floor(D.budgets.length / 2)],
    alpha: D.alphas[Math.min(D.alphas.length - 1, Math.round(D.alphas.length * 0.2))],
    resource: defaultResource(D, disease),
  });
  state.district = rankDistricts(D)[0]?.code || D.districts[0].code;

  const syncControls = fillControls(D, defaultRange);
  buildWhatIf(D);
  freshness(D);

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
