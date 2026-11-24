// ---------- Pipeline: everything after sending (phase board, dossier per application, agenda) ----------
// Uses the helpers from app.js ($, $$, t, api, postJson, msg, esc, md, setView, open, items).
// Phase ids and event types ("Mail rein", "Termin", ...) are the stored values of review.json and Verlauf_*.md.
const PHASES = ["abgeschickt", "keine_antwort", "antwort", "gespraech", "angebot", "absage"];  // order = progress, as in pipeline.py
const TYPES = ["Mail rein", "Mail raus", "Termin", "Frist", "Notiz", "Status"];
const DATE_TYPES = ["Termin", "Frist"];
const STALE_DAYS = 14;  // this long without activity, then a hint to follow up
let pitems = [], dossier = null, pview = "next", nextLetters = [], latestRun = null, mailRetry = 0;
const prepRunning = new Set();

const todayIso = () => new Date().toLocaleDateString("sv-SE");  // YYYY-MM-DD in local time
const daysSince = iso => iso ? Math.floor((new Date(todayIso()) - new Date(iso)) / 864e5) : 0;
const fmtDate = (iso, time) => { const [y, m, d] = iso.split("-"); return (S.lang === "en" ? `${y}-${m}-${d}` : `${+d}.${+m}.${y}`) + (time ? " " + time : ""); };
const shortDate = iso => { const [y, m, d] = iso.split("-"); const year = y === todayIso().slice(0, 4) ? "" : y; return S.lang === "en" ? `${m}-${d}` + (year && " " + year) : `${+d}.${+m}.` + year; };
// how long ago the last mail was (any direction), or the sending when there is none yet: green under a week, orange up to STALE_DAYS, red after
function mailAge(it) {
  const iso = it.last_mail || it.since;
  if (!iso) return "";
  const n = daysSince(iso), cls = n < 7 ? "ok" : n < STALE_DAYS ? "warn" : "err";
  const when = n === 0 ? t("p_today") : n === 1 ? t("p_yesterday") : t("p_days_ago").replace("{n}", n);
  return `<span class="mailage ${cls}" title="${fmtDate(iso)}">${it.last_mail ? t("p_last_mail") : t("p_no_mail_yet")} ${when}</span>`;
}
// one application (letters menu or dossier): the result is kept on the letter and the pipeline item and shown as a toast
async function checkPosting(path) {
  if (!path) return;
  msg(t("pc_running"));
  let r;
  try { r = await postJson("/api/posting/check", {path}); } catch (e) { msg(t("error") + e.message, true); r = null; }
  if (r) {
    for (const i of [...pitems, ...(typeof items !== "undefined" ? items : [])]) if (i.path === path) i.check = r;
    if (r.status === "unknown") msg(t("pc_unknown") + (r.grund ? ` (${r.grund})` : ""), true);
    else msg(t(r.status === "gone" ? "pc_gone" : "pc_open") + (r.grund ? ` (${r.grund})` : ""), r.status === "gone");
  }
  if (dossier?.path === path) renderDossier();
  if (typeof cur !== "undefined" && cur?.path === path) header();
}
const typeKey = type => "pt_" + type.toLowerCase().replace(" ", "_");

async function loadPipe() {
  // the letters and the newest job search run are fetched fresh too, so "Next" does not show an old state
  const [p, l, runs] = await Promise.all([api("/api/pipeline"), api("/api/letters").catch(() => nextLetters), api("/api/search/runs").catch(() => [])]);
  pitems = p; nextLetters = l;
  latestRun = runs[0] && daysSince(runs[0].date.slice(0, 10)) <= 1 ? await api("/api/search/run?name=" + encodeURIComponent(runs[0].name)).catch(() => null) : null;
  renderPipe();
  // the mail index is still being built the first time: ask again shortly
  if (pitems.some(i => i.mails === null) && mailRetry++ < 10) setTimeout(() => { if (view === "pipeline") loadPipe().catch(() => {}); }, 4000);
  if (dossier) { const it = pitems.find(i => i.path === dossier.path); if (it) dossier.item = it; else closeDossier(); }
}

function renderPipe() {
  $("#pCount").textContent = `${pitems.length} ${t(pitems.length === 1 ? "p_count_1" : "p_count")}`;
  $("#pempty").hidden = pitems.length > 0 || pview === "next";
  $("#pboard").hidden = pview !== "board" || !pitems.length; $("#pagenda").hidden = pview !== "agenda" || !pitems.length;
  $("#pstats").hidden = pview !== "stats" || !pitems.length; $("#pnext").hidden = pview !== "next";
  for (const b of $$("#pseg button")) b.classList.toggle("on", b.dataset.pv === pview);
  ({next: renderNext, board: renderPBoard, agenda: renderAgenda, stats: renderStats}[pview] || renderNext)();
}

