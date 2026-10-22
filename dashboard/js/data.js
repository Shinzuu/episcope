// Loads dashboard/data/*.json (docs/contracts.md "Serving") and builds the indexes every view reads.
// Implements VIS-4 (only aggregated, suppressed JSON reaches the browser). Nothing here assumes dates,
// district counts or file sizes: weeks, districts, methods, budgets and alphas all come from the data.
/* global d3, topojson */
import { parseDay } from "./util.js";
import { FALLBACK_DISTRICTS } from "./districts.js";

const TOPO = "data/bd_districts.topo.json";

// Probe for the TopoJSON without triggering a 404 console error: python's http.server lists directories.
async function topoAvailable() {
  try {
    const r = await fetch("data/", { cache: "no-store" });
    if (r.ok && (r.headers.get("content-type") || "").includes("text/html")) {
      return (await r.text()).includes("bd_districts.topo.json");
    }
  } catch (_) { /* fall through */ }
  return true; // unknown server: just try it
}

async function loadTopo() {
  // ?gridmap forces the placeholder tile map (used to test the fallback).
  if (new URLSearchParams(window.location.search).has("gridmap")) return null;
  if (!(await topoAvailable())) return null;
  try {
    const topo = await d3.json(TOPO);
    const obj = topo.objects.districts || Object.values(topo.objects)[0];
    const fc = topojson.feature(topo, obj);
    // Guard against counter-clockwise (RFC 7946) rings, which d3 would fill as the whole globe.
    for (const f of fc.features) {
      if (d3.geoArea(f) > 2 * Math.PI) rewind(f.geometry);
    }
    return { fc, mesh: topojson.mesh(topo, obj, (a, b) => a !== b) };
  } catch (e) {
    console.warn("TopoJSON unavailable, using grid map", e);
    return null;
  }
}

function rewind(g) {
  const rev = (rings) => rings.forEach((r) => r.reverse());
  if (g.type === "Polygon") rev(g.coordinates);
  else if (g.type === "MultiPolygon") g.coordinates.forEach(rev);
}

// PRV-3: ?district=<P-code> opens that district user's bundle (serving/roles.py): the own district in detail,
// every other division only as an aggregate. Without it the national bundle in data/ is used.
export function roleDistrict() {
  const d = new URLSearchParams(window.location.search).get("district");
  return d && /^[A-Za-z0-9]+$/.test(d) ? d : null;
}

export async function loadAll() {
  const names = ["meta", "weekly", "alerts", "forecasts", "allocation", "calendar", "dq"];
  const role = roleDistrict();
  const base = role ? `roles/${role}` : "data";
  const [meta, weekly, alerts, forecasts, allocation, calendar, dq] = await Promise.all(
    names.map((n) => d3.json(`${base}/${n}.json`)),
  );
  const geo = await loadTopo();
  return build({ meta, weekly, alerts, forecasts, allocation, calendar, dq, geo });
}

const key = (...parts) => parts.join("|");

// The detection's alarm gate (analytics/detection/config.py): no alarm unless the last 4 weeks hold at least 5
// cases. Recomputed here from the published counts only, so it reveals nothing about hidden ones; a hidden
// count is taken as unknown and adds nothing, so near-empty series stay quiet at every alert level.
const GATE_WEEKS = 4, GATE_MIN_CASES = 5;
function gateOpen(s, i) {
  let sum = 0;
  for (let j = Math.max(0, i - GATE_WEEKS + 1); j <= i; j += 1) sum += s?.[j]?.c ?? 0;
  return sum >= GATE_MIN_CASES;
}

