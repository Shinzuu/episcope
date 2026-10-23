// Single shared selection state plus a d3.dispatch bus (VIS-3 coordinated views, research.md section 13).
// Every view subscribes to "change" and re-renders from state; no view talks to another directly.
/* global d3 */

export const state = {
  district: null,   // P-code
  disease: null,
  t0: null,         // Date, first week_start in range
  t1: null,         // Date, last week_start in range
  method: null,     // alert method
  thrMult: 1,       // detection threshold multiplier (VIS-5)
  budget: null,     // scenario budget (VIS-5)
  alpha: null,      // equity floor (VIS-5)
  resource: null,
};

export const bus = d3.dispatch("change");

export function setState(patch, source = "") {
  const changed = Object.keys(patch).filter((k) => state[k] !== patch[k]);
  if (!changed.length) return;
  Object.assign(state, patch);
  bus.call("change", null, { changed, source });
}