function pChips(it) {
  const stale = daysSince(it.last);
  return (it.score ? `<i class="score ${it.note || ""}">${it.score}/10</i>` : "") +
    (it.mails?.length ? `<i class="run" title="${t("mm_chip")}">✉ ${it.mails.length}${mailPhase(it) ? " · " + t("ph_" + mailPhase(it)) + "?" : ""}</i>` : "") +
    (it.next ? `<i class="run" title="${esc(it.next.title)}">${t(typeKey(it.next.type))} ${shortDate(it.next.date)}${it.next.time ? " " + it.next.time : ""}</i>` : "") +
    (stale >= STALE_DAYS && ["abgeschickt", "antwort"].includes(it.phase) ? `<i class="fb" title="${t("p_stale_hint")}">${stale} ${t("p_stale")}</i>` : "");
}

// the furthest phase the new mails of an application suggest, when it is further than the current one
const mailPhase = it => (it.mails || []).map(m => m.phase).filter(ph => PHASES.indexOf(ph) > PHASES.indexOf(it.phase))
  .sort((a, b) => PHASES.indexOf(b) - PHASES.indexOf(a))[0] || "";

function renderPBoard() {
  const bd = $("#pboard"); bd.innerHTML = "";
  for (const ph of PHASES) {
    const g = pitems.filter(i => i.phase === ph).sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const col = document.createElement("div");
    col.className = "bcol"; col.dataset.phase = ph;
    col.innerHTML = `<h2>${t("ph_" + ph)}<span>${g.length}</span></h2>`;
    for (const it of g) {
      const d = document.createElement("div");
      d.className = "bcard" + (dossier?.path === it.path ? " sel" : "");
      d.innerHTML = `<b>${esc(it.company)}</b><small>${esc(it.title || "")}</small><div class="chips">${pChips(it)}</div>`;
      d.onclick = () => openDossier(it.path);
      d.draggable = true;
      d.ondragstart = e => { e.dataTransfer.setData("text/plain", it.path); d.classList.add("dragging"); };
      d.ondragend = () => d.classList.remove("dragging");
      col.append(d);
    }
    col.ondragover = e => { e.preventDefault(); col.classList.add("over"); };
    col.ondragleave = () => col.classList.remove("over");
    col.ondrop = e => { e.preventDefault(); col.classList.remove("over"); setPhase(e.dataTransfer.getData("text/plain"), ph); };
    bd.append(col);
  }
}

function allDates() {
  return pitems.flatMap(it => it.dates.map(e => ({...e, company: it.company, path: it.path})))
    .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
}

function renderAgenda() {
  const el = $("#pagenda"), today = todayIso(), all = allDates();
  const upcoming = all.filter(e => e.date >= today), past = all.filter(e => e.date < today).reverse();
  const row = e => `<div class="arow${e.date === today ? " today" : ""}" data-path="${esc(e.path)}"><span class="when">${fmtDate(e.date, e.time)}</span>` +
    `<span class="type ${e.type === "Frist" ? "deadline" : ""}">${t(typeKey(e.type))}</span><b>${esc(e.company)}</b><span>${esc(e.title)}</span></div>`;
  el.innerHTML = `<h3>${t("p_upcoming")}</h3>` + (upcoming.map(row).join("") || `<p class="muted">${t("p_no_dates")}</p>`) +
    (past.length ? `<details><summary>${t("p_past")} (${past.length})</summary>${past.map(row).join("")}</details>` : "");
  for (const r of el.querySelectorAll(".arow")) r.onclick = () => openDossier(r.dataset.path);
}