function build(raw) {
  const { meta, weekly, alerts, forecasts, allocation, calendar, dq, geo } = raw;
  const threshold = meta.suppression_threshold ?? 5;

  const weekStrs = Array.from(new Set(weekly.map((r) => r.w))).sort();
  const weeks = weekStrs.map(parseDay);
  const wIdx = new Map(weekStrs.map((w, i) => [w, i]));
  const diseases = meta.diseases?.length ? meta.diseases : Array.from(new Set(weekly.map((r) => r.dz)));

  const series = new Map();
  for (const r of weekly) {
    const k = key(r.d, r.dz);
    if (!series.has(k)) series.set(k, new Array(weeks.length).fill(null));
    series.get(k)[wIdx.get(r.w)] = { c: r.s ? null : r.c, s: !!r.s, inc: r.s ? null : r.inc };
  }

  const methods = Array.from(new Set(alerts.map((a) => a.method)));
  const alertIdx = new Map();
  for (const a of alerts) {
    const i = wIdx.get(a.w);
    if (i === undefined) continue;
    const k = key(a.d, a.dz, a.method);
    if (!alertIdx.has(k)) alertIdx.set(k, new Array(weeks.length).fill(null));
    alertIdx.get(k)[i] = { stat: a.stat, thr: a.thr, alert: !!a.alert, gate: gateOpen(series.get(key(a.d, a.dz)), i) };
  }

  // Latest forecast origin per district x disease.
  const fc = new Map();
  for (const f of forecasts) {
    const k = key(f.d, f.dz);
    const cur = fc.get(k);
    if (!cur || f.origin > cur.origin) fc.set(k, { origin: f.origin, rows: [f] });
    else if (f.origin === cur.origin) cur.rows.push(f);
  }
  for (const v of fc.values()) {
    v.rows.sort((a, b) => a.h - b.h);
    v.rows.forEach((r) => { r.targetDate = parseDay(r.target); });
    v.originDate = parseDay(v.origin);
  }

  const scenarios = allocation.scenarios;
  const budgets = Array.from(new Set(scenarios.map((s) => s.budget))).sort((a, b) => a - b);
  const alphas = Array.from(new Set(scenarios.map((s) => s.alpha))).sort((a, b) => a - b);
  const resources = Array.from(new Set(allocation.rows.map((r) => r.r)));
  const allocIdx = new Map();
  for (const r of allocation.rows) {
    const k = key(r.id, r.r);
    if (!allocIdx.has(k)) allocIdx.set(k, []);
    allocIdx.get(k).push(r);
  }

  const cal = new Map();
  for (const c of calendar) {
    const k = key(c.div, c.dz);
    if (!cal.has(k)) cal.set(k, []);
    cal.get(k).push(c);
  }

  // Districts: codes from the data; names from the TopoJSON, else the built-in fallback list.
  const nameOf = new Map(FALLBACK_DISTRICTS.map((d) => [d.code, d.name]));
  if (geo) for (const f of geo.fc.features) {
    if (f.properties?.district_name) nameOf.set(f.properties.district_code, f.properties.district_name);
  }
  const codes = Array.from(new Set(weekly.map((r) => r.d))).sort();
  // Division = first four characters of the P-code (BD + 2-digit division), per research.md section 1.3.
  const aggNames = meta.role?.aggregates || {};
  const districts = codes.map((code) => ({ code, name: aggNames[code] || nameOf.get(code) || code, div: code.slice(0, 4),
    aggregate: code in aggNames }));

  // A week is complete once all seven days are inside the data (week_start + 6 <= data_through). The newest
  // week is usually partial: its low count must not be read as a fall (persona walkthroughs).
  const through = parseDay(meta.data_through);
  const complete = (w) => !through || +w + 6 * 864e5 <= +through;
  let lastComplete = weeks.length - 1;
  while (lastComplete > 0 && !complete(weeks[lastComplete])) lastComplete -= 1;

  return {
    meta, role: meta.role || null, threshold, weeks, lastComplete, complete,
    bRef: allocation.b_ref ?? null, recommendedAlpha: allocation.recommended_alpha ?? null, weekStrs, wIdx, diseases, series, methods, alertIdx, fc,
    scenarios, budgets, alphas, resources, allocIdx, cal, dq, geo, districts,
    districtByCode: new Map(districts.map((d) => [d.code, d])),
    getSeries: (d, dz) => series.get(key(d, dz)) || new Array(weeks.length).fill(null),
    getAlerts: (d, dz, m) => alertIdx.get(key(d, dz, m)) || new Array(weeks.length).fill(null),
    getForecast: (d, dz) => fc.get(key(d, dz)) || null,
    getAlloc: (id, r) => allocIdx.get(key(id, r)) || [],
    getCalendar: (div, dz) => cal.get(key(div, dz)) || [],
  };
}

// An alert under the current threshold multiplier. At 1x the exported flag is used as-is; at other levels the
// alarm gate (gateOpen) still applies. For CUSUM this approximates, because the exported statistic resets at
// the default h.
export function isAlert(a, mult) {
  if (!a || a.stat == null) return false;
  return mult === 1 ? a.alert : a.gate && a.stat > a.thr * mult;
}
