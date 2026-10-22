#!/usr/bin/env node
// Scripted walkthrough of the dashboard in headless Chrome, used for the simulated persona walkthroughs
// (report §7). Serves dashboard/ on a free port, opens it once, runs a list of steps, and writes what a user
// would see after each step: a screenshot and the visible text of the parts asked for. Node built-ins and the
// Chrome DevTools Protocol only (as verify.mjs). Chrome and the server are always killed on exit.
//
//   node dashboard/tools/walk.mjs steps.json outDir
//
// steps.json: {"query": "?district=BD1004", "width": 1366, "steps": [
//   {"do": "select", "sel": "#district-select", "value": "BD1004"},
//   {"do": "click", "sel": "#disease-buttons button:nth-child(2)"},   // or {"do": "click", "text": "diarrhoea"}
//   {"do": "input", "sel": "#whatif input[type=range]", "value": "3"}, // sets the value, fires input + change
//   {"do": "hover", "sel": "#map .district[data-code=BD1004]"},
//   {"do": "text", "sel": "#table"},                                   // visible text, first 3000 characters
//   {"do": "shot", "name": "after-filter"},                            // full-page screenshot
//   {"do": "shot", "name": "table", "sel": "#table"},                  // one element only
//   {"do": "wait", "ms": 500}]}
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const DASH = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const [, , STEPS, OUT = join(tmpdir(), "walk")] = process.argv;
const CHROME = process.env.CHROME || "google-chrome-stable";
if (!STEPS) { console.error("usage: walk.mjs steps.json outDir"); process.exit(2); }
const plan = JSON.parse(readFileSync(STEPS, "utf8"));
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((res) => { const s = createServer(); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => res(port)); }); });
const procs = [];
const profile = mkdtempSync(join(tmpdir(), "episcope-walk-"));
function cleanup() {
  for (const p of procs) { try { p.kill("SIGKILL"); } catch (_) { /* gone */ } }
  try { rmSync(profile, { recursive: true, force: true }); } catch (_) { /* ignore */ }
}
process.on("exit", cleanup);
process.on("SIGINT", () => process.exit(130));

class CDP {
  constructor(url) { this.ws = new WebSocket(url); this.id = 0; this.pending = new Map(); this.handlers = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws.onopen = res; this.ws.onerror = rej;
      this.ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.id && this.pending.has(msg.id)) {
          const { res: ok, rej: bad } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? bad(new Error(JSON.stringify(msg.error))) : ok(msg.result);
        } else this.handlers.forEach((h) => h(msg));
      };
    });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params, sessionId }));
    return new Promise((res, rej) => this.pending.set(id, { res, rej }));
  }
}

async function main() {
  const port = await freePort();
  procs.push(spawn("python3", ["-m", "http.server", String(port), "--bind", "127.0.0.1"], { cwd: DASH, stdio: "ignore" }));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--disable-extensions", "--hide-scrollbars",
    "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  procs.push(chrome);
  const wsUrl = await new Promise((res, rej) => {
    let buf = "";
    chrome.stderr.on("data", (d) => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    setTimeout(() => rej(new Error("Chrome did not start")), 15000);
  });
  await sleep(300);
  const cdp = new CDP(wsUrl);
  await cdp.open();
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  const s = (m, p) => cdp.send(m, p, sessionId);
  const errors = [];
  cdp.handlers.push((msg) => {
    if (msg.sessionId !== sessionId) return;
    if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  });
  await s("Runtime.enable"); await s("Page.enable");
  const width = plan.width || 1366;
  const mobile = width < 600;
  await s("Emulation.setDeviceMetricsOverride", { width, height: mobile ? 844 : 900, deviceScaleFactor: 1, mobile });
  await s("Page.navigate", { url: `http://127.0.0.1:${port}/index.html${plan.query || ""}` });
  const evalJs = async (expr) => (await s("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
  let ready = false;
  for (let i = 0; i < 100 && !ready; i += 1) { await sleep(150); ready = await evalJs("document.body && document.body.dataset.ready === '1'"); }
  const log = [{ step: "open", ready, query: plan.query || "" }];
  const q = (sel) => JSON.stringify(sel);
  for (const [i, st] of (plan.steps || []).entries()) {
    const rec = { i, ...st };
    try {
      if (st.do === "click") {
        const pt = await evalJs(`(() => {
          const el = ${st.text ? `[...document.querySelectorAll("button,a,th,td,label,option,text,span")].find(e => e.textContent.trim() === ${q(st.text)})` : `document.querySelector(${q(st.sel)})`};
          if (!el) return null; el.scrollIntoView({block: "center"}); const r = el.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
        if (!pt) rec.error = "element not found";
        else for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
          await s("Input.dispatchMouseEvent", { type, x: pt.x, y: pt.y, button: "left", clickCount: 1 });
        }
      } else if (st.do === "select" || st.do === "input") {
        rec.ok = await evalJs(`(() => { const el = document.querySelector(${q(st.sel)}); if (!el) return false;
          el.value = ${q(String(st.value))}; el.dispatchEvent(new Event("input", {bubbles: true}));
          el.dispatchEvent(new Event("change", {bubbles: true})); return true; })()`);
        if (!rec.ok) rec.error = "element not found";
      } else if (st.do === "hover") {
        const pt = await evalJs(`(() => { const el = document.querySelector(${q(st.sel)}); if (!el) return null;
          el.scrollIntoView({block: "center"}); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
        if (!pt) rec.error = "element not found";
        else await s("Input.dispatchMouseEvent", { type: "mouseMoved", x: pt.x, y: pt.y });
        await sleep(200);
        rec.tooltip = await evalJs(`(document.querySelector(".tooltip, #tooltip, .tip")?.innerText || "").slice(0, 1000)`);
      } else if (st.do === "text") {
        rec.text = await evalJs(`(document.querySelector(${q(st.sel)})?.innerText || "(not found)").slice(0, 3000)`);
      } else if (st.do === "shot") {
        const h = await evalJs("Math.ceil(document.documentElement.scrollHeight)");
        await s("Emulation.setDeviceMetricsOverride", { width, height: Math.min(h, 4000), deviceScaleFactor: 1, mobile });
        await sleep(300);
        // With "sel", only that element is captured (page coordinates, so scrolling does not matter).
        const clip = st.sel ? await evalJs(`(() => { const el = document.querySelector(${q(st.sel)}); if (!el) return null;
          const r = el.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height, scale: 1 }; })()`) : null;
        if (st.sel && !clip) rec.error = "element not found";
        const { data } = await s("Page.captureScreenshot", clip ? { format: "png", clip, captureBeyondViewport: true } : { format: "png" });
        rec.file = join(OUT, `${st.name || `step-${i}`}.png`);
        writeFileSync(rec.file, Buffer.from(data, "base64"));
        await s("Emulation.setDeviceMetricsOverride", { width, height: mobile ? 844 : 900, deviceScaleFactor: 1, mobile });
      } else if (st.do === "wait") {
        await sleep(st.ms || 300);
      } else rec.error = `unknown step ${st.do}`;
    } catch (e) { rec.error = String(e.message || e); }
    await sleep(250);
    log.push(rec);
  }
  log.push({ step: "page errors", errors });
  await cdp.send("Browser.close").catch(() => {});
  writeFileSync(join(OUT, "walk.json"), JSON.stringify(log, null, 2));
  console.log(JSON.stringify(log, null, 2));
}

main().catch((e) => { console.error(e); process.exitCode = 1; }).finally(() => { cleanup(); setTimeout(() => process.exit(), 100); });