// ---------- Next: what needs doing, from the pipeline and the letters ----------
function renderNext() {
  const el = $("#pnext"), today = todayIso(), week = new Date(Date.now() + 7 * 864e5).toLocaleDateString("sv-SE");
  const letters = nextLetters.length ? nextLetters : typeof items !== "undefined" ? items : [];
  const row = (attr, when, type, company, text, btn = "") => `<div class="arow" ${attr}><span class="when">${when}</span><span class="type">${type}</span>` +
    `<b>${esc(company)}</b><span>${esc(text || "")}${btn}</span></div>`;
  const dp = p => `data-path="${esc(p)}"`, dl = p => `data-letter="${esc(p)}"`;
  const follow = pitems.filter(i => ["abgeschickt", "antwort"].includes(i.phase) && !i.next && daysSince(i.last) >= STALE_DAYS);
  const groups = [
    [t("nx_mails"), pitems.filter(i => i.mails?.length).map(i => row(dp(i.path), `✉ ${i.mails.length}`, mailPhase(i) ? t("ph_" + mailPhase(i)) + "?" : t("mm_chip"), i.company, i.mails[0].subject))],
    [t("nx_jobs"), latestRun ? [`<div class="arow" data-run="${esc(latestRun.name)}"><span class="when">${fmtDate(latestRun.name.replace("Stellensuche_", "").slice(0, 10))}</span>` +
      `<span class="type">${t("tab_jobs")}</span><b>${latestRun.jobs.filter(j => j.new).length} ${t("nx_jobs_new")}</b><span>${latestRun.jobs.length} ${t("nx_jobs_all")}</span></div>`] : []],
    [t("nx_dates"), allDates().filter(e => e.date >= today && e.date <= week).map(e => row(dp(e.path), fmtDate(e.date, e.time), t(typeKey(e.type)), e.company, e.title))],
    [t("nx_follow"), follow.map(i => row(dp(i.path), `${daysSince(i.last)} ${t("p_stale")}`, t("ph_" + i.phase), i.company, i.title,
      ` <button class="nxfu" ${followRunning.has(i.path) ? "disabled" : ""}>${t("fu_btn")}</button>`))],
    [t("nx_final"), letters.filter(i => i.status === "final").map(i => row(dl(i.path), i.since ? fmtDate(i.since) : "", t("st_final"), i.company, i.title,
      ` <button class="nxam" ${appMailRunning.has(i.path) ? "disabled" : ""}>${t("am_btn")}</button>`))],
    [t("nx_feedback"), letters.filter(i => i.status === "offen" && (i.feedback || "").trim()).map(i => row(dl(i.path), "", t("st_offen"), i.company, i.feedback.split("\n")[0].slice(0, 120)))],
  ].filter(([, rows]) => rows.length);
  el.innerHTML = groups.map(([h, rows]) => `<h3>${h} (${rows.length})</h3>${rows.join("")}`).join("") || `<p class="muted">${t("nx_none")}</p>`;
  for (const r of el.querySelectorAll(".arow")) r.onclick = () => {
    if (r.dataset.run) { setView("jobs"); openRun(r.dataset.run).catch(() => {}); }
    else if (r.dataset.path) openDossier(r.dataset.path); else { setView("letters"); open(r.dataset.letter).catch(() => {}); }
  };
  for (const b of el.querySelectorAll(".nxam")) b.onclick = e => { e.stopPropagation(); b.disabled = true; startAppMail(b.closest(".arow").dataset.letter); };
  for (const b of el.querySelectorAll(".nxfu")) b.onclick = e => { e.stopPropagation(); b.disabled = true; startFollowUp(b.closest(".arow").dataset.path); };
}

// A mail can move the application on: offer the phase it suggests when that is further than the current one
function suggestPhase(path, phase) {
  const it = pitems.find(i => i.path === path);
  if (!it || !phase || PHASES.indexOf(phase) <= PHASES.indexOf(it.phase)) return;
  toast(t("mm_phase_q").replace("{c}", it.company).replace("{p}", t("ph_" + phase)), false, {label: t("mm_phase_set"), fn: () => setPhase(path, phase)});
}

async function setPhase(path, phase) {
  const it = pitems.find(i => i.path === path);
  if (!it || it.phase === phase) return;
  try { await postJson("/api/letter", {path, phase}); } catch (e) { msg(t("error") + e.message, true); return; }
  msg(`${it.company}: ${t("ph_" + phase)}`);
  await loadPipe();
  if (dossier?.path === path) await openDossier(path);
}

// ---------- Dossier ----------
async function openDossier(path) {
  const item = pitems.find(i => i.path === path);
  if (!item) return;
  const d = await api("/api/log?path=" + encodeURIComponent(path)).catch(e => (msg(t("error") + e.message, true), null));
  if (!d) return;
  const same = dossier?.path === path;
  const tb = same ? dossier.tb : {q: "", state: "loading", hits: [], total: 0};
  dossier = {path, item, d, edit: {}, tb};
  $("#pipe").classList.add("dossier-open"); $("#dossier").hidden = false;
  renderDossier(); renderPipe();
  if (!same) tbSearch();
}
function closeDossier() {
  dossier = null; $("#dossier").hidden = true; $("#pipe").classList.remove("dossier-open");
  if (view === "pipeline") renderPipe();
}

async function saveDossier(quiet) {
  try {
    const r = await postJson("/api/log", {path: dossier.path, mtime: dossier.d.mtime, events: dossier.d.events, preparation: dossier.d.preparation, notes: dossier.d.notes});
    dossier.d.mtime = r.mtime;
    if (!quiet) msg(t("saved"));
    loadPipe().catch(() => {});
    return true;
  } catch (e) {
    msg(e.status === 409 ? t("p_conflict") : t("error") + e.message, true);
    if (e.status === 409) await openDossier(dossier.path);
    return false;
  }
}

const sortEvents = evs => evs.sort((a, b) => (b.date + b.time).localeCompare(a.date + a.time));  // like the server: newest first, stable

