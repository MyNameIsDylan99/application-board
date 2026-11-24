// Browser test: node tests/ui_smoke.mjs <port> <chromium>
// Starts a headless Chromium with a temporary profile, opens every view in both languages and checks that no
// script error happens, that the CSP holds, that HTML from a job search file is not executed and that the
// new dialogs open. Exit code 1 on any failure.
import {spawn} from "node:child_process";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

const [port, chromium] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const profile = mkdtempSync(join(tmpdir(), "bb-chrome-"));
const debugPort = 9400 + Math.floor(Math.random() * 400);
const browser = spawn(chromium, ["--headless=new", `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, "--no-first-run",
  ...(process.env.CI ? ["--no-sandbox"] : []), "about:blank"], {stdio: "ignore"});  // CI runners (Ubuntu 24.04) block the sandbox
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failed = 0;
const check = (name, ok, detail = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : ": " + detail)); if (!ok) failed++; };

try {
  let tab;
  for (let i = 0; i < 120 && !tab; i++) { await sleep(250); tab = await fetch(`http://127.0.0.1:${debugPort}/json/new?${base}/`, {method: "PUT"}).then(r => r.json()).catch(() => null); }
  const ws = new WebSocket(tab.webSocketDebuggerUrl); let id = 0; const pend = {}; const errs = [];
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d.result); delete pend[d.id]; }
    if (d.method === "Runtime.exceptionThrown") errs.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    // Some Chrome versions report the inline handler of the deliberate XSS probe below while DOMPurify parses it
    // (and block it); the probe itself checks that nothing ran, so a report from inside DOMPurify is not an error.
    const fromPurify = d.params?.entry?.stackTrace?.callFrames?.[0]?.url?.endsWith("/vendor/purify.min.js");
    if (d.method === "Log.entryAdded" && /Content Security Policy|Refused/.test(d.params.entry.text) && !fromPurify) errs.push("CSP: " + d.params.entry.text.slice(0, 90) +
      ` [${d.params.entry.url || ""}:${d.params.entry.lineNumber ?? ""} ${(d.params.entry.stackTrace?.callFrames || []).slice(0, 4).map(f => `${f.functionName}@${f.url.split("/").pop()}:${f.lineNumber}`).join(" < ")}]`); };
  await new Promise(r => ws.onopen = r);
  const send = (method, params = {}) => new Promise(r => { pend[++id] = r; ws.send(JSON.stringify({id, method, params})); });
  const ev = async e => { const r = await send("Runtime.evaluate", {expression: e, awaitPromise: true, returnByValue: true}); return r.exceptionDetails ? "EXC " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text) : r.result.value; };
  await send("Runtime.enable"); await send("Log.enable");
  for (const lang of ["de", "en"]) {
    await ev(`fetch("/api/settings", {method: "POST", headers: {"X-Bewerben": "1", "Content-Type": "application/json"}, body: JSON.stringify({lang: "${lang}"})})`);
    await send("Page.reload"); await sleep(500);
    for (let i = 0; i < 40 && !(await ev(`typeof items !== "undefined" && items.length > 0`)); i++) await sleep(250);
    for (const v of ["letters", "pipeline", "jobs", "profile", "knowledge", "settings"]) { await ev(`setView("${v}")`); await sleep(700); }
    await ev(`setView("jobs")`); await sleep(1200);
    check(`${lang}: job cards rendered`, await ev(`document.querySelectorAll("#jcards .card").length`) > 0);
    for (const p of ["details", "documents", "text", "templates"]) { await ev(`setView("profile"); profileSection("${p}")`); await sleep(500); }
    await ev(`setView("pipeline")`); await sleep(400);
    check(`${lang}: next view is the start`, await ev(`pview === "next" && !document.querySelector("#pnext").hidden && document.querySelector("#pnext").innerHTML.length > 0`));
    for (const pv of ["board", "agenda", "stats", "next"]) { await ev(`pview = "${pv}"; renderPipe()`); await sleep(200); }
    await ev(`setView("letters")`); await sleep(300);
    await ev(`openNewApp()`); check(`${lang}: new application dialog`, await ev(`document.querySelector("#newapp").classList.contains("on")`)); await ev(`document.querySelector("#newapp").classList.remove("on")`);
    await ev(`open(items[0].path).then(openVersions)`); await sleep(600);
    check(`${lang}: versions dialog`, await ev(`document.querySelector("#versions").classList.contains("on")`)); await ev(`document.querySelector("#versions").classList.remove("on")`);
    check(`${lang}: spell check on`, await ev(`ed.getInputField().getAttribute("spellcheck")`) === "true");
    check(`${lang}: rule check chips`, await ev(`document.querySelectorAll("#rules .chip").length`) > 0);
    check(`${lang}: requirement chips from the mapping`, await ev(`[...document.querySelectorAll("#reqs .chip")].some(c => c.textContent.includes("C#"))`), await ev(`document.querySelector("#reqs").textContent`));
    check(`${lang}: suggestions fold and unfold`, await ev(`(() => { const tg = () => document.querySelector("#reqs .rqtoggle"); tg().click();
      const hidden = getComputedStyle(document.querySelector("#reqs .chip")).display === "none"; tg().click();
      return hidden && getComputedStyle(document.querySelector("#reqs .chip")).display !== "none"; })()`));
  }
  check("markdown is sanitized", await ev(`md('<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>ok')`) === '<p><img src="x">ok</p>\n');
  // Vim must still work with the contenteditable input (needed for the spell check)
  const key = async (k, code, text) => { await send("Input.dispatchKeyEvent", {type: "keyDown", key: k, code, text, windowsVirtualKeyCode: k.length === 1 ? k.toUpperCase().charCodeAt(0) : 27});
    await send("Input.dispatchKeyEvent", {type: "keyUp", key: k, code, windowsVirtualKeyCode: k.length === 1 ? k.toUpperCase().charCodeAt(0) : 27}); };
  await ev(`setView("letters"); S.vim = true; ed.setOption("keyMap", "vim"); ed.setOption("readOnly", false); ed.focus(); ed.setCursor(0, 0)`); await sleep(200);
  await key("i", "KeyI", "i"); await key("Z", "KeyZ", "Z"); await key("Escape", "Escape"); await sleep(200);
  check("vim insert works in the editor", await ev(`ed.getLine(0).startsWith("Z") && !ed.state.vim.insertMode`), await ev(`ed.getLine(0).slice(0, 30)`));
  await ev(`ed.undo(); dirtyEd = false`);
  check("HTML from a job search file is not executed", await ev(`window.__xss === undefined`) === true);
  check("no script errors or CSP violations", errs.length === 0, errs.join(" | "));
  ws.close();
} catch (e) {
  check("browser test ran", false, e.stack);
} finally {
  browser.kill();
  await sleep(300);
  rmSync(profile, {recursive: true, force: true});
}
process.exit(failed ? 1 : 0);
