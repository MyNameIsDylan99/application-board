const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const COLS = ["open", "final", "sent"];  // stored status values; "archived" is shown only on request
let showArchive = false;
try { showArchive = localStorage.getItem("rv-archive") === "1"; } catch {}
const cols = () => showArchive ? [...COLS, "archived"] : COLS;
const PANELS = ["list", "editor", "feedback", "preview"];
const DEFAULT_LAYOUT = {zones: {left: ["list"], center: ["editor"], right: ["preview"], bottom: ["feedback"]}, sizes: {left: 300, right: 420, bottom: 190}, hidden: ["preview"], max: null};
const ACCENTS = ["", "#2b5fd9", "#1f7a4a", "#b3261e", "#a35c00", "#6b3fa0", "#0e7c86"];
let S = {theme: "system", accent: "", lang: "de", vim: true, model: "", root: ""};  // settings from the server
let L = structuredClone(DEFAULT_LAYOUT);
let items = [], sel = 0, cur = null, mtime = null, dirtyEd = false, dirtyFb = false, loading = false, preview = false, pendingG = false, mode = "";
let filter = "", activePanel = "list", settingsOpen = false;
const t = k => (I18N[S.lang] || I18N.de)[k] ?? I18N.de[k] ?? k;
const locale = () => S.lang === "en" ? "en-GB" : "de-CH";
// all rendered markdown goes through DOMPurify: letters, mails, job postings and AI answers are not trusted HTML
const md = (text, opt) => DOMPurify.sanitize(marked.parse(String(text ?? ""), opt));
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const MD = {name: "markdown", highlightFormatting: true};
// contenteditable input: lets the browser's spell check work inside CodeMirror
const ed = CodeMirror($("#edwrap"), {keyMap: "vim", mode: MD, lineWrapping: true, lineNumbers: true, inputStyle: "contenteditable", spellcheck: true});
const fb = CodeMirror($("#fb"), {keyMap: "vim", mode: MD, lineWrapping: true, inputStyle: "contenteditable", spellcheck: true});

// Vim and the system clipboard: "+ and "* are the clipboard, every yank also lands there.
// The clipboard is only read on "+p/"*p: Vim pastes synchronously, so the p is held back briefly,
// the clipboard read and the p replayed. (Reading it on focus made Chromium show its "Paste" prompt
// on every click into the window and took the focus away.)
{
  const rc = CodeMirror.Vim.getRegisterController();
  let clip = "", linewise = false;
  const write = (s, lw) => { clip = s; linewise = !!lw; navigator.clipboard?.writeText(s).catch(() => {}); };
  const sync = () => (navigator.clipboard?.readText?.() ?? Promise.resolve(clip)).then(s => { if (s !== clip) { clip = s; linewise = s.endsWith("\n"); } }).catch(() => {});
  const reg = {
    get linewise() { return linewise; }, blockwise: false,
    setText: write, pushText: (s, lw) => write(clip + s, lw), clear() {}, toString: () => clip,
  };
  CodeMirror.Vim.defineRegister("+", reg); CodeMirror.Vim.defineRegister("*", reg);
  const push = rc.pushText.bind(rc);
  rc.pushText = (name, op, text, lw, bw) => {
    push(name, op, text, lw, bw);
    if (op === "yank" && name !== "_" && name !== "+" && name !== "*") write(lw && !text.endsWith("\n") ? text + "\n" : text, lw);
  };
  for (const cm of [ed, fb]) cm.on("keydown", (cm, e) => {
    const st = cm.state.vim;
    if (!st || st.insertMode || (e.key !== "p" && e.key !== "P") || !["+", "*"].includes(st.inputState?.registerName)) return;
    e.preventDefault();  // CodeMirror and Vim skip the key, the register stays selected
    sync().finally(() => CodeMirror.Vim.handleKey(cm, e.key, "user"));
  });
}

// ---------- Language ----------
function translate() {
  document.documentElement.lang = S.lang;
  for (const el of $$("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of $$("[data-i18n-label]")) el.label = t(el.dataset.i18nLabel);
  for (const el of $$("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
  for (const el of $$("[data-i18n-ph]")) el.placeholder = t(el.dataset.i18nPh);
  $("#bRaw").firstElementChild.textContent = live ? t("raw") : t("live");
  renderList(); header(); jobUi(); rules(); chips(); bar(); fillThemes();
  if (S.providers) renderProviders();
  if (jdata) { renderCards(); renderRuns(); }
  if (jcfg) renderConfig();
}

// ---------- Live view ----------
// mark the cursor line; measure again on a line change because shown and hidden characters change the widths
let live = true;
try { live = localStorage.getItem("rv-raw") !== "1"; } catch {}
for (const cm of [ed, fb]) {
  let active = null;
  const mark = () => {
    const h = cm.getLineHandle(cm.getCursor().line);
    if (h === active) return;
    try { if (active) cm.removeLineClass(active, "wrap", "live-active"); } catch {}
    cm.addLineClass(h, "wrap", "live-active");
    active = h;
    if (live) cm.refresh();
  };
  // always keep the cursor visible with some margin, like scrolloff in Vim
  // (after the refresh() above, so in the next tick, otherwise refresh restores the old scroll position)
  // the margin is at most a third of the height: 80 px do not fit into a small feedback panel,
  // CodeMirror then scrolled the cursor out of the visible area
  cm.on("cursorActivity", () => setTimeout(() => cm.scrollIntoView(null, Math.min(80, cm.getScrollInfo().clientHeight / 3))));
  cm.on("cursorActivity", mark);
  cm.on("swapDoc", () => { active = null; mark(); });
  cm.on("focus", () => live && cm.refresh());
  cm.on("blur", () => live && cm.refresh());
}
function setLive(v) {
  live = v;
  document.body.classList.toggle("live", v);
  try { localStorage.setItem("rv-raw", v ? "0" : "1"); } catch {}
  $("#bRaw").firstElementChild.textContent = v ? t("raw") : t("live");
  ed.refresh(); fb.refresh();
}
let rulesTimer = null;
for (const [cm, name] of [[ed, "mode_letter"], [fb, "mode_feedback"]]) {
  cm.on("change", () => {
    if (loading) return;
    cm === ed ? dirtyEd = true : dirtyFb = true; bar();
    if (cm === ed) { if (preview) renderPreview(); clearTimeout(rulesTimer); rulesTimer = setTimeout(rules, 300); }
  });
  cm.on("vim-mode-change", e => { mode = t(name) + " " + e.mode.toUpperCase(); bar(); });
  cm.on("focus", () => { mode = t(name) + " " + (S.vim ? "NORMAL" : t("mode_edit")); setActive(cm === ed ? "editor" : "feedback"); bar(); });
}

function msg(tx, err) { toast(tx, err); }
function toast(tx, err, act) {
  const box = $("#toasts"), d = document.createElement("div");
  d.className = "toast" + (err ? " err" : ""); d.setAttribute("role", err ? "alert" : "status");
  d.innerHTML = `<span></span><button title="${t("toast_close")}"><svg><use href="#i-close"/></svg></button>`;
  d.firstElementChild.textContent = tx;
  d.lastElementChild.onclick = () => d.remove();
  if (act) {  // e.g. accept a suggestion right away
    const b = document.createElement("button");
    b.className = "toast-act"; b.textContent = act.label;
    b.onclick = () => { d.remove(); act.fn(); };
    d.lastElementChild.before(b);
  }
  box.append(d);
  while (box.children.length > 6) box.firstElementChild.remove();
  if (!err && !act) setTimeout(() => d.remove(), 3500);
  return d;
}
function bar() {
  $("#mode").textContent = "-- " + mode + " --";
  $("#file").textContent = cur ? cur.path + (dirtyEd || dirtyFb ? " [+]" : "") : "";
}

async function api(url, opt = {}) {
  // X-Bewerben: the server only accepts writes with this header (protection against foreign web pages)
  const r = await fetch(url, {...opt, headers: {...opt.headers, "X-Bewerben": "1"}});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), {status: r.status});
  return j;
}
const postJson = (url, b) => api(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b)});
const post = b => postJson("/api/letter", b);

// ---------- Background jobs (feedback, finalize, preparation, follow-up, suggestions, learnings, drafts) ----------
// The server runs them in a thread; the page asks /api/job until the state is no longer "running".
const jobUrl = (kind, key) => `/api/job?kind=${kind}&key=${encodeURIComponent(key || "")}`;
const jobState = (kind, key) => api(jobUrl(kind, key)).catch(() => ({state: "idle"}));
const cancelJob = (kind, key) => postJson("/api/cancel", {kind, key}).catch(e => msg(t("error") + e.message, true));
async function waitJob(kind, key, every = 2000) {
  for (;;) {
    const j = await api(jobUrl(kind, key)).catch(() => ({state: "running"}));  // a network hiccup is not the end of the job
    if (j.state !== "running") return j.state === "idle" ? {state: "error", text: t("job_lost")} : j;  // idle: the server was restarted
    await new Promise(r => setTimeout(r, every));
  }
}
// a toast with "Cancel" while a job runs; returns the function that removes it
const runningToast = (text, kind, key) => { const d = toast(text, false, {label: t("cancel"), fn: () => cancelJob(kind, key)}); return () => d.remove(); };
const match = i => !filter || (i.company + " " + (i.title || i.file)).toLowerCase().includes(filter);
let sortScore = false;
try { sortScore = ["score", "wertung"].includes(localStorage.getItem("rv-sort")); } catch {}
const byScore = (a, b) => (b.score ?? 0) - (a.score ?? 0);  // without a score to the end, otherwise stable (company)
const ordered = () => cols().flatMap(k => { const g = items.filter(i => i.status === k && match(i)); if (sortScore) g.sort(byScore); return g.sort((a, b) => !!b.pinned - !!a.pinned); });
function setSort(score) {
  sortScore = score;
  try { localStorage.setItem("rv-sort", score ? "score" : "company"); } catch {}
  const c = cur; renderList(); if (c) { sel = ordered().indexOf(c); renderList(); }
}

async function refresh() {
  items = await api("/api/letters");
  if (cur) cur = items.find(i => i.path === cur.path) || cur;
  const o = ordered();
  if (cur && o.includes(cur)) sel = o.indexOf(cur);
  renderList(); header();
}

function itemChips(it) {
  const days = it.since ? Math.floor((Date.now() - new Date(it.since)) / 864e5) : 0;
  return (it.pinned ? `<i class="pin" title="${t("pinned")}">📌</i>` : "") + (it.score ? `<i class="score ${it.note || ""}" title="${t("j_score")}${it.note ? ", " + it.note : ""}">${it.score}/10</i>` : "") + (finalizing.has(it.path) ? `<i class="run">${t("finalizing")}</i>` : running.has(it.path) ? `<i class="run">${t("ai_short")}</i>` : it.feedback ? `<i class="fb">FB</i>` : "") + (it.pdf ? `<i class="pdf">PDF</i>` : "") +
    (days > 1 ? `<i>${days} ${t("days")}</i>` : "");
}
let boardMode = false;
try { boardMode = localStorage.getItem("rv-board") === "1"; } catch {}
function setListMode(board) {
  boardMode = board;
  try { localStorage.setItem("rv-board", board ? "1" : "0"); } catch {}
  for (const b of $$("#listSeg button")) b.classList.toggle("on", (b.dataset.lm === "board") === board);
  renderList();
}
function renderList() {
  const o = ordered(), el = $("#list"), bd = $("#board");
  $("#listSort").textContent = "↕ " + t(sortScore ? "sort_score" : "sort_company");
  el.innerHTML = ""; bd.innerHTML = "";
  $("#listEmpty").hidden = items.length > 0;
  el.hidden = boardMode || !items.length; bd.hidden = !boardMode || !items.length;
  for (const k of cols()) {
    const g = o.filter(i => i.status === k);
    const sec = document.createElement(boardMode ? "div" : "section");
    sec.className = boardMode ? "bcol" : ""; sec.dataset.status = k;
    sec.innerHTML = `<h2>${t("col_" + k)}<span>${g.length}</span></h2>`;
    for (const it of g) {
      const idx = o.indexOf(it), d = document.createElement("div");
      d.className = (boardMode ? "bcard" : "item") + (idx === sel ? " sel" : "") + (it.status === "archived" ? " archived" : "");
      d.innerHTML = `<b>${esc(it.company)}</b><small>${esc(it.title || it.file)}</small><div class="chips">${itemChips(it)}</div>`;
      d.onclick = () => go(idx);
      if (boardMode) {
        d.draggable = true;
        d.ondragstart = e => { e.dataTransfer.setData("text/plain", it.path); e.dataTransfer.effectAllowed = "move"; d.classList.add("dragging"); };
        d.ondragend = () => d.classList.remove("dragging");
      }
      sec.append(d);
    }
    if (boardMode) {
      sec.ondragover = e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; sec.classList.add("over"); };
      sec.ondragleave = () => sec.classList.remove("over");
      sec.ondrop = e => { e.preventDefault(); sec.classList.remove("over"); const it = items.find(i => i.path === e.dataTransfer.getData("text/plain")); if (!it) return; (k === "final" && it.status === "open" ? finalizeLetter(it) : setStatusFor(it, k)).catch(err => msg(t("error") + err.message, true)); };
    }
    (boardMode ? bd : el).append(sec);
  }
  (boardMode ? bd : el).querySelector(".sel")?.scrollIntoView({block: "nearest"});
}
async function setStatusFor(it, s) {
  if (it.status === s) return;
  if (cur?.path === it.path) return setStatus(s);
  await post({path: it.path, status: s});
  msg(`${it.company}: ${t("st_" + s)}`);
  await refresh();
}