function renderDossier() {
  const {item: it, d} = dossier, f = it.files || {};
  const link = /^https?:\/\//.test(it.link || "") ? it.link : "";
  const ev = d.events.map((e, i) => `<div class="ev ${e.type === "Status" ? "status" : ""}"><div class="evh"><span class="when">${fmtDate(e.date, e.time)}</span>` +
    `<span class="type t-${e.type.replace(" ", "-")}">${t(typeKey(e.type))}</span><b>${esc(e.title)}</b><button class="del" data-i="${i}" title="${t("pa_delete")}"><svg><use href="#i-close"/></svg></button></div>` +
    (e.text ? (e.text.length > 400 ? `<details><summary>${esc(e.text.slice(0, 160))} …</summary><pre>${esc(e.text)}</pre></details>` : `<pre>${esc(e.text)}</pre>`) : "") + `</div>`).join("");
  const sec = (key, label, extra = "") => `<section class="asec${fold(key)}" data-key="${key}" data-fold="${key}"><div class="asech"><h3>${label}</h3>${extra}<button class="edit">${dossier.edit[key] ? t("p_done") : t("pa_edit")}</button></div>` +
    (dossier.edit[key] ? `<textarea rows="14">${esc(d[key])}</textarea>` : `<div class="md">${d[key] ? md(d[key]) : `<p class="muted">${t("p_empty_" + key)}</p>`}</div>`) + `</section>`;
  $("#dossier").innerHTML =
    `<div class="ahead"><div><h2>${esc(it.company)}</h2><small>${esc(it.title || "")}</small>${mailAge(it)}</div>${it.score ? `<i class="score ${it.note || ""}">${it.score}/10</i>` : ""}<button class="x" title="${t("toast_close")}"><svg><use href="#i-close"/></svg></button></div>` +
    `<div class="abar"><select id="aPhase">${PHASES.map(p => `<option value="${p}" ${p === it.phase ? "selected" : ""}>${t("ph_" + p)}</option>`).join("")}</select>` +
    (link ? `<a class="btn" href="${esc(link)}" target="_blank" rel="noopener">${t("link")}</a><button id="aCheck" class="${checkClass(it.check)}" title="${esc(checkTitle(it.check))}">${checkLabel(it.check)}</button>` : "") +
    (f.cl_pdf ? `<a class="btn" href="/file?path=${encodeURIComponent(f.cl_pdf)}" target="_blank" rel="noopener">${t("cl")}</a>` : "") +
    `<button id="aLetter">${t("p_open_letter")}</button><button id="aIcs">${t("p_ics")}</button>` +
    `<button id="aFollow" ${followRunning.has(dossier.path) ? "disabled" : ""}>${followRunning.has(dossier.path) ? t("fu_running") : t("fu_btn")}</button><button id="aArchive">${t("archive")}</button></div>` +
    (it.mails?.length ? `<section class="asec${fold("mails")}" data-fold="mails"><div class="asech"><h3>${t("mm_title")}</h3><span class="muted">${it.mails.length}</span></div>` +
      it.mails.map((r, i) => `<div class="tbrow"><span class="when">${fmtDate(r.date, r.time)}</span><div><b>${esc(r.subject || "")}</b>` +
        `<small>${esc(r.from)} · ${esc(r.folder)}${r.phase && r.phase !== "antwort" ? " · " + t("ph_" + r.phase) : ""}</small></div><button data-mm="${i}">${t("pa_add")}</button></div>`).join("") + `</section>` : "") +
    `<section class="asec${fold("new")}" data-fold="new"><div class="asech"><h3>${t("p_new")}</h3><select id="aSource">${SOURCES.map(q => `<option value="${q}" ${q === source ? "selected" : ""}>${t("q_" + q)}</option>`).join("")}</select></div>` +
    `<div id="qTb" ${source !== "tb" ? "hidden" : ""}><div class="tbbar"><input id="tbQ" type="search" value="${esc(dossier.tb.q)}" placeholder="${t("tb_ph")}" autocomplete="off">` +
    `<button id="tbCompany" type="button" title="${t("tb_company")}">${esc(shortCompany(it.company))}</button></div><div class="muted tbstate" id="tbState"></div><div id="tbList"></div></div>` +
    `<label id="qEml" class="dropzone" ${source !== "eml" ? "hidden" : ""}>${t("q_eml_drop")}<input type="file" accept=".eml,message/rfc822" hidden></label>` +
    `<form id="aForm" class="aform" ${source !== "manual" ? "hidden" : ""}>` +
    `<select name="type">${TYPES.filter(x => x !== "Status").map(x => `<option value="${x}">${t(typeKey(x))}</option>`).join("")}</select>` +
    `<input type="date" name="date" value="${todayIso()}" required><input type="time" name="time"><input name="title" placeholder="${t("p_title_ph")}">` +
    `<textarea name="text" rows="4" placeholder="${t("p_text_ph")}"></textarea>` +
    `<div class="arow2"><button class="primary">${t("pa_add")}</button></div></form></section>` +
    `<section class="asec${fold("timeline")}" data-fold="timeline"><div class="asech"><h3>${t("p_timeline")}</h3><span class="muted">${d.events.length}</span></div><div class="evs">${ev || `<p class="muted">${t("p_no_events")}</p>`}</div></section>` +
    sec("preparation", t("p_prep"), `<button id="aPrep" ${prepRunning.has(dossier.path) ? "disabled" : ""}>${prepRunning.has(dossier.path) ? t("p_prep_running") : t("p_prep_ai")}</button>`) +
    sec("notes", t("p_notes"));

  const A = $("#dossier");
  A.querySelector(".x").onclick = closeDossier;
  $("#aPhase").onchange = e => setPhase(dossier.path, e.target.value);
  $("#aLetter").onclick = () => { const p = dossier.path; setView("letters"); open(p).catch(() => {}); };
  $("#aFollow").onclick = () => startFollowUp(dossier.path);
  $("#aArchive").onclick = async () => {
    const path = dossier.path;
    try { await postJson("/api/letter", {path, status: "archiv"}); } catch (e) { msg(t("error") + e.message, true); return; }
    msg(`${it.company}: ${t("st_archiv")}`); closeDossier(); await loadPipe(); refresh().catch(() => {});
  };
  $("#aIcs").onclick = () => icsDownload(dossier.path);
  if ($("#aCheck")) $("#aCheck").onclick = () => { $("#aCheck").disabled = true; $("#aCheck").textContent = t("pc_running"); checkPosting(dossier.path); };
  $("#aForm").onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    if (!fd.title.trim() && !fd.text.trim()) { msg(t("p_need_text"), true); return; }
    dossier.d.events.unshift({date: fd.date, time: fd.time, type: fd.type, title: fd.title.trim(), text: fd.text.trim()});
    sortEvents(dossier.d.events);
    if (await saveDossier(true)) { msg(t("p_added")); renderDossier(); }
  };
  A.querySelector("#qEml input[type=file]").onchange = e => { const f = e.target.files[0]; if (f) importEml(f); e.target.value = ""; };
  $("#aSource").onchange = e => setSource(e.target.value);
  $("#tbQ").oninput = e => { dossier.tb.q = e.target.value; clearTimeout(tbTimer); tbTimer = setTimeout(tbSearch, 180); };
  $("#tbQ").onkeydown = e => { if (e.key === "Escape" && e.target.value) { e.preventDefault(); e.stopPropagation(); e.target.value = ""; e.target.oninput(e); } };
  $("#tbCompany").onclick = () => { $("#tbQ").value = shortCompany(dossier.item.company); $("#tbQ").oninput({target: $("#tbQ")}); $("#tbQ").focus(); };
  for (const b of A.querySelectorAll(".ev .del")) b.onclick = async () => {
    const [x] = dossier.d.events.splice(+b.dataset.i, 1);
    if (await saveDossier(true)) { renderDossier(); toast(t("pa_deleted"), false, {label: t("p_undo"), fn: async () => { dossier.d.events.push(x); await saveDossier(true); renderDossier(); }}); }
  };
  for (const s of A.querySelectorAll(".asec[data-key]")) {
    const key = s.dataset.key;
    s.querySelector(".edit").onclick = async () => {
      if (dossier.edit[key]) { dossier.d[key] = s.querySelector("textarea").value; await saveDossier(true); }
      dossier.edit[key] = !dossier.edit[key]; renderDossier();
      if (dossier.edit[key]) A.querySelector(`.asec[data-key=${key}] textarea`).focus();
    };
    const ta = s.querySelector("textarea");
    if (ta) ta.onkeydown = e => { if (e.key === "Escape" || ((e.ctrlKey || e.metaKey) && e.key === "s")) { e.preventDefault(); s.querySelector(".edit").click(); } };
  }
  for (const s of A.querySelectorAll(".asec[data-fold]")) {  // a click on the heading folds the section (remembered for all dossiers)
    const h = s.querySelector("h3");
    h.tabIndex = 0; h.title = t("p_fold");
    h.onclick = () => { const k = s.dataset.fold; folded.has(k) ? folded.delete(k) : folded.add(k); s.classList.toggle("folded", folded.has(k));
      try { localStorage.setItem("rv-folded", JSON.stringify([...folded])); } catch {} };
    h.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); h.onclick(); } };
  }
  for (const b of A.querySelectorAll("button[data-mm]")) b.onclick = () => addMail(it.mails[+b.dataset.mm].token, b);
  const prep = $("#aPrep");
  if (prep) prep.onclick = () => startPrep(dossier.path);
  renderTb();
}

