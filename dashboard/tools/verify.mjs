#!/usr/bin/env node
// Headless-Chrome check of the dashboard (VIS-1, VIS-3, VIS-7): serves dashboard/ on a free port, loads it at
// phone and laptop widths, saves full-page screenshots, fails on any console error or exception, and clicks a
// district on the map to confirm the other views follow the selection. Uses only Node built-ins (Node >= 22
// for the global WebSocket) and the Chrome DevTools Protocol. Chrome and the server are always killed on exit.
//
//   node dashboard/tools/verify.mjs [outDir]       (default outDir: $TMPDIR/dash)
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const DASH = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = process.argv[2] || join(tmpdir(), "dash");
const CHROME = process.env.CHROME || "google-chrome-stable";
const WIDTHS = [390, 1366];
const QUERY = process.env.QUERY || ""; // e.g. QUERY='?gridmap' to check the placeholder map
const TAG = QUERY ? `-${QUERY.replace(/\W+/g, "")}` : "";
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((res) => { const s = createServer(); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => res(port)); }); });

const procs = [];
const profile = mkdtempSync(join(tmpdir(), "episcope-chrome-"));
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
  const server = spawn("python3", ["-m", "http.server", String(port), "--bind", "127.0.0.1"], { cwd: DASH, stdio: "ignore" });
  procs.push(server);
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

  const report = { outDir: OUT, widths: {} };
  let ok = true;
  for (const width of WIDTHS) {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const s = (m, p) => cdp.send(m, p, sessionId);
    const errors = [];
    cdp.handlers.push((msg) => {
      if (msg.sessionId !== sessionId) return;
      if (msg.method === "Runtime.consoleAPICalled" && (msg.params.type === "error" || msg.params.type === "assert")) {
        errors.push(`console.${msg.params.type}: ${msg.params.args.map((a) => a.value ?? a.description).join(" ")}`);
      }
      if (msg.method === "Runtime.exceptionThrown") errors.push(`exception: ${msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text}`);
      if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") errors.push(`log: ${msg.params.entry.text} ${msg.params.entry.url || ""}`);
    });
    await s("Runtime.enable"); await s("Log.enable"); await s("Page.enable");
    const mobile = width < 600;
    await s("Emulation.setDeviceMetricsOverride", { width, height: mobile ? 844 : 900, deviceScaleFactor: 1, mobile });
    if (mobile) await s("Emulation.setTouchEmulationEnabled", { enabled: true });
    await s("Page.navigate", { url: `http://127.0.0.1:${port}/index.html${QUERY}` });
    const evalJs = async (expr) => (await s("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
    let ready = false;
    for (let i = 0; i < 100 && !ready; i += 1) { await sleep(150); ready = await evalJs("document.body && document.body.dataset.ready === '1'"); }
    if (!ready) errors.push("dashboard never signalled ready");
    await sleep(400);

    const shot = async (name) => {
      const h = await evalJs("Math.ceil(document.documentElement.scrollHeight)");
      await s("Emulation.setDeviceMetricsOverride", { width, height: h, deviceScaleFactor: 1, mobile });
      await sleep(300);
      const { data } = await s("Page.captureScreenshot", { format: "png" });
      const file = join(OUT, `${name}.png`);
      writeFileSync(file, Buffer.from(data, "base64"));
      return file;
    };
    const signature = `(() => ({
      state: window.__episcope?.state.district,
      ts: document.getElementById('ts-title').textContent,
      heat: document.getElementById('heat-title').textContent,
      tableSelected: document.querySelector('#table tr.selected')?.dataset.code || null,
      allocSelected: Array.from(document.querySelectorAll('#alloc g.row')).filter(g => g.querySelector('rect[stroke="#000"]')).length,
      select: document.getElementById('district-select').value,
      mapSelected: document.querySelector('#map .district.selected')?.getAttribute('data-code') || null,
    }))()`;

    const before = await evalJs(signature);
    const files = [await shot(`dashboard-${width}${TAG}`)];
    // Pick an unselected district whose bbox centre actually hits its own shape, then click it for real.
    const target = await evalJs(`(() => {
      const cur = window.__episcope.state.district;
      for (const el of document.querySelectorAll('#map .district')) {
        const code = el.getAttribute('data-code');
        if (code === cur) continue;
        const r = el.getBoundingClientRect();
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (r.width > 6 && document.elementFromPoint(x, y) === el) return { code, x, y };
      }
      return null;
    })()`);
    let after = null;
    if (!target) errors.push("no clickable district found on the map");
    else {
      for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
        await s("Input.dispatchMouseEvent", { type, x: target.x, y: target.y, button: "left", clickCount: 1 });
      }
      await sleep(500);
      after = await evalJs(signature);
      files.push(await shot(`dashboard-${width}${TAG}-after-map-click`));
    }
    const linked = !!after && after.state === target.code && after.select === target.code
      && after.tableSelected === target.code && after.mapSelected === target.code
      && after.ts !== before.ts && after.heat !== before.heat && after.allocSelected === 1;
    if (!linked) errors.push("map click did not update every linked view");
    report.widths[width] = { errors, before, clicked: target?.code, after, linked, files };
    if (errors.length) ok = false;
    await cdp.send("Target.closeTarget", { targetId });
  }
  await cdp.send("Browser.close").catch(() => {});
  report.ok = ok;
  console.log(JSON.stringify(report, null, 2));
  writeFileSync(join(OUT, `verify${TAG}.json`), JSON.stringify(report, null, 2));
  process.exitCode = ok ? 0 : 1;
}

main().catch((e) => { console.error(e); process.exitCode = 1; }).finally(() => { cleanup(); setTimeout(() => process.exit(), 100); });