// result of "Is the posting still open?" (postings.json, shared with the job search); used by the letters menu and the dossier
function checkLabel(c) { return c?.status === "gone" ? t("pc_gone") : c?.status === "open" ? t("pc_open") : t("pc_btn"); }
function checkClass(c) { return c?.status === "gone" ? "pc-gone" : c?.status === "open" ? "pc-open" : ""; }
function checkTitle(c) {
  return c?.checked ? `${t("pc_checked")} ${c.checked.split("-").reverse().join(".")}${c.reason ? ", " + c.reason : ""}. ${t("pc_again")}` : t("pc_title");
}
function header() {
  const s = cur?.status;
  $("#title").innerHTML = cur ? `${esc(cur.company)}<small>${esc(cur.title || cur.file)}</small>` : `${t("none")}<small></small>`;
  let pill = cur ? t("st_" + s) : "";
  if (cur?.since) {
    const days = Math.floor((Date.now() - new Date(cur.since)) / 864e5);
    pill += ` ${t("since")} ${cur.since}` + (days > 1 ? ` (${days} ${t("days")})` : "");
  }
  $("#pill").textContent = pill;
  const link = /^https?:\/\//.test(cur?.link || "") ? cur.link : "";
  $("#bLink").hidden = !link; $("#bLink").href = link || "#";
  $("#bCheckM").hidden = !link;
  $("#bCheckM").firstElementChild.textContent = checkLabel(cur?.check); $("#bCheckM").className = checkClass(cur?.check); $("#bCheckM").title = checkTitle(cur?.check);
  $("#bFinal").hidden = s !== "open";
  $("#bSent").hidden = s !== "final";
  const f = cur?.files, showFiles = !!f && s !== "open";
  // real links instead of window.open: no popup blocker catches them
  const href = rel => rel ? "/file?path=" + encodeURIComponent(rel) + "&v=" + encodeURIComponent(cur.finalized || "") : "#";
  $("#bCL").hidden = !showFiles || !f.cl_pdf; $("#bCV").hidden = !showFiles || !f.cv_pdf;
  $("#bCL").href = href(f?.cl_pdf); $("#bCV").href = href(f?.cv_pdf);
  const hints = (showFiles && cur.hints) || [];
  $("#bWarn").hidden = !hints.length;
  $("#bWarn").textContent = `${hints.length} ${t(hints.length === 1 ? "fin_hint" : "fin_hints")}`; $("#bWarn").title = hints.join("\n");
  $("#bRefin").disabled = !cur;
  $("#bBack").hidden = !cur || s === "open" || s === "archived";
  $("#bPinM").hidden = !cur; $("#bPinM").firstElementChild.textContent = t(cur?.pinned ? "unpin" : "pin");
  $("#bArchiveM").hidden = !cur; $("#bArchiveM").firstElementChild.textContent = t(s === "archived" ? "unarchive" : "archive");
  $("#bArchive").classList.toggle("on", showArchive);
  $("#bBack").firstElementChild.textContent = s === "sent" ? t("back_final") : t("back_offen");
  $("#bPrev").classList.toggle("on", preview);
  $("#edEmpty").hidden = !!cur;
}

async function open(path, force) {
  if (!force && cur?.path === path) return;
  if (!force && (dirtyEd || dirtyFb)) await save();
  const d = await api("/api/letter?path=" + encodeURIComponent(path));
  savePos();
  cur = items.find(i => i.path === path);
  loading = true;
  ed.setValue(d.content); ed.clearHistory();
  fb.setValue(d.feedback); fb.clearHistory();
  loading = false;
  restorePos(path);
  mtime = d.mtime; dirtyEd = dirtyFb = false; answer = d.answer || "";
  ed.getWrapperElement().lang = cur?.lang || S.lang;  // spell check in the language of the letter
  if (!running.has(path)) jobState("feedback", path).then(j => {
    if (j.state === "running") { running.add(path); jobUi(); poll(path); }
    else if (j.state === "done" && j.before != null && !lastJob[path]) { lastJob[path] = j; jobUi(); }
  }).catch(() => {});
  if (!finalizing.has(path)) jobState("finalize", path).then(j => { if (j.state === "running") { finalizing.add(path); jobUi(); pollFin(path); } }).catch(() => {});
  jobUi();
  header(); bar(); rules(); if (preview) renderPreview();
}

async function save() {
  if (!cur) return;
  const b = {path: cur.path};
  if (dirtyEd) { b.content = ed.getValue(); b.mtime = mtime; }
  if (dirtyFb) b.feedback = fb.getValue();
  if (!("content" in b) && !("feedback" in b)) { msg(t("nothing")); return; }
  try {
    const r = await post(b);
    if (r.mtime) mtime = r.mtime;
    dirtyEd = dirtyFb = false;
    msg(t("saved") + " " + new Date().toLocaleTimeString(locale()));
    await refresh(); bar();
  } catch (e) {
    msg(e.status === 409 ? t("conflict") : t("error") + e.message, true);
    throw e;
  }
}

async function setStatus(s) {
  if (!cur || cur.status === s) return;
  if (dirtyEd || dirtyFb) await save();
  await post({path: cur.path, status: s});
  msg(`${cur.company}: ${t("st_" + s)}`);
  await refresh();
}

function go(i) {
  const o = ordered();
  if (!o.length) return;
  sel = Math.max(0, Math.min(o.length - 1, i));
  renderList();
  open(o[sel].path).catch(() => {});
}
function step(d) { go(sel + d); }
// next visible panel in a direction, measured by the real position on screen (the layout can be rearranged freely)
function panelJump(dir) {
  const box = el => el.getBoundingClientRect();
  const panels = $$("#ws .panel").filter(p => !p.hidden && p.offsetParent && box(p).width > 0);
  const from = panels.find(p => p.dataset.panel === activePanel) || panels[0];
  if (!from) return;
  const a = box(from), ax = (a.left + a.right) / 2, ay = (a.top + a.bottom) / 2;
  let best = null, bestD = Infinity;
  for (const p of panels) {
    if (p === from) continue;
    const b = box(p), bx = (b.left + b.right) / 2, by = (b.top + b.bottom) / 2;
    const ok = {left: b.right <= a.left + 4, right: b.left >= a.right - 4, up: b.bottom <= a.top + 4, down: b.top >= a.bottom - 4}[dir];
    if (!ok) continue;
    const cross = dir === "left" || dir === "right" ? Math.abs(by - ay) : Math.abs(bx - ax);
    const d = Math.hypot(bx - ax, by - ay) + cross * 2;  // prefer panels in the same row or column
    if (d < bestD) { bestD = d; best = p; }
  }
  if (!best) return;
  const id = best.dataset.panel;
  ({list: focusList, editor: focusEd, feedback: focusFb}[id] || (() => { $("#pv").focus(); setActive("preview"); }))();
}
function focusList() { if (view !== "letters") setView("letters"); ensureVisible("list"); ed.getInputField().blur(); fb.getInputField().blur(); $("#list").focus(); setActive("list"); mode = t("mode_list"); bar(); }
function focusEd() { if (!cur) return; if (view !== "letters") setView("letters"); ensureVisible("editor"); ed.focus(); }
function focusFb() { if (!cur) return; if (view !== "letters") setView("letters"); ensureVisible("feedback"); fb.focus(); }
function setActive(id) { activePanel = id; for (const p of $$(".panel")) p.classList.toggle("focus", p.dataset.panel === id); }

const LETTER_HEAD = /^#\s*(Bewerbungsschreiben|Cover Letter)\s*$/;  // German or English letter heading
function letterStart(text) { return text.split("\n").findIndex(l => LETTER_HEAD.test(l)); }
function renderPreview() {
  const tx = ed.getValue(), i = letterStart(tx);
  $("#pv article").innerHTML = md(i >= 0 ? tx.split("\n").slice(i).join("\n") : tx, {breaks: true});
}