let folded = new Set();
try { folded = new Set(JSON.parse(localStorage.getItem("rv-folded") || "[]")); } catch {}
const fold = k => folded.has(k) ? " folded" : "";

// ---------- New entry: choose the source (Thunderbird, .eml, manual) ----------
const SOURCES = ["tb", "eml", "manual"];
let source = "tb", tbTimer = 0, tbSeq = 0;
try { const s = localStorage.getItem("rv-source"); source = SOURCES.includes(s) ? s : "tb"; } catch {}
function setSource(q) {
  source = q;
  try { localStorage.setItem("rv-source", q); } catch {}
  if (!dossier) return;
  $("#aSource").value = q; $("#qTb").hidden = q !== "tb"; $("#qEml").hidden = q !== "eml"; $("#aForm").hidden = q !== "manual";
  if (q === "tb") $("#tbQ").focus();
}

// Mails straight from Thunderbird: the server keeps an index in memory, so it searches on every key press
const shortCompany = f => f.replace(/&\s*Cie\.?|\((.*?)\)|\b(AG|GmbH|gmbh|SA|Sagl|Ltd|Inc|LLC|KG|Schweiz)\b\.?/g, " ").replace(/\s+/g, " ").trim();
async function tbSearch() {
  const a = dossier, n = ++tbSeq;
  if (!a) return;
  a.tb.state = "loading"; renderTbState();
  let r;
  try { r = await api("/api/thunderbird?q=" + encodeURIComponent(a.tb.q.trim())); }
  catch (e) { r = {hits: [], missing: e.message}; }
  if (n !== tbSeq || dossier !== a) return;  // typed on meanwhile or another dossier
  Object.assign(a.tb, {state: r.missing ? "missing" : "ok", hits: r.hits, total: r.total ?? r.hits.length, error: r.missing ? t("tb_missing") : ""});
  renderTb();
}
function renderTbState() {
  const el = $("#tbState");
  if (!el || !dossier) return;
  const {tb} = dossier;
  el.textContent = tb.state === "loading" ? t("tb_loading") : tb.state === "missing" ? tb.error
    : tb.q.trim() ? `${tb.total} ${t("tb_hits")}` + (tb.total > tb.hits.length ? `, ${t("tb_shown")} ${tb.hits.length}` : "") : t("tb_newest");
}
function renderTb() {
  const box = $("#tbList");
  if (!box || !dossier) return;
  renderTbState();
  const {tb, d} = dossier;
  const added = r => d.events.some(e => e.date === r.date && e.time === r.time && e.title === r.subject);
  box.innerHTML = (tb.state === "ok" && !tb.hits.length ? `<p class="muted">${t("tb_none")}</p>` : "") +
    tb.hits.map((r, i) => `<div class="tbrow${added(r) ? " in" : ""}"><span class="when">${fmtDate(r.date, r.time)}</span><div><b>${esc(r.subject || "")}</b><small>${esc(r.from)} · ${esc(r.folder)}</small></div>` +
      (added(r) ? `<span class="muted">${t("tb_in")}</span>` : `<button data-i="${i}">${t("pa_add")}</button>`) + `</div>`).join("");
  for (const b of box.querySelectorAll("button[data-i]")) b.onclick = () => addMail(tb.hits[+b.dataset.i].token, b);
}

