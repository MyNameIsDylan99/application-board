// Screenshots of a board running on the demo data (tests/demo.py): node tests/screenshots.mjs <port> <chromium> <out folder>
// Writes one PNG per view; docs/demo.gif is made from them with ImageMagick (see CONTRIBUTING.md).
import {spawn} from "node:child_process";
import {mkdtempSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

const [port, chromium, out] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const profile = mkdtempSync(join(tmpdir(), "bb-shot-"));
const debugPort = 9800 + Math.floor(Math.random() * 100);
const browser = spawn(chromium, ["--headless=new", `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, "--no-first-run",
  "--force-prefers-reduced-motion", "about:blank"], {stdio: "ignore"});
const sleep = ms => new Promise(r => setTimeout(r, ms));
try {
  let tab;
  for (let i = 0; i < 40 && !tab; i++) { await sleep(250); tab = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, {method: "PUT"}).then(r => r.json()).catch(() => null); }
  const ws = new WebSocket(tab.webSocketDebuggerUrl); let id = 0; const pend = {};
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d.result); delete pend[d.id]; } };
  await new Promise(r => ws.onopen = r);
  const send = (method, params = {}) => new Promise(r => { pend[++id] = r; ws.send(JSON.stringify({id, method, params})); });
  const ev = e => send("Runtime.evaluate", {expression: e, awaitPromise: true, returnByValue: true}).then(r => r.result?.value);
  const shot = async name => { await sleep(700); await ev(`document.querySelectorAll(".toast").forEach(t => t.remove())`); const r = await send("Page.captureScreenshot", {format: "png"}); writeFileSync(join(out, name + ".png"), Buffer.from(r.data, "base64")); console.log(name); };
  await send("Emulation.setDeviceMetricsOverride", {width: 1440, height: 860, deviceScaleFactor: 1, mobile: false});
  await send("Page.navigate", {url: base + "/"});
  for (let i = 0; i < 40 && !(await ev(`typeof items !== "undefined" && items.length > 0`)); i++) await sleep(250);
  await ev(`setSetting({lang: "de", theme: "light"})`); await sleep(400);
  await ev(`setView("letters"); open(items.find(i => i.company.startsWith("Pixelwerk")).path)`); await shot("letters");
  await ev(`openNewApp()`); await shot("new");
  await ev(`document.querySelector("#newapp").classList.remove("on"); setView("jobs"); openRun(jruns[0]?.name || "")`); await shot("jobsearch");
  await ev(`setView("pipeline"); pview = "next"; loadPipe()`); await shot("next");
  await ev(`pview = "board"; renderPipe(); openDossier(pitems.find(i => i.company.startsWith("Nordlicht")).path)`); await shot("pipeline");
  await ev(`closeDossier(); pview = "stats"; renderPipe()`); await shot("statistics");
  await ev(`setView("profile"); profileSection("details")`); await shot("profile");
  ws.close();
} finally {
  browser.kill(); await sleep(300); rmSync(profile, {recursive: true, force: true});
}