// ---------- Rule check: word count, dashes, ß, date ----------
const MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
function rules() {
  const bar = $("#rules"); bar.innerHTML = "";
  renderReqs();
  if (!cur) return;
  const chip = (text, cls, pos, title) => {
    const c = document.createElement("span");
    c.className = "chip " + cls; c.textContent = text; if (title) c.title = title;
    if (pos) c.onclick = () => { focusEd(); ed.setCursor(pos); };
    bar.append(c);
  };
  const all = ed.getValue().split("\n"), start = letterStart(ed.getValue());
  if (start < 0) { chip(t("no_letter"), "warn", {line: 0, ch: 0}); return; }
  const lines = all.slice(start + 1), text = lines.join("\n");
  const words = text.split(/\s+/).filter(w => /[\p{L}\p{N}]/u.test(w)).length;
  // word range and forbidden characters come from the profile (rule_check in the profile); 0 means no limit
  const rc = S.rulecheck || {}, lo = +rc.words_min || 0, hi = +rc.words_max || 0;
  const target = lo && hi ? `${lo}–${hi}` : lo ? `≥ ${lo}` : hi ? `≤ ${hi}` : "";
  chip(`${words} ${t("words")}`, (lo && words < lo) || (hi && words > hi) ? "warn" : "ok", {line: start + 1, ch: 0}, target && t("words_target") + " " + target);
  const chars = (rc.chars || []).filter(Boolean);
  if (chars.length) {
    const re = new RegExp(chars.map(c => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
    let n = 0;
    lines.forEach((l, i) => {
      for (const m of l.matchAll(re)) if (n++ < 12) chip(`„${m[0].trim() || m[0]}“ ${start + 2 + i}`, "warn", {line: start + 1 + i, ch: m.index}, t("char_forbidden"));
    });
  }
  const d = new Date(), dd = String(d.getDate()).padStart(2, "0"), mm = String(d.getMonth() + 1).padStart(2, "0"), y = d.getFullYear();
  const forms = [`${d.getDate()}. ${MONTHS_DE[d.getMonth()]} ${y}`, `${d.getDate()} ${MONTHS_EN[d.getMonth()]} ${y}`, `${MONTHS_EN[d.getMonth()]} ${d.getDate()}, ${y}`, `${dd}.${mm}.${y}`, `${y}-${mm}-${dd}`];
  if (forms.some(f => text.includes(f))) chip(t("date_ok"), "ok");
  else {
    const di = lines.findIndex(l => /\b(19|20)\d\d\b/.test(l) && /\d{1,2}[.\s]/.test(l));
    chip(t("date_missing"), "warn", {line: start + 1 + Math.max(di, 0), ch: 0}, forms[0]);
  }
}

// ---------- Requirements of the posting as one-click feedback ----------
// From "# Requirements & Skills Mapping": each **requirement** line and the evidence below it; evidence that names a gap marks it.
const GAP = /l(ü|ue)cke|\bgap\b|fehlt|keine (praktische |eigene )?erfahrung|nicht vorhanden|missing|no experience|not yet|noch nicht/i;
function requirements(text) {
  const m = text.match(/^# Requirements & Skills Mapping[ \t]*\n([\s\S]*?)(?=^# |$(?![\s\S]))/m);
  if (!m) return [];
  const out = [];
  for (const line of m[1].split("\n")) {
    const r = line.match(/^\s*(?:[-*]\s*)?\*\*(.+?)\*\*\s*:?\s*$/);
    if (r) out.push({name: r[1].replace(/:$/, ""), ev: ""});
    else if (out.length) out[out.length - 1].ev += line + "\n";
  }
  return out.map(x => ({...x, gap: GAP.test(x.ev)}));
}
function addFeedback(line) {
  const old = fb.getValue();
  fb.setValue((old.trim() ? old.trimEnd() + "\n" : "") + "- " + line); dirtyFb = true; bar();
  fb.focus(); fb.setCursor(fb.lastLine(), 1e9); msg(t("rq_added"));
}
let reqsFolded = false;
try { reqsFolded = localStorage.getItem("rv-reqs-folded") === "1"; } catch {}
function renderReqs() {
  const el = $("#reqs"); el.innerHTML = "";
  el.classList.toggle("folded", reqsFolded);
  if (!cur) return;
  const chip = (label, cls, title, line) => {
    const c = document.createElement("span");
    c.className = "chip " + cls; c.textContent = label; c.title = title; c.onclick = () => addFeedback(line);
    el.append(c);
  };
  const short = s => s.length > 38 ? s.slice(0, 36) + "…" : s;
  for (const r of requirements(ed.getValue()).slice(0, 14))
    r.gap ? chip("△ " + short(r.name), "gap", t("rq_gap_title"), t("rq_gap_fb").replace("{r}", r.name))
          : chip("✓ " + short(r.name), "", t("rq_ok_title"), t("rq_ok_fb").replace("{r}", r.name));
  if (!el.children.length) return;
  for (const k of ["shorter", "concrete"]) chip(t("rq_" + k), "act", t("rq_quick_title"), t("rq_" + k + "_fb"));
  const n = el.children.length, tg = document.createElement("button");  // fold the suggestions down to this one button (remembered)
  tg.className = "rqtoggle"; tg.textContent = `${reqsFolded ? "▸" : "▾"} ${t("rq_suggestions")} (${n})`; tg.title = t("p_fold");
  tg.onclick = () => { reqsFolded = !reqsFolded; try { localStorage.setItem("rv-reqs-folded", reqsFolded ? "1" : "0"); } catch {} renderReqs(); };
  el.prepend(tg);
}

// ---------- Vim ex commands ----------
const V = CodeMirror.Vim;
CodeMirror.commands.save = () => save().catch(() => {});
V.defineEx("wq", "wq", () => save().then(focusList).catch(() => {}));
V.defineEx("x", "x", () => save().then(focusList).catch(() => {}));
V.defineEx("quit", "q", focusList);
V.defineEx("final", "fin", () => finalizeLetter());
V.defineEx("sent", "sent", () => setStatus("sent"));
V.defineEx("offen", "off", () => setStatus("open"));
V.defineEx("next", "n", () => step(1));
V.defineEx("Next", "N", () => step(-1));
V.defineEx("fb", "fb", focusFb);
V.defineEx("letter", "let", focusEd);
V.defineEx("brief", "br", focusEd);
V.defineEx("edit", "e", (cm, p) => {
  if ((p.argString || "").trim() === "!" || !(dirtyEd || dirtyFb)) open(cur.path, true).then(() => msg(t("reloaded")));
  else msg(t("unsaved"), true);
});
V.defineEx("only", "on", () => toggleMax(activePanel));
V.defineEx("max", "max", () => toggleMax(activePanel));
V.defineEx("diff", "diff", () => showDiff());
V.defineEx("settings", "sett", () => openSettings());
V.defineEx("jobs", "jobs", () => setView("jobs"));
V.defineEx("letters", "lett", () => setView("letters"));
V.defineEx("bewerbungen", "bew", () => setView("letters"));
V.defineEx("knowledge", "kn", () => setView("knowledge"));
V.defineEx("wissen", "wis", () => setView("knowledge"));
V.defineEx("raw", "raw", () => setLive(false));
V.defineEx("live", "live", () => setLive(true));

// ---------- Apply feedback through the AI provider ----------
// The server starts the job, the page polls every 2 s. The old text comes back too (for diff and undo).
const running = new Set();
const lastJob = {};  // path -> last "done" result with the old text
let answer = "";
const finalizing = new Set();  // paths for which Word/PDF are being created right now
function jobUi() {
  const fin = !!cur && finalizing.has(cur.path), r = !!cur && running.has(cur.path) || fin;
  ed.setOption("readOnly", r); fb.setOption("readOnly", r);
  $("#bFinal").disabled = fin; $("#bFinal").textContent = fin ? t("finalizing") : t("final");
  $("#bApply").disabled = r || !cur;
  $("#bApply").textContent = cur && running.has(cur.path) ? t("working") : t("apply");
  $("#bCancel").hidden = !r;
  $("#bDiff").hidden = !cur || !lastJob[cur.path];
  $("#bDiffM").disabled = !cur || !lastJob[cur.path]; $("#bApplyM").disabled = r || !cur;
  $("#answer").textContent = fin ? t("fin_locked") : r ? t("locked") : answer ? t("ai_says") + answer : "";
  $("#answer").title = answer;
}
async function applyFeedback() {
  if (!cur || running.has(cur.path)) return;
  if (!fb.getValue().trim()) { msg(t("no_feedback"), true); return; }
  if (dirtyEd || dirtyFb) await save();
  startApply(cur.path);
}
async function startApply(path) {
  try {
    await postJson("/api/apply", {path});
  } catch (e) { msg(t("error") + e.message, true); return; }
  running.add(path); jobUi(); renderList();
  msg((items.find(i => i.path === path)?.company || path) + t("started"));
  poll(path);
}
// hints from the final check: each as its own toast, actionable ones with "Accept"
const queued = new Set();  // letters whose feedback is applied after the running job
function showHints(path, hints) {
  for (const h of hints) {
    // "Info: ..." is only information, "KI-Prüfung nicht möglich"/"AI check not possible" cannot be applied
    const info = h.startsWith("Info"), ok = !info && !/^(KI-Pr(ü|ue)fung|AI check)/.test(h);
    toast(h, !info, ok && {label: t("accept"), fn: () => acceptHint(path, h)});
  }
}
async function acceptHint(path, hint) {
  const line = "- " + hint.replace(/^(KI|AI):\s*/, "");
  const add = old => (old.trim() ? old.trimEnd() + "\n" : "") + line;
  try {
    if (cur?.path === path) { fb.setValue(add(fb.getValue())); dirtyFb = true; await save(); }
    else { await post({path, feedback: add(items.find(i => i.path === path)?.feedback || "")}); await refresh(); }
  } catch (e) { return; }
  if (running.has(path) || finalizing.has(path)) { queued.add(path); msg(t("hint_queued")); return; }
  startApply(path);
}
async function poll(path) {
  const j = await waitJob("feedback", path);
  running.delete(path);
  const company = items.find(i => i.path === path)?.company || path;
  if (j.state === "done") {
    lastJob[path] = j;
    msg(company + t("done"));
    if (cur?.path === path) { await open(path, true); if (j.changed) showDiff(); }
  } else if (j.state === "cancelled") msg(company + ": " + t("cancelled"));
  else msg(company + ": " + (j.text || t("unknown")), true);
  await refresh(); jobUi();
  const it = items.find(i => i.path === path);
  if (queued.delete(path) && it?.feedback?.trim()) startApply(path);  // hints accepted meanwhile
  else if (j.state === "done" && j.changed && it?.status === "final" && it.files) finalizeLetter(it);  // the PDFs would be outdated otherwise
}

// ---------- Finalize: Word and PDF from the template, then checks, then column Final ----------
async function finalizeLetter(it = cur) {
  if (!it || finalizing.has(it.path) || running.has(it.path)) return;
  if (cur?.path === it.path && (dirtyEd || dirtyFb)) await save();
  try { await postJson("/api/finalize", {path: it.path}); } catch (e) { msg(t("error") + e.message, true); return; }
  finalizing.add(it.path); jobUi(); renderList();
  msg(it.company + t("fin_started"));
  pollFin(it.path);
}
async function pollFin(path) {
  const j = await waitJob("finalize", path);
  finalizing.delete(path);
  const company = items.find(i => i.path === path)?.company || path;
  if (j.state === "done") {
    const hints = j.hints || [];
    msg(company + t("fin_done") + (hints.length ? ` (${hints.length} ${t(hints.length === 1 ? "fin_hint" : "fin_hints")})` : ` (${t("fin_ok")})`));
    showHints(path, hints);
    await refresh();
    if (cur?.path === path) await open(path, true);  // the date in the letter may have changed
  } else { msg(company + ": " + (j.state === "cancelled" ? t("cancelled") : j.text || t("unknown")), j.state !== "cancelled"); await refresh(); }
  jobUi();
  if (queued.delete(path) && items.find(i => i.path === path)?.feedback?.trim()) startApply(path);
}
// new tabs always through real links: some browsers silently block window.open
function openUrl(u) { const a = Object.assign(document.createElement("a"), {href: u, target: "_blank", rel: "noopener"}); a.click(); }
const openFile = rel => rel && openUrl("/file?path=" + encodeURIComponent(rel));

// line diff (LCS). ponytail: O(n*m), letters have fewer than 200 lines
function diffLines(a, b) {
  const A = a.split("\n"), B = b.split("\n"), n = A.length, m = B.length;
  const dp = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { out.push([" ", A[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(["-", A[i++]]);
    else out.push(["+", B[j++]]);
  }
  while (i < n) out.push(["-", A[i++]]);
  while (j < m) out.push(["+", B[j++]]);
  return out;
}
function showDiff() {
  const j = cur && lastJob[cur.path];
  if (!j) { msg(t("no_diff"), true); return; }
  $("#diffBody").innerHTML = diffHtml(j.before, ed.getValue());
  $("#bUndo").disabled = j.before === ed.getValue();
  $("#diff").classList.add("on");
}
async function undoJob() {
  const j = cur && lastJob[cur.path];
  if (!j) return;
  loading = true; ed.setValue(j.before); loading = false;
  try {
    // undo: the server notes it in the feedback log so the next round and the learnings know about it
    const r = await post({path: cur.path, content: j.before, mtime, undo: true, ...(dirtyFb ? {feedback: fb.getValue()} : {})});
    mtime = r.mtime; dirtyEd = dirtyFb = false; bar();
    msg(t("undone")); delete lastJob[cur.path]; jobUi(); rules(); if (preview) renderPreview();
  } catch (e) { dirtyEd = true; bar(); msg(e.status === 409 ? t("conflict") : t("error") + e.message, true); }
  $("#diff").classList.remove("on");
}
$("#bApply").onclick = applyFeedback;
$("#bCancel").onclick = () => cur && (finalizing.has(cur.path) ? cancelJob("finalize", cur.path) : cancelJob("feedback", cur.path));
V.defineEx("apply", "app", applyFeedback);
V.defineEx("umsetzen", "ums", applyFeedback);
$("#bDiff").onclick = () => showDiff();
$("#bUndo").onclick = undoJob;
$("#bDiffClose").onclick = $("#bDiffClose2").onclick = () => $("#diff").classList.remove("on");
$("#diff").onclick = e => { if (e.target === $("#diff")) $("#diff").classList.remove("on"); };

// ---------- Layout: zones, dividers, moving, maximizing and hiding panels ----------
const panelEl = id => $(`.panel[data-panel="${id}"]`);
function sanitizeLayout(l) {
  const out = structuredClone(DEFAULT_LAYOUT);
  if (!l || typeof l !== "object") return out;
  const seen = new Set();
  for (const z of Object.keys(out.zones)) {
    out.zones[z] = (Array.isArray(l.zones?.[z]) ? l.zones[z] : []).filter(id => PANELS.includes(id) && !seen.has(id) && seen.add(id));
  }
  for (const id of PANELS) if (!seen.has(id)) out.zones[Object.keys(DEFAULT_LAYOUT.zones).find(z => DEFAULT_LAYOUT.zones[z].includes(id))].push(id);
  for (const k of Object.keys(out.sizes)) if (Number.isFinite(l.sizes?.[k])) out.sizes[k] = Math.max(120, Math.min(1600, l.sizes[k]));
  out.hidden = (Array.isArray(l.hidden) ? l.hidden : []).filter(id => PANELS.includes(id));
  out.max = PANELS.includes(l.max) && !out.hidden.includes(l.max) ? l.max : null;
  return out;
}
function applyLayout() {
  const ws = $("#ws");
  for (const [z, ids] of Object.entries(L.zones)) {
    const zone = $(`.zone[data-zone="${z}"]`);
    for (const id of ids) zone.append(panelEl(id));
  }
  for (const id of PANELS) {
    const el = panelEl(id);
    el.hidden = L.hidden.includes(id);
    el.classList.toggle("maxed", L.max === id);
    el.querySelector(".pmax").innerHTML = `<svg><use href="#i-${L.max === id ? "restore" : "max"}"/></svg>`;
  }
  for (const zone of $$(".zone")) zone.hidden = ![...zone.children].some(c => !c.hidden);
  const vis = z => !$(`.zone[data-zone="${z}"]`).hidden;
  $(".split[data-for=left]").hidden = !vis("left") || !(vis("center") || vis("right") || vis("bottom"));
  $(".split[data-for=right]").hidden = !vis("right") || !vis("center");
  $(".split[data-for=bottom]").hidden = !vis("bottom") || !(vis("center") || vis("right"));
  ws.classList.toggle("max", !!L.max);
  // when the center is empty, the right (otherwise the left) zone takes the free space instead of a gap
  ws.classList.toggle("no-center", !vis("center"));
  ws.classList.toggle("no-right", !vis("right"));
  setSizes();
  preview = !L.hidden.includes("preview");
  ed.refresh(); fb.refresh(); if (preview) renderPreview();
  header(); chips();
}
function setSizes() {
  const ws = $("#ws");
  ws.style.setProperty("--w-left", L.sizes.left + "px");
  ws.style.setProperty("--w-right", L.sizes.right + "px");
  ws.style.setProperty("--h-bottom", L.sizes.bottom + "px");
}
let layoutTimer = null;
function saveLayout() {
  try { localStorage.setItem("rv-layout", JSON.stringify(L)); } catch {}
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(() => postJson("/api/settings", {layout: L}).catch(e => msg(t("error") + e.message, true)), 500);
}
function chips() {
  const c = $("#chips"); c.innerHTML = "";
  for (const id of L.hidden) {
    const b = document.createElement("button");
    b.textContent = "+ " + t("p_" + id); b.title = t("show_panel");
    b.onclick = () => showPanel(id);
    c.append(b);
  }
}
function toggleMax(id) { L.max = L.max === id ? null : id; applyLayout(); saveLayout(); }
function hidePanel(id) {
  if (!L.hidden.includes(id)) L.hidden.push(id);
  if (L.max === id) L.max = null;
  applyLayout(); saveLayout();
  if (activePanel === id && id !== "list") focusList();
}
function showPanel(id) { L.hidden = L.hidden.filter(x => x !== id); applyLayout(); saveLayout(); }
function ensureVisible(id) {
  if (!L.hidden.includes(id) && (!L.max || L.max === id)) return;
  L.hidden = L.hidden.filter(x => x !== id);
  if (L.max && L.max !== id) L.max = null;
  applyLayout(); saveLayout();
}
function togglePanel(id) { L.hidden.includes(id) ? showPanel(id) : hidePanel(id); }
function resetLayout() { L = structuredClone(DEFAULT_LAYOUT); applyLayout(); saveLayout(); }

// drag the dividers (pointer events)
for (const sp of $$(".split")) {
  sp.onpointerdown = e => {
    const z = sp.dataset.for, horiz = z === "bottom", start = horiz ? e.clientY : e.clientX, init = L.sizes[z];
    sp.setPointerCapture(e.pointerId); sp.classList.add("active"); document.body.classList.add("resizing");
    document.body.style.cursor = horiz ? "row-resize" : "col-resize";
    sp.onpointermove = ev => {
      const d = (horiz ? ev.clientY : ev.clientX) - start;
      L.sizes[z] = Math.max(120, Math.min(1600, z === "left" ? init + d : init - d));
      setSizes();
    };
    sp.onpointerup = sp.onpointercancel = () => {
      sp.onpointermove = sp.onpointerup = sp.onpointercancel = null;
      sp.classList.remove("active"); document.body.classList.remove("resizing"); document.body.style.cursor = "";
      ed.refresh(); fb.refresh(); saveLayout();
    };
    e.preventDefault();
  };
}

// drag panels by their head and drop them into a zone or before/after another panel
function dropTarget(x, y, self) {
  const el = document.elementFromPoint(x, y);
  const zone = el?.closest(".zone"); if (!zone) return null;
  const z = zone.dataset.zone, horiz = z === "bottom";
  const panels = [...zone.children].filter(p => !p.hidden && p.dataset.panel !== self);
  for (const p of panels) {
    const r = p.getBoundingClientRect();
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
      const after = horiz ? x > (r.left + r.right) / 2 : y > (r.top + r.bottom) / 2;
      return {zone: z, ref: p.dataset.panel, after};
    }
  }
  return {zone: z};
}
function markTarget(tg) {
  for (const z of $$(".zone")) z.classList.toggle("over", !!tg && !tg.ref && z.dataset.zone === tg.zone);
  for (const p of $$(".panel")) { p.classList.toggle("before", !!tg?.ref && tg.ref === p.dataset.panel && !tg.after); p.classList.toggle("after", !!tg?.ref && tg.ref === p.dataset.panel && tg.after); }
}
function dropPanel(id, tg) {
  for (const z of Object.keys(L.zones)) L.zones[z] = L.zones[z].filter(x => x !== id);
  const arr = L.zones[tg.zone];
  if (tg.ref) arr.splice(arr.indexOf(tg.ref) + (tg.after ? 1 : 0), 0, id); else arr.push(id);
  applyLayout(); saveLayout();
}
for (const panel of $$(".panel")) {
  const id = panel.dataset.panel, head = panel.querySelector(".phead");
  panel.querySelector(".pmax").onclick = () => toggleMax(id);
  panel.querySelector(".phide").onclick = () => hidePanel(id);
  head.ondblclick = e => { if (!e.target.closest("button")) toggleMax(id); };
  panel.addEventListener("pointerdown", () => setActive(id), true);
  head.onpointerdown = e => {
    if (e.target.closest("button") || e.button !== 0) return;
    const sx = e.clientX, sy = e.clientY; let moved = false, tg = null;
    head.setPointerCapture(e.pointerId);
    head.onpointermove = ev => {
      if (!moved) { if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 6) return; moved = true; $("#ws").classList.add("dragging"); $("#ghost").textContent = t("p_" + id); $("#ghost").classList.add("on"); }
      $("#ghost").style.left = ev.clientX + 12 + "px"; $("#ghost").style.top = ev.clientY + 12 + "px";
      tg = dropTarget(ev.clientX, ev.clientY, id); markTarget(tg);
    };
    head.onpointerup = head.onpointercancel = ev => {
      head.onpointermove = head.onpointerup = head.onpointercancel = null;
      if (!moved) return;
      $("#ws").classList.remove("dragging"); $("#ghost").classList.remove("on"); markTarget(null);
      if (ev.type === "pointerup" && tg) dropPanel(id, tg);
    };
    e.preventDefault();
  };
}

// ---------- Settings ----------
function applySettings() {
  const root = document.documentElement;
  if (S.theme === "system") delete root.dataset.theme; else root.dataset.theme = S.theme;
  if (S.accent) root.style.setProperty("--accent", S.accent); else root.style.removeProperty("--accent");
  try { localStorage.setItem("rv-theme", S.theme); localStorage.setItem("rv-accent", S.accent || ""); } catch {}
  for (const cm of [ed, fb, ...(typeof prEd !== "undefined" ? [prEd] : [])]) {
    cm.setOption("keyMap", S.vim ? "vim" : "default");
    if (cm === prEd) continue;  // keeps its own keys (Ctrl+S)
    cm.setOption("extraKeys", S.vim ? {} : {Esc: focusList});
  }
  $("#sTheme").value = S.theme; $("#sLang").value = S.lang; $("#sVim").checked = !!S.vim; $("#sSpell").checked = S.spellcheck !== false; $("#sUpdate").checked = !!S.update_check;
  for (const cm of [ed, fb]) cm.setOption("spellcheck", S.spellcheck !== false);
  const missing = Object.entries(S.tools || {}).filter(([, ok]) => !ok).map(([k]) => ({soffice: "LibreOffice", pdftotext: "Poppler"}[k] || k));
  $("#sInfo").textContent = `${t("s_version")} ${S.version || "?"} · ${t("s_config")} ${S.config_dir || "?"} · ` + (missing.length ? `${t("s_missing")}: ${missing.join(", ")}` : t("s_all_tools"));
  $("#sAccent").value = S.accent || getComputedStyle(root).getPropertyValue("--accent").trim() || "#2b5fd9";
  $("#rootPath").value = S.root;
  for (const b of $$("#swatches button")) b.classList.toggle("on", b.dataset.accent === (S.accent || ""));
  translate();
}
async function setSetting(patch) {
  try {
    S = await postJson("/api/settings", patch);
    $("#smsg").textContent = ""; applySettings();
    return true;
  } catch (e) { $("#smsg").textContent = t("error") + e.message; return false; }
}
let settingsLoaded = false;
function openSettings() { setView("settings"); }
function closeSettings() { setView(prevMain); }
$("#bSBack").onclick = closeSettings;
for (const b of $$("#snav button")) b.onclick = () => showSec(b.dataset.sec);
function showSec(id) {
  for (const b of $$("#snav button")) b.classList.toggle("on", b.dataset.sec === id);
  for (const sec of $$("#settings section[data-sec]")) sec.hidden = sec.dataset.sec !== id;
  $("#smsg").textContent = "";
}
// [id, English name, German name if different]; the color tokens live in app.css. The ids are stored in the settings.
const THEME_LIST = [["pixel", "Pixel"], ["nord", "Nord"], ["dracula", "Dracula"], ["solarized-light", "Solarized Light", "Solarized Hell"], ["solarized-dark", "Solarized Dark", "Solarized Dunkel"],
  ["gruvbox-dark", "Gruvbox Dark", "Gruvbox Dunkel"], ["gruvbox-light", "Gruvbox Light", "Gruvbox Hell"], ["catppuccin-mocha", "Catppuccin Mocha"], ["catppuccin-latte", "Catppuccin Latte"],
  ["tokyo-night", "Tokyo Night"], ["one-dark", "One Dark"], ["monokai", "Monokai"], ["rose-pine", "Rosé Pine"], ["rose-pine-dawn", "Rosé Pine Dawn"], ["everforest", "Everforest"],
  ["kanagawa", "Kanagawa"], ["sepia", "Sepia (paper)", "Sepia (Papier)"], ["terminal", "Terminal"], ["synthwave", "Synthwave"], ["hochkontrast", "High contrast", "Hoher Kontrast"],
  ["ozean", "Ocean", "Ozean"], ["zuckerwatte", "Cotton candy (kitsch)", "Zuckerwatte (kitschig)"]];
const themeName = ([, en, de]) => S.lang === "de" && de || en;
const sortedThemes = () => [...THEME_LIST].sort((a, b) => themeName(a).localeCompare(themeName(b), S.lang));  // by the shown name, so per language
function fillThemes() {
  let g = $("#sTheme optgroup");
  if (!g) { g = document.createElement("optgroup"); $("#sTheme").append(g); }
  g.label = t("th_more");
  g.replaceChildren(...sortedThemes().map(x => new Option(themeName(x), x[0])));
  $("#sTheme").value = S.theme;
}
$("#sTheme").onchange = e => setSetting({theme: e.target.value});
$("#sLang").onchange = e => setSetting({lang: e.target.value});
$("#sVim").onchange = e => setSetting({vim: e.target.checked});
$("#sSpell").onchange = e => setSetting({spellcheck: e.target.checked});
$("#sUpdate").onchange = async e => { if (await setSetting({update_check: e.target.checked}) && e.target.checked) checkUpdate(true); };
// Only when switched on: the server asks GitHub for the newest release (at most every 6 hours)
async function checkUpdate(tell) {
  const u = await api("/api/update").catch(() => null);
  if (!u?.enabled) return;
  if (u.newer) toast(`${t("upd_new")} ${u.latest} (${t("s_version")} ${u.current})`, false, {label: t("upd_open"), fn: () => window.open(u.url, "_blank", "noopener")});
  else if (tell) msg(u.error ? t("error") + u.error : `${t("upd_current")} ${u.current}`, !!u.error);
}
$("#sAccent").oninput = e => setSetting({accent: e.target.value});
for (const a of ACCENTS) {
  const b = document.createElement("button");
  b.dataset.accent = a; b.style.background = a || "linear-gradient(135deg,#2b5fd9,#7aa2ff)"; b.title = a || t("default");
  b.onclick = () => setSetting({accent: a});
  $("#swatches").append(b);
}
$("#bLayoutReset").onclick = () => { resetLayout(); $("#smsg").textContent = t("s_layout_done"); };

// folder: text field plus a server-side folder browser
let browseAt = "";
async function applyRoot(path) {
  $("#rootErr").textContent = "";
  const ok = await setSetting({root: path});
  if (!ok) { $("#rootErr").textContent = $("#smsg").textContent; $("#smsg").textContent = ""; return; }
  cur = null; mtime = null; items = []; sel = 0; Object.keys(lastJob).forEach(k => delete lastJob[k]);
  loading = true; ed.setValue(""); fb.setValue(""); loading = false; dirtyEd = dirtyFb = false;
  settingsLoaded = false;
  await refresh().catch(() => {});
  if (items.length) open(ordered()[0]?.path).catch(() => {});
  msg(t("s_folder_ok") + ": " + S.root);
  $("#rootErr").textContent = ""; $("#smsg").textContent = t("s_folder_ok");
}
async function browse(path) {
  try {
    const d = await api("/api/dirs?path=" + encodeURIComponent(path || ""));
    browseAt = d.path; $("#browser").hidden = false; $("#bDirUp").disabled = !d.parent; $("#bDirUse").disabled = !d.ok;
    const el = $("#dirs"); el.innerHTML = `<div class="cur${d.ok ? " ok" : ""}">${esc(d.path)}${d.ok ? " ✓" : ""}</div>`;
    for (const s of d.dirs) {
      const row = document.createElement("div");
      row.className = s.ok ? "ok" : ""; row.textContent = s.name + "/" + (s.ok ? " ✓" : "");
      row.onclick = () => browse(d.path.replace(/\/$/, "") + "/" + s.name);
      el.append(row);
    }
    $("#bDirUp").onclick = () => browse(d.parent);
  } catch (e) {
    if (path && path !== S.root) return browse(S.root);  // invalid input in the text field: start at the current folder
    $("#rootErr").textContent = t("error") + e.message;
  }
}
$("#bRootApply").onclick = () => applyRoot($("#rootPath").value.trim());
$("#rootPath").onkeydown = e => { if (e.key === "Enter") applyRoot(e.target.value.trim()); };
$("#bBrowse").onclick = () => { if ($("#browser").hidden) browse($("#rootPath").value.trim() || S.root); else $("#browser").hidden = true; };
$("#bDirUse").onclick = () => { $("#rootPath").value = browseAt; applyRoot(browseAt); };

// memories: memory files, skill references, CLAUDE.md; rendered, editable with mtime protection
const stripFront = tx => tx.startsWith("---") ? tx.replace(/^---[\s\S]*?\n---\n?/, "") : tx;
async function loadMemory() {
  const el = $("#memory"); el.innerHTML = "…";
  let d;
  try { d = await api("/api/memory"); } catch (e) { el.textContent = t("error") + e.message; return; }
  el.innerHTML = "";
  const groups = [];
  const mem = d.files.filter(f => f.group === "memory");
  for (const ty of ["index", "user", "feedback", "project", "reference"]) {
    const g = mem.filter(f => f.type === ty); if (g.length) groups.push([t("g_memory") + ": " + t("g_" + ty), g]);
  }
  const other = mem.filter(f => !["index", "user", "feedback", "project", "reference"].includes(f.type));
  if (other.length) groups.push([t("g_memory") + ": " + t("g_other"), other]);
  if (!mem.length) { const p = document.createElement("div"); p.className = "hint"; p.textContent = t("mem_none") + d.dir; el.append(p); }
  const sk = d.files.filter(f => f.group === "skill"); if (sk.length) groups.push([t("g_skill"), sk]);
  const cl = d.files.filter(f => f.group === "claude"); if (cl.length) groups.push([t("g_claude"), cl]);
  for (const [title, files] of groups) {
    const h = document.createElement("h3"); h.className = "grp"; h.textContent = `${title} (${files.length})`; el.append(h);
    for (const f of files) el.append(memItem(f));
  }
}
function memItem(f) {
  const det = document.createElement("details"); det.className = "mem";
  const render = () => {
    det.innerHTML = `<summary><b>${esc(f.name)}</b><span>${esc(f.description)}</span><small>${esc(f.type)}</small></summary>` +
      `<div class="path">${esc(f.path)}${f.modified ? " · " + esc(f.modified.slice(0, 10)) : ""}</div><div class="memview">${md(stripFront(f.content))}</div><div class="btns"><button class="edit">${t("edit")}</button></div>`;
    det.querySelector(".edit").onclick = edit;
  };
  const edit = () => {
    det.querySelector(".memview").innerHTML = `<textarea class="memedit">${esc(f.content)}</textarea>`;
    det.querySelector(".btns").innerHTML = `<button class="primary save">${t("save")}</button><button class="cancel">${t("cancel")}</button><span class="err"></span>`;
    det.querySelector(".cancel").onclick = render;
    det.querySelector(".save").onclick = async () => {
      const content = det.querySelector("textarea").value;
      try {
        const r = await postJson("/api/memfile", {path: f.path, content, mtime: f.mtime});
        f.content = content; f.mtime = r.mtime; render(); msg(t("mem_saved") + ": " + f.name);
      } catch (e) { det.querySelector(".err").textContent = e.status === 409 ? t("conflict") : t("error") + e.message; }
    };
  };
  render();
  return det;
}
const fmtSize = n => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(0) + " KB" : (n / 1048576).toFixed(1) + " MB";

// ---------- Search in the list ----------
const search = $("#search");
search.oninput = () => { filter = search.value.trim().toLowerCase(); sel = Math.max(0, ordered().indexOf(cur)); renderList(); };
search.onkeydown = e => {
  if (e.key === "Enter") { e.preventDefault(); focusList(); }
  if (e.key === "Escape") { e.preventDefault(); search.value = ""; search.oninput(); search.classList.remove("on"); focusList(); }
};
function openSearch() { ensureVisible("list"); search.classList.add("on"); search.focus(); search.select(); }

// ---------- Buttons and keys ----------
$("#bPrev").onclick = () => togglePanel("preview");
$("#bRaw").onclick = () => setLive(!live);
$("#bFinal").onclick = () => finalizeLetter();
$("#bMarkFinal").onclick = () => setStatus("final");
$("#bRefin").onclick = () => finalizeLetter();
$("#bWarn").onclick = () => cur && showHints(cur.path, cur.hints || []);
$("#bSent").onclick = () => setStatus("sent");
$("#bBack").onclick = () => setStatus(cur.status === "sent" ? "final" : "open");
$("#bHelp").onclick = () => $("#help").classList.toggle("on");
$("#help").onclick = e => { if (e.target === $("#help") || e.target.closest(".close")) $("#help").classList.remove("on"); };
$("#list").addEventListener("focus", () => { setActive("list"); mode = t("mode_list"); bar(); });
$("#pv").addEventListener("focus", () => setActive("preview"));

document.addEventListener("keydown", e => {
  const inField = e.target.matches?.("input, textarea, select");
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (e.target.classList?.contains("memedit")) e.target.closest(".mem").querySelector(".save")?.click();
    else save().catch(() => {});
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "p")) { e.preventDefault(); openPalette(); return; }
  if ($("#palette").classList.contains("on")) return;
  if (view === "settings" || view === "knowledge" || view === "profile") {
    if (!inField && !e.target.closest?.(".CodeMirror") && !e.ctrlKey && !e.metaKey && !e.altKey && "12345".includes(e.key) && e.key) {
      e.preventDefault(); setView(["letters", "jobs", "knowledge", "pipeline", "profile"][+e.key - 1]); return;
    }
    const inEditor = e.target.closest?.(".CodeMirror");  // Esc and q belong to Vim there (profile editor)
    if (!inEditor && (e.key === "Escape" || (e.key === "q" && !inField))) { e.preventDefault(); closeSettings(); }
    return;
  }
  if (e.altKey && ["1", "2", "3"].includes(e.key)) { e.preventDefault(); [focusList, focusEd, focusFb][+e.key - 1](); return; }
  // Alt+h/j/k/l: to the next panel left/down/up/right, like Ctrl+W h/j/k/l in Vim (Ctrl+W closes the browser tab).
  // e.code instead of e.key because Alt produces other characters on the Mac.
  const DIRECTION = {KeyH: "left", KeyJ: "down", KeyK: "up", KeyL: "right"};
  if (e.altKey && !e.ctrlKey && !e.metaKey && DIRECTION[e.code] && view === "letters") { e.preventDefault(); panelJump(DIRECTION[e.code]); return; }
  if (inField || e.target.closest?.(".CodeMirror") || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key;
  if ($("#diff").classList.contains("on")) { if (k === "Escape" || k === "q") { e.preventDefault(); $("#diff").classList.remove("on"); } return; }
  const always = {
    ArrowDown: () => step(1), ArrowUp: () => step(-1), Enter: focusEd,
    "?": () => $("#help").classList.toggle("on"), Escape: () => $("#help").classList.remove("on"),
  };
  const vim = {
    j: () => step(1), k: () => step(-1), G: () => go(ordered().length - 1),
    i: focusEd, l: focusEd, c: focusFb,
    A: applyFeedback, F: () => cur?.status === "open" && finalizeLetter(), S: () => setStatus("sent"),
    O: () => cur && setStatus(cur.status === "sent" ? "final" : "open"),
    o: () => $("#bLink").click(), p: () => togglePanel("preview"), m: () => setLive(!live), r: () => refresh().then(() => msg(t("list_reloaded"))),
    "/": openSearch, z: () => toggleMax(activePanel), d: () => showDiff(), ",": openSettings, v: openVersions, n: openNewApp, P: () => togglePin().catch(e => msg(t("error") + e.message, true)),
  };
  if (S.vim && k === "g") { if (pendingG && view !== "jobs") go(0); pendingG = !pendingG; setTimeout(() => pendingG = false, 600); return; }
  const MAIN = ["letters", "pipeline", "jobs", "profile", "knowledge"];
  if (S.vim && pendingG && (k === "t" || k === "T")) { e.preventDefault(); pendingG = false; setView(MAIN[(MAIN.indexOf(view) + (k === "t" ? 1 : MAIN.length - 1)) % MAIN.length]); return; }
  const nav = {"1": () => setView("letters"), "2": () => setView("jobs"), "3": () => setView("knowledge"), "4": () => setView("pipeline"), "5": () => setView("profile"), ":": openPalette};
  if (view === "pipeline") {
    const pk = {...nav, ",": openSettings, "?": always["?"], Escape: always.Escape};
    if (pipeKey(k)) e.preventDefault(); else if (pk[k]) { e.preventDefault(); pk[k](); }
    return;
  }
  if (view === "jobs") {
    const jk = {...nav, ",": openSettings, "/": () => { setPane("results"); $("#jText").focus(); $("#jText").select(); }, "?": always["?"], Escape: always.Escape};
    if (S.vim && jk[k]) { e.preventDefault(); jk[k](); } else if (!S.vim && (k === "?" || k === "Escape")) always[k]();
    return;
  }
  const act = always[k] || (S.vim && (vim[k] || nav[k]));
  if (act) { e.preventDefault(); act(); }
});
window.addEventListener("beforeunload", e => { if (dirtyEd || dirtyFb) e.preventDefault(); });


// ---------- Views (rail) ----------
const VIEWS = {letters: "tab_letters", jobs: "tab_jobs", knowledge: "tab_knowledge", pipeline: "tab_pipe", profile: "tab_profile", settings: "settings"};
const VIEW_KEYS = {letters: "1", jobs: "2", knowledge: "3", pipeline: "4", profile: "5", settings: ","};
let view = "letters", prevMain = "letters", knowledgeLoaded = false;
function setView(v, save = true) {
  if (!VIEWS[v]) return;
  // hidden views lose their scroll position in the browser: remember it before switching, restore it when coming back
  if (view === "letters" && v !== "letters" && !pendingScroll) pendingScroll = [Math.round(ed.getScrollInfo().top), Math.round(fb.getScrollInfo().top)];
  if (view === "letters" || view === "jobs") prevMain = view;
  view = v; settingsOpen = v === "settings";
  document.body.dataset.view = v;
  $("#ws").hidden = v !== "letters";
  for (const id of ["jobs", "knowledge", "settings", "profile"]) $("#" + id).hidden = v !== id;
  $("#pipe").hidden = v !== "pipeline";
  for (const b of $$("#rail [data-view]")) b.classList.toggle("on", b.dataset.view === v);
  $("#crumbRoot").textContent = t(VIEWS[v]);
  $("#more").classList.remove("open");
  if (v === "letters") { ed.refresh(); fb.refresh(); applyPendingScroll(); if (document.activeElement === document.body || document.activeElement?.closest("#rail")) focusList(); }
  else {
    ed.getInputField().blur(); fb.getInputField().blur();
    mode = t(VIEWS[v]).toUpperCase(); bar();
    if (v === "jobs" && !jruns.length) loadRuns().catch(e => msg(t("error") + e.message, true));
    if (v === "profile") openProfile();
    if (v === "pipeline") loadPipe().catch(e => msg(t("error") + e.message, true));
    if (v === "knowledge" && !knowledgeLoaded) { knowledgeLoaded = true; loadMemory(); loadLearnings(); }
    if (v === "settings") $("#snav button.on")?.focus();
  }
  if (save && (v === "letters" || v === "jobs") && S.tab !== v) { S.tab = v; postJson("/api/settings", {tab: v}).catch(() => {}); }
}
for (const b of $$("#rail [data-view]")) b.onclick = () => setView(b.dataset.view);
$("#railToggle").onclick = () => { const open = document.documentElement.classList.toggle("rail-open"); try { localStorage.setItem("rv-rail", open ? "1" : "0"); } catch {} };
for (const b of $$("#wseg button")) b.onclick = () => { for (const x of $$("#wseg button")) x.classList.toggle("on", x === b); for (const sec of $$("#knowledge section[data-wsec]")) sec.hidden = sec.dataset.wsec !== b.dataset.wsec; };
for (const b of $$("#listSeg button")) b.onclick = e => { e.stopPropagation(); setListMode(b.dataset.lm === "board"); };
$("#listEmpty button").onclick = () => setView("jobs");
$("#bMore").onclick = e => { e.stopPropagation(); $("#more").classList.toggle("open"); };
document.addEventListener("click", e => { if (!e.target.closest("#more")) $("#more").classList.remove("open"); });
$("#more .menulist").onclick = () => $("#more").classList.remove("open");
$("#bDiffM").onclick = () => showDiff();
$("#bApplyM").onclick = applyFeedback;
$("#bReload").onclick = () => refresh().then(() => msg(t("list_reloaded")));
$("#bJStartTop").onclick = () => { setPane("run"); $("#bJStart").click(); };

// ---------- Command palette ----------
let palSel = 0, palItems = [];
function fuzzy(q, s) {
  s = s.toLowerCase(); if (!q) return 1;
  if (s.includes(q)) return 100 - s.indexOf(q);
  let i = 0, score = 0, last = -2;
  for (const ch of q) { const j = s.indexOf(ch, i); if (j < 0) return 0; score += j === last + 1 ? 3 : 1; last = j; i = j + 1; }
  return score;
}
function paletteItems() {
  const act = (label, key, run) => ({g: t("pal_actions"), label, key, run});
  return [
    ...Object.entries(VIEWS).map(([v, k], i) => ({g: t("pal_views"), label: t(k), key: VIEW_KEYS[v], run: () => setView(v)})),
    ...(typeof pitems !== "undefined" ? pitems : []).map(i => ({g: t("tab_pipe"), label: `${i.company} · ${t("ph_" + i.phase)}`, key: "", run: () => { setView("pipeline"); openDossier(i.path); }})),
    ...items.map(i => ({g: t("pal_letters"), label: `${i.company} · ${i.title || i.file}`, key: t("st_" + i.status), run: () => { setView("letters"); filter = ""; search.value = ""; sel = Math.max(0, ordered().indexOf(i)); renderList(); open(i.path).catch(() => {}); }})),
    act(t("final"), "F", () => finalizeLetter()), act(t("mark_final"), "", () => setStatus("final")), act(t("refinal"), "", () => finalizeLetter()),
    act(t("cl"), "", () => openFile(cur?.files?.cl_pdf)), act(t("cv"), "", () => openFile(cur?.files?.cv_pdf)), act(t("sent"), "S", () => setStatus("sent")), act(t("st_open"), "O", () => setStatus("open")),
    act(t("am_btn"), "", () => cur && startAppMail(cur.path)),
    act(t("new_app"), "n", openNewApp), act(t("versions"), "v", openVersions), act(t("pin") + " / " + t("unpin"), "P", () => togglePin()), act(t("archive"), "", () => toggleArchiveLetter()), act(t("archive_show_title"), "", () => setShowArchive(!showArchive)),
    act(t("apply"), "A", applyFeedback), act(t("diff_title"), "d", () => showDiff()), act(t("preview"), "p", () => togglePanel("preview")),
    act(live ? t("raw") : t("live"), "m", () => setLive(!live)), act(t("link"), "o", () => $("#bLink").click()), act(t("pc_btn"), "", () => checkPosting(cur?.path)), act(t("list_reloaded"), "r", () => refresh()),
    act(t("a_board"), "", () => setListMode(!boardMode)), act(t("a_maxpanel"), "z", () => toggleMax(activePanel)), act(t("s_layout_reset"), "", resetLayout),
    act(t("a_theme_light"), "", () => setSetting({theme: "light"})), act(t("a_theme_dark"), "", () => setSetting({theme: "dark"})), act(t("a_theme_system"), "", () => setSetting({theme: "system"})),
    ...sortedThemes().map(x => act("Theme: " + themeName(x), "", () => setSetting({theme: x[0]}))),
    act(t("a_lang_de"), "", () => setSetting({lang: "de"})), act(t("a_lang_en"), "", () => setSetting({lang: "en"})),
    act(S.vim ? t("a_vim_off") : t("a_vim_on"), "", () => setSetting({vim: !S.vim})),
    act(t("j_start"), "", () => { setView("jobs"); setPane("run"); $("#bJStart").click(); }), act(t("help"), "?", () => $("#help").classList.add("on")),
  ];
}
function openPalette() {
  $("#palette").classList.add("on"); $("#palIn").value = ""; renderPalette(); $("#palIn").focus();
}
function closePalette() { $("#palette").classList.remove("on"); if (view === "letters" && !ed.hasFocus() && !fb.hasFocus()) focusList(); }
function renderPalette() {
  const q = $("#palIn").value.trim().replace(/^:/, "").toLowerCase();
  palItems = paletteItems().map(it => [fuzzy(q, it.label), it]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, 14).map(x => x[1]);
  palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  const el = $("#palList"); el.innerHTML = ""; let g = null;
  if (!palItems.length) el.innerHTML = `<div class="palg">${t("pal_none")}</div>`;
  palItems.forEach((it, i) => {
    if (it.g !== g) { g = it.g; const h = document.createElement("div"); h.className = "palg"; h.textContent = g; el.append(h); }
    const d = document.createElement("div"); d.className = "pali" + (i === palSel ? " sel" : "");
    d.innerHTML = `<span></span>${it.key ? `<kbd>${esc(it.key)}</kbd>` : ""}`; d.firstElementChild.textContent = it.label;
    d.onclick = () => runPalette(i); d.onmousemove = () => { if (palSel !== i) { palSel = i; renderPalette(); } };
    el.append(d);
  });
  el.querySelector(".sel")?.scrollIntoView({block: "nearest"});
}
function runPalette(i) { const it = palItems[i]; closePalette(); if (it) it.run(); }
$("#palIn").oninput = () => { palSel = 0; renderPalette(); };
$("#palIn").onkeydown = e => {
  if (e.key === "ArrowDown") { e.preventDefault(); palSel = Math.min(palItems.length - 1, palSel + 1); renderPalette(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); palSel = Math.max(0, palSel - 1); renderPalette(); }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(palSel); }
  else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
};
$("#palette").onclick = e => { if (e.target === $("#palette")) closePalette(); };
$("#bPalette").onclick = openPalette;

// ---------- Job search: runs and cards ----------
let jruns = [], jrun = null, jdata = null, jcfg = null, jsrc = [], jpoll = null, jlogNext = 0;
const jdrafts = {};
const normUrl = u => { try { const x = new URL(u); return "https://" + x.host.replace(/^www\./, "") + x.pathname.replace(/\/$/, ""); } catch { return u; } };
async function loadRuns(select) {
  jruns = await api("/api/search/runs");
  renderRuns();
  if (jruns.length && (select || !jrun)) await openRun(select && jruns.some(r => r.name === select) ? select : jruns[0].name);
  else if (!jruns.length) { jdata = null; $("#jcards").innerHTML = `<div class="empty"><p>${t("j_none")}</p><button class="primary">${t("j_none_action")}</button></div>`; $("#jcards .empty button").onclick = () => setPane("run"); $("#jCount").textContent = ""; $("#jhead").hidden = $("#jmore").hidden = true; }
}
function renderRuns() {
  const el = $("#jrunlist"); el.innerHTML = "";
  for (const r of jruns) {
    const d = document.createElement("div");
    d.className = r.name === jrun ? "sel" : "";
    d.innerHTML = `<b>${esc(r.date)}</b><small>${esc(r.rel.split("/").pop())}</small>`;
    d.onclick = () => openRun(r.name).catch(e => msg(t("error") + e.message, true));
    el.append(d);
  }
}
async function openRun(name) {
  jdata = await api("/api/search/run?name=" + encodeURIComponent(name));
  jrun = name; renderRuns(); setPane("results"); $("#jhead").hidden = false;
  $("#jhead .md").innerHTML = md(Object.entries(jdata.head).map(([n, v]) => `**${n}:** ${v}`).join("  \n") + "\n\n" + (jdata.intro || ""));
  $("#jmore .md").innerHTML = md(jdata.other.map(a => `## ${a.name}\n\n${a.md}`).join("\n\n"));
  $("#jmore").hidden = !jdata.other.length;
  renderCards();
}
const jnotes = new Set(["A", "B", "C"]);
function renderCards() {
  if (!jdata) return;
  const txt = $("#jText").value.trim().toLowerCase(), onlyNew = $("#jOnlyNew").checked, showHidden = $("#jShowHidden").checked;
  const hidden = new Set((jdata.hidden || []).map(normUrl));
  const all = jdata.jobs, el = $("#jcards"); el.innerHTML = "";
  const rows = all.filter(s => jnotes.has(s.note) && (!onlyNew || s.new) && (showHidden || !hidden.has(normUrl(s.link))) &&
    (!txt || (s.company + " " + s.title + " " + s.place).toLowerCase().includes(txt)));
  $("#jCount").textContent = `${rows.length} ${t("j_of")} ${all.length} ${t("j_count")}`;
  const sort = $("#jSort").value;  // filter() returns a copy, so sort() does not change jdata
  if (sort === "score") rows.sort((a, b) => (b.score ?? 0) - (a.score ?? 0) || a.note.localeCompare(b.note));
  else if (sort === "company") rows.sort((a, b) => (a.company || "").localeCompare(b.company || "", S.lang));
  for (const s of rows) {
    const isHidden = hidden.has(normUrl(s.link)), c = document.createElement("div");
    const gone = s.check?.status === "gone";
    c.className = "card" + (isHidden ? " hidden" : "") + (gone ? " gone" : "");
    const dr = jdrafts[s.link];
    c.innerHTML = `<div class="top"><span class="note ${s.note}">${s.note}</span>${s.score ? `<span class="score" title="${t("j_score")}">${s.score}<small>/10</small></span>` : ""}<div><div class="firm">${esc(s.company || "?")}</div><div class="ttl">${esc(s.title)}</div><div class="place">${esc(s.place)}</div></div>` +
      (gone ? `<span class="tag gone" title="${esc(`${s.check.reason} (${s.check.checked})`)}">${t("j_gone")}</span>` : "") +
      (s.applied ? `<span class="tag applied">${t("j_applied")}</span>` : s.new ? `<span class="tag new">${t("j_new")}</span>` : "") + `</div>` +
      (s.workload ? `<p><b>${t("j_workload")}</b>${esc(s.workload)}</p>` : "") +
      (s.pros ? `<p><b>${t("j_pros")}</b>${esc(s.pros)}</p>` : "") + (s.gaps ? `<p><b>${t("j_gaps")}</b>${esc(s.gaps)}</p>` : "") +
      (s.hint ? `<p><b>${t("j_hint")}</b>${esc(s.hint)}</p>` : "") +
      (s.also ? `<p><b>${t("j_also")}</b>${[...s.also.matchAll(/(https?:\/\/\S+)(?: \(([^)]*)\))?/g)].map(m => `<a href="${esc(m[1])}" target="_blank" rel="noopener">${esc(m[2] || new URL(m[1]).hostname)}</a>`).join(", ")}</p>` : "") + (!s.pros && !s.gaps && s.md ? `<div class="md">${md(s.md)}</div>` : "") +
      `<div class="btns">${/^https?:\/\//.test(s.link || "") ? `<a class="btn open" href="${esc(s.link)}" target="_blank" rel="noopener">${t("j_open")}</a>` : `<button disabled>${t("j_open")}</button>`}<button class="draft" ${dr?.state === "running" ? "disabled" : ""}>${dr?.state === "running" ? t("j_drafting") : t("j_draft")}</button><button class="hide">${isHidden ? t("j_unhide") : t("j_hide")}</button></div>` +
      (dr?.state === "done" ? `<div class="draft">${t("j_drafted")}${esc(dr.path)}</div>` : dr?.state === "error" ? `<div class="draft err">${t("error")}${esc(dr.text)}</div>` : "");
    c.querySelector(".draft").onclick = () => startDraft(s);
    c.querySelector(".hide").onclick = async () => {
      const r = await postJson("/api/search/hide", {url: s.link, hidden: !isHidden}).catch(e => (msg(t("error") + e.message, true), null));
      if (r) { jdata.hidden = r.hidden; renderCards(); }
    };
    el.append(c);
  }
}
$("#listSort").onclick = () => setSort(!sortScore);
setSort(sortScore);
$("#jCheck").onclick = async () => {
  if (!jrun) return;
  const b = $("#jCheck"); b.disabled = true;
  try {
    await postJson("/api/search/check", {name: jrun});
    msg(t("j_checking"));
    const j = await waitJob("jobcheck", jrun, 2000);
    if (j.state === "done") { msg(t("j_checked").replace("{gone}", j.gone).replace("{unknown}", j.unknown)); await openRun(jrun); }
    else msg(t("error") + (j.text || t("unknown")), true);
  } catch (e) { msg(t("error") + e.message, true); }
  b.disabled = false;
};
$("#jText").oninput = renderCards; $("#jSort").onchange = renderCards; $("#jOnlyNew").onchange = renderCards; $("#jShowHidden").onchange = renderCards;
$("#jText").onkeydown = e => { if (e.key === "Escape") { e.preventDefault(); e.target.blur(); } };
for (const b of $$(".jbar .notes button")) b.onclick = () => { jnotes.has(b.dataset.note) ? jnotes.delete(b.dataset.note) : jnotes.add(b.dataset.note); b.classList.toggle("on"); renderCards(); };
async function startDraft(s) {
  let r;
  try { r = await postJson("/api/search/draft", {job: {url: s.link, company: s.company, title: s.title, place: s.place, md: s.md}}); }
  catch (e) { msg(t("error") + e.message, true); return; }
  jdrafts[s.link] = {state: "running"}; renderCards();
  const done = runningToast(`${s.company}: ${t("j_drafting")}`, "draft", r.key);
  const j = await waitJob("draft", r.key);
  done(); jdrafts[s.link] = j; renderCards();
  if (j.state === "done") { msg(t("j_drafted") + j.path); refresh().catch(() => {}); } else if (j.state !== "cancelled") msg(t("error") + j.text, true);
}
function setPane(p) {
  for (const [id, b] of [["jresults", "bJResults"], ["jconfig", "bJConfig"], ["jrun", "bJRun"]]) { $("#" + id).hidden = id !== "j" + p; $("#" + b).classList.toggle("on", id === "j" + p); }
  if (p === "config" && !jcfg) loadConfig().catch(e => msg(t("error") + e.message, true));
  if (p === "run") pollRun(true);
}
$("#bJResults").onclick = () => setPane("results"); $("#bJConfig").onclick = () => setPane("config"); $("#bJRun").onclick = () => setPane("run");

// ---------- Job search: settings ----------
const lines = v => v.split("\n").map(x => x.trim()).filter(Boolean);
async function loadConfig() { [jcfg, jsrc] = await Promise.all([api("/api/search/config"), api("/api/search/sources")]); renderConfig(); loadRefs(); }
function renderConfig() {  // the keys of jcfg (fields, places, radius_km, ...) are those of search.json
  const c = jcfg; if (!c) return;
  const f = $("#jfields"); f.innerHTML = "";
  c.fields.forEach((fe, i) => {
    const d = document.createElement("div"); d.className = "jfield";
    d.innerHTML = `<input type="checkbox" class="on" ${fe.on ? "checked" : ""} title="${t("j_field_on")}"><input class="name" value="${esc(fe.name)}" placeholder="${t("j_field_name")}"><textarea class="terms" placeholder="${t("j_terms")}">${esc(fe.terms.join("\n"))}</textarea><button type="button" class="rm">${t("j_remove")}</button>`;
    d.querySelector(".rm").onclick = () => { c.fields.splice(i, 1); renderConfig(); };
    f.append(d);
  });
  $("#jPlaces").value = c.places.join("\n"); $("#jLiPlaces").value = (c.linkedin_places || []).join("\n"); $("#jExcl").value = c.exclude.join("\n"); $("#jRadius").value = c.radius_km; $("#jFresh").value = c.max_age_days;
  $("#jTargetMin").value = c.target_min; $("#jTargetMax").value = c.target_max; $("#jMaxDetails").value = c.max_details; $("#jRemote").checked = !!c.remote;
  $("#jPages").value = c.pages || 1; $("#jRemotePlaces").value = (c.remote_places || []).join("\n"); $("#jScale").value = c.scale; renderSources();
  $("#jCompanies").value = (c.companies || []).join("\n"); $("#jAuto").checked = !!c.auto; $("#jAutoHour").value = c.auto_hour ?? 8;
  const sel = $("#jProvider"); sel.innerHTML = `<option value="">(${t("p_tasks")}: ${t("t_rate")})</option>` + (S.providers || []).map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  sel.value = c.provider || "";
}
function collectConfig() {
  const c = jcfg;
  c.fields = $$("#jfields .jfield").map(d => ({name: d.querySelector(".name").value.trim(), terms: lines(d.querySelector(".terms").value), on: d.querySelector(".on").checked})).filter(x => x.name);
  c.places = lines($("#jPlaces").value); c.linkedin_places = lines($("#jLiPlaces").value); c.exclude = lines($("#jExcl").value);
  c.radius_km = +$("#jRadius").value || 60; c.max_age_days = +$("#jFresh").value || 30; c.target_min = +$("#jTargetMin").value || 15; c.target_max = +$("#jTargetMax").value || 25;
  c.max_details = +$("#jMaxDetails").value || 60; c.remote = $("#jRemote").checked; c.pages = +$("#jPages").value || 1; c.remote_places = lines($("#jRemotePlaces").value);
  c.sources = Object.fromEntries($$("#jsrclist .jsrc[data-id]").map(d => [d.dataset.id, d.querySelector(".on").checked]));
  c.custom_sources = $$("#jsrclist .jsrc:not([data-id])").map(collectSource);
  c.scale = $("#jScale").value; c.provider = $("#jProvider").value;
  c.companies = lines($("#jCompanies").value); c.auto = $("#jAuto").checked; c.auto_hour = Math.min(23, Math.max(0, +$("#jAutoHour").value || 0));
  return c;
}
// Sources: the catalogue of the server plus the own ones (custom_sources). A feed of the catalogue can be copied and changed.
const SRC_MAP = ["items", "title", "company", "place", "date", "link", "text", "title_format", "link_contains"];
function renderSources() {
  const list = $("#jsrclist"); list.innerHTML = "";
  const test = (d, what) => async () => {
    const out = d.querySelector(".res"), b = d.querySelector(".test"); out.textContent = t("j_src_testing"); b.disabled = true;
    try {
      const r = await postJson("/api/search/test", {source: what()});
      const note = !r.count ? t("j_src_none") : t("j_src_found").replace("{n}", r.count).replace("{m}", r.matching) + (r.term ? ` (${r.term})` : "");
      out.innerHTML = `<div>${esc(note)}</div>` + r.jobs.map(j => `<div class="hit${j.match ? "" : " off"}"><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a> · ${esc(j.company)} · ${esc(j.place || "?")} · ${j.age} ${esc(t("j_src_days"))}</div>`).join("");
    } catch (e) { out.textContent = t("error") + e.message; }
    b.disabled = false;
  };
  jsrc.forEach(s => {
    const d = document.createElement("div"); d.className = "jsrc"; d.dataset.id = s.id;
    d.innerHTML = `<div class="row"><label><input type="checkbox" class="on" ${jcfg.sources[s.id] ? "checked" : ""}> ${esc(s.name)}</label><span class="hint">${esc(t("j_area_" + s.area))}</span><span class="spacer"></span>${s.kind === "feed" ? `<button type="button" class="copy">${t("j_src_copy")}</button>` : ""}<button type="button" class="test">${t("j_src_test")}</button></div><div class="res hint"></div>`;
    d.querySelector(".test").onclick = test(d, () => s.id);
    if (s.kind === "feed") d.querySelector(".copy").onclick = () => { collectConfig(); jcfg.sources[s.id] = false; addSource({...s, id: "", name: s.name + " (2)", on: true}); };
    list.append(d);
  });
  (jcfg.custom_sources || []).forEach((s, i) => {
    const d = document.createElement("div"); d.className = "jsrc own"; d.dataset.own = s.id || "";
    d.innerHTML = `<div class="row"><label><input type="checkbox" class="on" ${s.on ? "checked" : ""}></label><input class="name" value="${esc(s.name || "")}" placeholder="${t("j_src_name")}" aria-label="${t("j_src_name")}"><input class="url" value="${esc(s.url || "")}" placeholder="https://example.com/jobs?q={term}" aria-label="${t("j_src_url")}"><button type="button" class="test">${t("j_src_test")}</button><button type="button" class="rm">${t("j_remove")}</button></div>
      <div class="row"><label>${t("j_src_match")} <select class="match">${["title", "text", "all"].map(m => `<option value="${m}" ${(s.match || "title") === m ? "selected" : ""}>${t("j_src_match_" + m)}</option>`).join("")}</select></label><label><input type="checkbox" class="remote" ${s.remote ? "checked" : ""}> ${t("j_src_remote")}</label></div>
      <details ${SRC_MAP.some(k => s[k]) ? "open" : ""}><summary>${t("j_src_map")}</summary><div class="hint">${esc(t("j_src_map_hint"))}</div><div class="jmap">${SRC_MAP.map(k => `<label>${t("j_map_" + k)}<input data-k="${k}" value="${esc(s[k] || "")}"></label>`).join("")}</div></details><div class="res hint"></div>`;
    d.querySelector(".test").onclick = test(d, () => collectSource(d));
    d.querySelector(".rm").onclick = () => { collectConfig(); jcfg.custom_sources.splice(i, 1); renderConfig(); };
    list.append(d);
  });
}
function collectSource(d) {
  const s = {id: d.dataset.own, name: d.querySelector(".name").value.trim(), url: d.querySelector(".url").value.trim(), on: d.querySelector(".on").checked, match: d.querySelector(".match").value, remote: d.querySelector(".remote").checked};
  d.querySelectorAll("[data-k]").forEach(i => { if (i.value.trim()) s[i.dataset.k] = i.value.trim(); });
  return s;
}
function addSource(s) { jcfg.custom_sources = [...(jcfg.custom_sources || []), s]; renderConfig(); const own = $$("#jsrclist .own"); own[own.length - 1].querySelector(".name").focus(); }
$("#bJAddSource").onclick = () => { collectConfig(); addSource({name: "", url: "", on: true, match: "title"}); };
$("#bJAddField").onclick = () => { collectConfig(); jcfg.fields.push({name: "", terms: [], on: true}); renderConfig(); };
$("#bJSave").onclick = async () => {
  try { jcfg = await postJson("/api/search/config", collectConfig()); renderConfig(); $("#jcfgmsg").textContent = t("j_saved") + " " + new Date().toLocaleTimeString(locale()); }
  catch (e) { $("#jcfgmsg").textContent = t("error") + e.message; }
};
async function loadRefs() {
  const el = $("#jrefs"); el.innerHTML = "";
  const d = await api("/api/memory").catch(() => ({files: []}));
  for (const f of d.files.filter(f => f.group === "skill" && f.name.startsWith("stellensuche/"))) el.append(memItem(f));
}

// ---------- Job search: run ----------
async function pollRun(once) {
  const st = await api("/api/search/status?since=" + jlogNext).catch(() => null);
  if (!st) return;
  if (st.next < jlogNext) { jlogNext = 0; $("#jlog").textContent = ""; }
  if (st.log.length) { $("#jlog").textContent += st.log.join("\n") + "\n"; $("#jlog").scrollTop = $("#jlog").scrollHeight; }
  jlogNext = st.next;
  $("#jcounters").innerHTML = Object.entries(st.n || {}).map(([k, v]) => `<span><b>${v}</b> ${t("n_" + k)}</span>`).join("");
  const running = st.state === "running";
  $("#bJStart").disabled = running; $("#bJStop").hidden = !running;
  $("#jstate").textContent = running ? t("j_running") : st.state === "done" ? t("j_done") + (st.file ? ": " + st.file : "") : st.state === "stopped" ? t("j_stopped") : st.state === "error" ? t("j_error") + ": " + (st.error || "") : "";
  if (running) jpoll = setTimeout(() => pollRun(), 1500);
  else if (jpoll && !once) { jpoll = null; const f = st.file ? st.file.split("/").pop().replace(/\.md$/, "") : null; loadRuns(f).then(() => { if (f) setPane("run"); }).catch(() => {}); }
}
$("#bJStart").onclick = async () => {
  try { await postJson("/api/search/start", {mode: $("#jMode").value}); } catch (e) { $("#jstate").textContent = t("error") + e.message; return; }
  jlogNext = 0; $("#jlog").textContent = ""; jpoll = jpoll || 1; pollRun();
};
$("#bJStop").onclick = () => postJson("/api/search/stop", {}).catch(() => {});

// ---------- AI providers ----------
const TEMPLATES = {OpenAI: ["https://api.openai.com/v1", "gpt-5"], OpenRouter: ["https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4.5"], Mistral: ["https://api.mistral.ai/v1", "mistral-large-latest"],
  Groq: ["https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"], Together: ["https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo"],
  Ollama: ["http://localhost:11434/v1", "llama3"], "LM Studio": ["http://localhost:1234/v1", "local-model"]};
async function renderUsage() {
  const rows = await api("/api/usage").catch(() => []), el = $("#usage");
  if (!rows.length) { el.innerHTML = `<p class="muted">${t("u_none")}</p>`; return; }
  const n = x => x.toLocaleString(locale());
  el.innerHTML = `<table><tr><th>${t("u_month")}</th><th>${t("p_tasks")}</th><th>${t("u_provider")}</th><th>${t("u_requests")}</th><th>${t("u_in")}</th><th>${t("u_out")}</th><th>USD</th></tr>` +
    rows.map(r => `<tr><td>${esc(r.month)}</td><td>${esc(r.task ? t("t_" + r.task) : "?")}</td><td>${esc(r.provider)}</td><td>${n(r.n)}</td><td>${n(r.in)}</td><td>${n(r.out)}</td><td>${r.cost ? r.cost.toFixed(2) : ""}</td></tr>`).join("") + `</table>`;
}
function renderProviders() {
  renderUsage();
  const el = $("#plist"); el.innerHTML = "";
  for (const p of S.providers || []) {
    const d = document.createElement("div"); d.className = "prov";
    d.innerHTML = `<b>${esc(p.name)}</b><span class="meta">${esc(p.type)}${p.model ? " · " + esc(p.model) : ""}${p.base_url ? " · " + esc(p.base_url) : ""}${p.cmd ? " · " + esc(p.cmd) : ""}${p.key_masked ? " · " + t("p_key") + " " + esc(p.key_masked) : ""}</span>` +
      `<span class="res"></span><button class="test">${t("p_test")}</button><button class="edit">${t("p_edit")}</button><button class="del">${t("p_delete")}</button>`;
    d.querySelector(".test").onclick = async () => {
      const r = d.querySelector(".res"); r.className = "res"; r.textContent = t("p_testing");
      const j = await postJson("/api/providers/test", {id: p.id}).catch(e => ({ok: false, text: e.message}));
      r.className = "res " + (j.ok ? "ok" : "fail"); r.textContent = (j.ok ? t("p_ok") + " (" + j.ms + " ms): " : t("p_fail") + ": ") + (j.text || "");
    };
    d.querySelector(".edit").onclick = () => openProviderForm(p);
    d.querySelector(".del").onclick = async () => {
      if (!confirm(t("p_delete") + " " + p.name + "?")) return;
      try { S = await postJson("/api/providers", {delete: p.id}); applySettings(); $("#smsg").textContent = t("p_deleted"); } catch (e) { $("#smsg").textContent = t("error") + e.message; }
    };
    el.append(d);
  }
  for (const sel of $$("select[data-task]")) {
    sel.innerHTML = ("optional" in sel.dataset ? `<option value="">${esc(t("t_check_none"))}</option>` : "") +
      (S.providers || []).map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    sel.value = S.tasks?.[sel.dataset.task] ?? "";
    sel.onchange = () => setSetting({tasks: {[sel.dataset.task]: sel.value}});
  }
}
function providerRows() {
  const ty = $("#pType").value;
  for (const el of $$("#pform [data-for]")) el.classList.toggle("show", el.dataset.for.split(" ").includes(ty));
  $("#ptypehint").textContent = ty === "claude-code" ? t("p_type_hint_claude") : ty === "cli" ? t("p_type_hint_cli") : "";
}
function openProviderForm(p) {
  $("#pform").hidden = false; $("#pmsg").textContent = "";
  $("#pId").value = p?.id || ""; $("#pName").value = p?.name || ""; $("#pType").value = p?.type || "openai-compatible"; $("#pModel").value = p?.model || "";
  $("#pBase").value = p?.base_url || ""; $("#pCmd").value = p?.cmd || ""; $("#pTemp").value = p?.temperature ?? ""; $("#pMax").value = p?.max_tokens ?? "";
  $("#pKey").value = ""; $("#pClearKey").checked = false; $("#pKeyMasked").textContent = p?.key_masked || "";
  providerRows(); $("#pName").focus();
}
$("#pType").onchange = providerRows;
$("#bPAdd").onclick = () => openProviderForm(null);
$("#bPCancel").onclick = () => { $("#pform").hidden = true; };
for (const [name, [base, model]] of Object.entries(TEMPLATES)) {
  const b = document.createElement("button"); b.type = "button"; b.textContent = name;
  b.onclick = () => { $("#pBase").value = base; if (!$("#pModel").value) $("#pModel").value = model; if (!$("#pName").value) $("#pName").value = name; };
  $("#ptemplates").append(b);
}
$("#pform").onsubmit = async e => {
  e.preventDefault();
  const body = {provider: {id: $("#pId").value, name: $("#pName").value, type: $("#pType").value, model: $("#pModel").value, base_url: $("#pBase").value, cmd: $("#pCmd").value,
    temperature: $("#pTemp").value, max_tokens: $("#pMax").value}, key: $("#pKey").value, clear_key: $("#pClearKey").checked};
  try { S = await postJson("/api/providers", body); $("#pKey").value = ""; $("#pform").hidden = true; applySettings(); $("#smsg").textContent = t("p_saved"); }
  catch (err) { $("#pmsg").textContent = t("error") + err.message; }
};

// ---------- Archive ----------
function setShowArchive(v) {
  showArchive = v;
  try { localStorage.setItem("rv-archive", v ? "1" : "0"); } catch {}
  const c = cur; renderList(); if (c) { sel = Math.max(0, ordered().indexOf(c)); renderList(); } header();
}
$("#bArchive").onclick = e => { e.stopPropagation(); setShowArchive(!showArchive); };
function toggleArchiveLetter(it = cur) {
  if (!it) return;
  const to = it.status === "archived" ? "open" : "archived";
  if (to === "archived" && !showArchive && cur?.path === it.path) { const o = ordered(), i = o.indexOf(it); setStatus(to).then(() => go(Math.min(i, ordered().length - 1))); return; }
  (cur?.path === it.path ? setStatus(to) : setStatusFor(it, to)).catch(e => msg(t("error") + e.message, true));
}
$("#bArchiveM").onclick = () => toggleArchiveLetter();
async function togglePin(it = cur) {
  if (!it) return;
  if (cur?.path === it.path && (dirtyEd || dirtyFb)) await save();
  await post({path: it.path, pinned: !it.pinned});
  msg(`${it.company}: ${t(it.pinned ? "unpinned" : "pinned")}`);
  await refresh();
}
$("#bPinM").onclick = () => togglePin().catch(e => msg(t("error") + e.message, true));
$("#bCheckM").onclick = () => checkPosting(cur?.path);

// ---------- New application from a link or a pasted posting ----------
const overlayClose = id => { $(id).classList.remove("on"); if (view === "letters") focusList(); };
for (const id of ["#newapp", "#versions", "#followup"]) {
  $(id).onclick = e => { if (e.target === $(id) || e.target.closest(".close")) overlayClose(id); };
  $(id).addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); overlayClose(id); } });
}
function openNewApp() {
  if (view !== "letters") setView("letters");
  $("#newForm").reset(); $("#newErr").textContent = "";
  $("#newapp").classList.add("on"); $("#newForm").url.focus();
}
$("#bNew").onclick = e => { e.stopPropagation(); openNewApp(); };
$("#newForm").onsubmit = async e => {
  e.preventDefault();
  const f = e.target, job = {url: f.url.value.trim(), text: f.text.value.trim(), company: f.company.value.trim(), title: f.title.value.trim(), tone: f.tone.value};
  if (!job.url && !job.text) { $("#newErr").textContent = t("new_need"); return; }
  let r;
  try { r = await postJson("/api/search/draft", {job}); } catch (err) { $("#newErr").textContent = err.message; return; }
  $("#newapp").classList.remove("on");
  const done = runningToast(`${job.company || job.url}: ${t("new_running")}`, "draft", r.key);
  const j = await waitJob("draft", r.key);
  done();
  if (j.state === "done") {
    msg(t("new_done") + j.path); await refresh();
    const i = ordered().findIndex(x => x.path === j.path); if (i >= 0) go(i);
  } else if (j.state !== "cancelled") msg(t("error") + j.text, true);
};