// Add a mail from the Thunderbird index to the log of the open dossier
async function addMail(token, b) {
  b.disabled = true;
  try {
    const {phase, ...m} = await postJson("/api/eml", {token});
    dossier.d.events.push(m);
    sortEvents(dossier.d.events);
    if (!await saveDossier(true)) { b.disabled = false; return; }
    msg(t("p_added"));
    dossier.item.mails = (dossier.item.mails || []).filter(x => x.token !== token);
    renderDossier(); suggestPhase(dossier.path, phase);
  } catch (e) { msg(t("error") + e.message, true); b.disabled = false; }
}

async function importEml(file) {
  if (!dossier) return;
  try {
    const m = await postJson("/api/eml", {raw: await file.text()});
    setSource("manual");
    const form = $("#aForm");
    for (const k of ["type", "date", "time", "title", "text"]) form[k].value = m[k];
    msg(t("p_eml_loaded")); suggestPhase(dossier.path, m.phase);
  } catch (e) { msg(t("error") + e.message, true); }
}

async function startPrep(path) {
  if (dossier?.path === path && dossier.edit.preparation) {
    dossier.d.preparation = $("#dossier .asec[data-key=preparation] textarea").value; dossier.edit.preparation = false;
    if (!await saveDossier(true)) return;
  }
  try { await postJson("/api/prepare", {path}); } catch (e) { msg(t("error") + e.message, true); return; }
  prepRunning.add(path); if (dossier?.path === path) renderDossier();
  const done = runningToast(t("p_prep_started"), "prepare", path);
  const j = await waitJob("prepare", path, 2500);
  done(); prepRunning.delete(path);
  const company = pitems.find(i => i.path === path)?.company || "";
  if (j.state === "done") msg(`${company}: ${t("p_prep_done")}`); else if (j.state !== "cancelled") msg(`${company}: ${j.text || t("unknown")}`, true);
  if (dossier?.path === path) await openDossier(path);
}

// ---------- Follow-up mail ----------
const followRunning = new Set();
async function startFollowUp(path) {
  try { await postJson("/api/followup", {path}); } catch (e) { msg(t("error") + e.message, true); return; }
  followRunning.add(path); if (dossier?.path === path) renderDossier();
  const done = runningToast(t("fu_running"), "followup", path);
  const j = await waitJob("followup", path, 2000);
  done(); followRunning.delete(path); if (dossier?.path === path) renderDossier();
  if (j.state === "done") showFollowUp(path, j); else if (j.state !== "cancelled") msg(t("error") + (j.text || t("unknown")), true);
}
// Application mail: the e-mail to send a final application with, in the same sheet
const appMailRunning = new Set();
async function startAppMail(path) {
  try { await postJson("/api/appmail", {path}); } catch (e) { msg(t("error") + e.message, true); return; }
  appMailRunning.add(path);
  const done = runningToast(t("am_running"), "appmail", path);
  const j = await waitJob("appmail", path, 2000);
  done(); appMailRunning.delete(path); if (view === "pipeline" && pview === "next") renderPipe();
  if (j.state === "done") showFollowUp(path, j, true); else if (j.state !== "cancelled") msg(t("error") + (j.text || t("unknown")), true);
}
function showFollowUp(path, j, application = false) {
  $("#fuTitle").textContent = t(application ? "am_title" : "fu_title");
  $("#fuNote").hidden = application;  // a letter that is not sent yet has no log to note it in
  $("#fuFiles").hidden = !application;
  $("#fuFiles").textContent = application ? (j.attachments?.length ? `${t("am_attach")}: ${j.attachments.join(", ")}` : t("am_no_files")) : "";
  $("#fuTo").value = j.to || ""; $("#fuSubject").value = j.subject || ""; $("#fuBody").value = j.body || "";
  const mailto = () => `mailto:${encodeURIComponent($("#fuTo").value.trim())}?subject=${encodeURIComponent($("#fuSubject").value)}&body=${encodeURIComponent($("#fuBody").value)}`;
  $("#fuMail").href = mailto();
  for (const id of ["#fuTo", "#fuSubject", "#fuBody"]) $(id).oninput = () => { $("#fuMail").href = mailto(); };
  $("#fuTb").hidden = !S.thunderbird;
  $("#fuTb").textContent = t(application && j.attachments?.length ? "fu_tb_files" : "fu_tb");
  $("#fuTb").onclick = async () => {
    try {
      const r = await postJson("/api/compose", {path, to: $("#fuTo").value.trim(), subject: $("#fuSubject").value, body: $("#fuBody").value, attach: application});
      msg(t("fu_tb_done") + (r.attached.length ? ` (${r.attached.join(", ")})` : ""));
    } catch (e) { msg(t("error") + e.message, true); }
  };
  $("#fuCopy").onclick = () => navigator.clipboard.writeText(`${$("#fuSubject").value}\n\n${$("#fuBody").value}`).then(() => msg(t("copied")), e => msg(t("error") + e.message, true));
  $("#fuNote").onclick = async () => {
    if (dossier?.path !== path) await openDossier(path);
    dossier.d.events.unshift({date: todayIso(), time: "", type: "Notiz", title: $("#fuSubject").value || t("fu_title"), text: $("#fuBody").value});
    sortEvents(dossier.d.events);
    if (await saveDossier(true)) { msg(t("fu_saved")); renderDossier(); $("#followup").classList.remove("on"); }
  };
  $("#followup").classList.add("on"); $("#fuBody").focus();
}