// ---------- Earlier versions of a letter (backups before AI rounds and of manual saves) ----------
let versionPick = null;
function diffHtml(a, b) {
  const d = diffLines(a, b), keep = new Set();
  d.forEach((l, i) => { if (l[0] !== " ") for (let k = i - 2; k <= i + 2; k++) keep.add(k); });
  let html = "", gap = false;
  d.forEach((l, i) => {
    if (!keep.has(i)) { if (!gap) html += `<div class="skip">…</div>`; gap = true; return; }
    gap = false; html += `<div class="${l[0] === "-" ? "del" : l[0] === "+" ? "add" : ""}">${l[0]} ${esc(l[1]) || " "}</div>`;
  });
  return keep.size ? html : `<div class="skip">${t("diff_none")}</div>`;
}
async function openVersions() {
  if (!cur) return;
  const list = await api("/api/versions?path=" + encodeURIComponent(cur.path)).catch(e => (msg(t("error") + e.message, true), null));
  if (!list) return;
  versionPick = null; $("#bVRestore").disabled = true;
  $("#vDiff").innerHTML = list.length ? "" : `<div class="skip">${t("versions_none")}</div>`;
  const el = $("#vList"); el.innerHTML = "";
  for (const v of list) {
    const b = document.createElement("button");
    b.textContent = new Date(v.ts).toLocaleString(locale(), {dateStyle: "short", timeStyle: "short"});
    b.onclick = async () => {
      const r = await api(`/api/version?path=${encodeURIComponent(cur.path)}&name=${encodeURIComponent(v.name)}`).catch(e => (msg(t("error") + e.message, true), null));
      if (!r) return;
      for (const x of el.children) x.classList.toggle("on", x === b);
      versionPick = r.content; $("#vDiff").innerHTML = diffHtml(r.content, ed.getValue()); $("#bVRestore").disabled = false;
    };
    el.append(b);
  }
  $("#versions").classList.add("on");
  el.firstElementChild?.click(); el.firstElementChild?.focus();
}
$("#bVersions").onclick = openVersions;
$("#bVRestore").onclick = () => {
  if (versionPick == null || !cur) return;
  ed.setValue(versionPick);  // a normal change: dirty, undo with u, saved with :w
  $("#versions").classList.remove("on"); msg(t("versions_taken")); focusEd();
};
V.defineEx("versions", "ver", openVersions);