// ---------- Statistics ----------
function renderStats() {
  const el = $("#pstats"), all = pitems, n = all.length;
  if (!n) { el.innerHTML = `<p class="muted">${t("st_none")}</p>`; return; }
  const replied = all.filter(i => !["abgeschickt", "keine_antwort"].includes(i.phase) || i.first_reply), interview = all.filter(i => ["gespraech", "angebot"].includes(i.phase));
  const offer = all.filter(i => i.phase === "angebot"), rejected = all.filter(i => i.phase === "absage");
  const days = all.filter(i => i.first_reply && i.since).map(i => daysSince(i.since) - daysSince(i.first_reply)).filter(d => d >= 0).sort((a, b) => a - b);
  const median = days.length ? days[Math.floor(days.length / 2)] : null;
  const pct = k => Math.round(k / n * 100) + " %";
  const tile = (v, label) => `<div class="stat"><b>${v}</b><span>${label}</span></div>`;
  const bar = (label, k) => `<span>${label}</span><i style="width:${k ? Math.max(1, k / n * 100) : 0}%"></i><span>${k}</span>`;
  const notes = ["A", "B", "C", ""].map(g => [g, all.filter(i => (i.note || "") === g)]).filter(([, l]) => l.length);
  el.innerHTML = `<div class="stgrid">${tile(n, t("st_sent"))}${tile(pct(replied.length), t("st_replied"))}${tile(pct(interview.length), t("st_interview"))}` +
    `${tile(offer.length, t("st_offer"))}${tile(rejected.length, t("st_rejected"))}${tile(median ?? "–", t("st_reply_days"))}</div>` +
    `<h3>${t("st_funnel")}</h3><div class="funnel">${bar(t("st_sent"), n)}${bar(t("st_replied"), replied.length)}${bar(t("st_interview"), interview.length)}${bar(t("st_offer"), offer.length)}</div>` +
    `<h3>${t("st_by_note")}</h3><table class="sttab"><tr><th>${t("st_note")}</th><th>${t("st_sent")}</th><th>${t("st_replied")}</th><th>${t("st_interview")}</th></tr>` +
    notes.map(([g, l]) => `<tr><td>${g || t("st_no_note")}</td><td>${l.length}</td><td>${l.filter(i => replied.includes(i)).length}</td><td>${l.filter(i => interview.includes(i)).length}</td></tr>`).join("") + `</table>`;
}

// ---------- Reminders: browser notifications for today's and tomorrow's dates while the board is open ----------
const NOTIFIED = "rv-notified";
async function checkReminders() {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!pitems.length) pitems = await api("/api/pipeline").catch(() => []);
  const today = todayIso(), tomorrow = new Date(Date.now() + 864e5).toLocaleDateString("sv-SE");
  let seen = [];
  try { seen = JSON.parse(localStorage.getItem(NOTIFIED)) || []; } catch {}
  for (const e of allDates().filter(e => e.date === today || e.date === tomorrow)) {
    const id = `${e.path}|${e.date}|${e.time}|${e.title}|${e.date === today}`;
    if (seen.includes(id)) continue;
    new Notification(`${t(e.date === today ? "notify_today" : "notify_tomorrow")}${e.time ? " " + e.time : ""}: ${t(typeKey(e.type))}`, {body: `${e.company}${e.title ? ", " + e.title : ""}`, tag: id});
    seen.push(id);
  }
  try { localStorage.setItem(NOTIFIED, JSON.stringify(seen.slice(-200))); } catch {}
}
$("#pNotify").onclick = async () => {
  if (!("Notification" in window)) { msg(t("notify_denied"), true); return; }
  const p = await Notification.requestPermission();
  if (p === "granted") { msg(t("notify_on")); checkReminders(); } else msg(t("notify_denied"), true);
};
setInterval(checkReminders, 10 * 60 * 1000);
setTimeout(checkReminders, 5000);

// ---------- Calendar export (.ics) ----------
function icsDownload(path, upcoming) {
  location.href = "/calendar.ics?download=1" + (path ? "&path=" + encodeURIComponent(path) : "") + (upcoming ? "&upcoming=1" : "");
}
// ---------- Wiring ----------
for (const b of $$("#pseg button")) b.onclick = () => { pview = b.dataset.pv; renderPipe(); };
$("#pIcsAll").onclick = () => icsDownload("", true);
// a calendar app on this computer can subscribe to the board's calendar and stays up to date while the board runs
$("#pSub").onclick = () => { const url = location.origin + "/calendar.ics"; navigator.clipboard.writeText(url).then(() => msg(t("p_subscribe_done").replace("{url}", url)), () => msg(url)); };
$("#pReload").onclick = () => loadPipe().then(() => msg(t("list_reloaded")));
$("#pempty button").onclick = () => setView("letters");
// drop an .eml anywhere on the dossier
$("#dossier").ondragover = e => { if (dossier && [...e.dataTransfer.items].some(i => i.kind === "file")) { e.preventDefault(); $("#dossier").classList.add("over"); } };
$("#dossier").ondragleave = e => { if (!$("#dossier").contains(e.relatedTarget)) $("#dossier").classList.remove("over"); };
$("#dossier").ondrop = e => { const f = e.dataTransfer.files[0]; $("#dossier").classList.remove("over"); if (f) { e.preventDefault(); importEml(f); } };
function pipeKey(k) {
  if (k === "Escape" && dossier) { closeDossier(); return true; }
  if (k === "r") { loadPipe(); return true; }
  if (k === "a") { pview = pview === "board" ? "agenda" : "board"; renderPipe(); return true; }
  return false;
}