// ---------- Session: where the user is right now (view, letter, cursor, filters, dossier ...) ----------
// Per browser in localStorage; the layout is also stored on the server as before.
const SESSION_KEY = "rv-session";
const OLD_VIEWS = {bewerbungen: "letters", wissen: "knowledge", profil: "profile"};  // view ids before the English rename
let session = {}, sessionLast = "";
try { session = JSON.parse(localStorage.getItem(SESSION_KEY) || localStorage.getItem("rv-sitzung")) || {}; } catch {}
session.pos ||= {};
// take the scroll position only from a visible editor: a hidden one reports 0
const cmPos = (cm, prev) => { const c = cm.getCursor(); return [c.line, c.ch, cm.getWrapperElement().offsetParent ? Math.round(cm.getScrollInfo().top) : prev?.[2] ?? -1]; };
function savePos() {
  if (!cur) return;
  const prev = session.pos[cur.path] || {};
  session.pos[cur.path] = {ed: cmPos(ed, prev.ed), fb: cmPos(fb, prev.fb), t: Date.now()};
  const old = Object.entries(session.pos).sort((a, b) => b[1].t - a[1].t).slice(80);  // ponytail: only the last 80 letters
  for (const [k] of old) delete session.pos[k];
}
let pendingScroll = null;  // scroll position for an editor that was still hidden when it was set
function restorePos(path) {
  const p = session.pos[path];
  if (!p) return;
  for (const [cm, [line, ch]] of [[ed, p.ed], [fb, p.fb]]) cm.setCursor(Math.min(line, cm.lastLine()), ch);
  pendingScroll = [p.ed[2], p.fb[2]];
  if (view === "letters") applyPendingScroll();
}
function applyPendingScroll() {
  const s = pendingScroll;
  if (!s) return;
  pendingScroll = null;
  // after the scrolling of cursorActivity; without a stored position (-1) just show the cursor
  setTimeout(() => {
    for (const [cm, top] of [[ed, s[0]], [fb, s[1]]]) {
      if (top >= 0) cm.scrollTo(null, top);
      const c = cm.cursorCoords(null, "local"), i = cm.getScrollInfo();  // the cursor has to be visible in the end
      if (top < 0 || c.top < i.top || c.bottom > i.top + i.clientHeight) cm.scrollIntoView(null, Math.min(80, i.clientHeight / 3));
    }
  });
}
function saveSession() {
  savePos();
  const z = {view, cur: cur?.path || "", active: activePanel, filter: search.value,
    jrun, jpane: $("#jseg button.on")?.id || "", jtext: $("#jText").value, jnotes: [...jnotes], jnew: $("#jOnlyNew").checked,
    jhidden: $("#jShowHidden").checked, jsort: $("#jSort").value,
    pview: typeof pview !== "undefined" ? pview : "next", dossier: typeof dossier !== "undefined" && dossier?.path || "", tbq: typeof dossier !== "undefined" && dossier?.tb?.q || "",
    wsec: $("#wseg button.on")?.dataset.wsec || "", ssec: $("#snav button.on")?.dataset.sec || "",
    psec: $("#prnav button.on")?.dataset.psec || "", prfile: typeof prFile !== "undefined" ? prFile : "", pos: session.pos};
  const str = JSON.stringify(z);
  if (str !== sessionLast) { sessionLast = str; try { localStorage.setItem(SESSION_KEY, str); localStorage.removeItem("rv-sitzung"); } catch {} }
}
setInterval(saveSession, 1500);
addEventListener("pagehide", saveSession);
document.addEventListener("visibilitychange", () => { if (document.hidden) saveSession(); });

async function restoreSession() {
  const z = session;
  if (z.filter) { search.value = z.filter; search.classList.add("on"); filter = z.filter.trim().toLowerCase(); }
  const o = ordered(), i = o.findIndex(x => x.path === z.cur);
  if (o.length) { sel = Math.max(0, i); renderList(); await open(o[sel].path).catch(() => {}); }
  // job search: set the filters before loading so the cards appear right away
  if (z.jtext != null) $("#jText").value = z.jtext;
  if (z.jsort) $("#jSort").value = {wertung: "score", firma: "company", datei: "file"}[z.jsort] || z.jsort;
  $("#jOnlyNew").checked = !!(z.jnew ?? z.jneu); $("#jShowHidden").checked = !!(z.jhidden ?? z.jaus);
  if (Array.isArray(z.jnotes)) { jnotes.clear(); for (const n of z.jnotes) jnotes.add(n); for (const b of $$(".jbar .notes button")) b.classList.toggle("on", jnotes.has(b.dataset.note)); }
  if (z.wsec) $(`#wseg button[data-wsec="${z.wsec}"]`)?.click();
  if (z.ssec) showSec(z.ssec);
  const psec = {angaben: "details", dokumente: "documents", vorlagen: "templates"}[z.psec] || z.psec;
  if (psec) for (const x of $$("#prnav button")) x.classList.toggle("on", x.dataset.psec === psec);
  if (z.prfile || z.prdatei) prFile = z.prfile || z.prdatei;
  const zv = OLD_VIEWS[z.view] || z.view;
  const v = VIEWS[zv] ? zv : S.tab === "jobs" ? "jobs" : "letters";
  setView(v, false);
  if (v === "jobs") {
    await loadRuns(z.jrun).catch(() => {});
    if (z.jpane) $("#" + z.jpane)?.click();
  } else if (v === "pipeline") {
    pview = ["board", "agenda", "stats"].includes(z.pview) ? z.pview : "next";
    await loadPipe().catch(() => {});
    const path = z.dossier || z.akte;
    if (path && pitems.some(x => x.path === path)) {
      await openDossier(path);
      if (z.tbq) { dossier.tb.q = z.tbq; $("#tbQ").value = z.tbq; tbSearch(); }
    }
  } else if (v === "letters") {
    ({editor: focusEd, feedback: focusFb}[z.active || z.aktiv] || focusList)();
  }
}

// ---------- Start ----------
(async () => {
  try { S = await api("/api/settings"); } catch (e) { msg(t("no_server") + e.message, true); }
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem("rv-layout")); } catch {}
  L = sanitizeLayout(S.layout || cached);
  applySettings(); setLive(live); applyLayout(); setListMode(boardMode);
  const setupInfo = await api("/api/setup").catch(() => ({needed: false}));
  if (setupInfo.needed) { showSetup(setupInfo); return; }  // first start: no data folder yet
  await refresh();
  await restoreSession();  // view, letter, cursor, filters and dossier from last time
  if (S.update_check) setTimeout(() => checkUpdate(false), 3000);
  let first = false;
  try { first = localStorage.getItem("rv-first-start") === "1"; localStorage.removeItem("rv-first-start"); } catch {}
  if (first) { setView("profile"); profileSection("details"); msg(t("su_welcome")); } else msg(t("hint"));
})().catch(e => msg(t("no_server") + e.message, true));
