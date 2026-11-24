// ---------- Profile: details, documents, profile text, templates ----------
// Uses the helpers from app.js ($, $$, t, api, postJson, msg, toast, esc, fmtSize, diffLines, MD, S).
// The keys of the profile (name, ort, dateien, dokument_ordner, vorlagen, ...) are the stored format of bewerbungs-board.json.
const AI_BUTTON = {profil: "pr_profil_ai", stil: "pr_stil_ai", lebenslauf: "pr_cv_ai"};  // profile files with an AI suggestion
const TEXT_FIELDS = ["name", "ort", "email", "telefon", "adresse", "foto", "unterschrift"];
const FILE_KEYS = ["profil", "stil", "suchprofil", "bewertung", "lebenslauf"];
let prof = null, profLoaded = false, prFile = "profil", prMtime = "", prDirty = false;
const prEd = CodeMirror($("#prEditor"), {keyMap: "vim", mode: MD, lineWrapping: true, lineNumbers: true});
prEd.on("change", () => { if (!prEd.__loading) { prDirty = true; $("#prDirty").hidden = false; } });

function profileSection(id) {
  for (const b of $$("#prnav button")) b.classList.toggle("on", b.dataset.psec === id);
  for (const s of $$("#profile section[data-psec]")) s.hidden = s.dataset.psec !== id;
  if (id === "text") setTimeout(() => prEd.refresh());
  if (id === "documents") loadDocuments();
  if (id === "templates") loadTemplates();
}
for (const b of $$("#prnav button")) b.onclick = () => profileSection(b.dataset.psec);

async function loadProfile() {
  const d = await api("/api/profile").catch(e => (msg(t("error") + e.message, true), null));
  if (!d) return;
  prof = d.profile; profLoaded = true;
  const f = $("#prForm");
  for (const k of TEXT_FIELDS) f[k].value = prof[k] || "";
  f.regeln.value = (prof.regeln || []).join("\n");
  f.ss_statt_sz.checked = !!prof.ss_statt_sz;
  const rc = prof.regelcheck || {};
  f.rc_min.value = rc.woerter_min || 0; f.rc_max.value = rc.woerter_max || 0; f.rc_chars.value = (rc.zeichen || []).join("\n");
  f.dn_anschreiben.value = prof.dateiname.anschreiben; f.dn_lebenslauf.value = prof.dateiname.lebenslauf;
  for (const k of FILE_KEYS) f["df_" + k].value = prof.dateien[k];
  f.folders.value = (prof.dokument_ordner || []).map(o => o.kategorie && o.kategorie !== o.pfad.split("/").pop() ? `${o.pfad}: ${o.kategorie}` : o.pfad).join("\n");
  for (const k of ["foto", "unterschrift"]) imagePreview(k);
  for (const [k, v] of Object.entries(d.files)) {
    const opt = $(`#prFileSelect option[value="${k}"]`);
    if (opt) opt.textContent = `${t("pr_f_" + k)} · ${v.path}${v.exists ? "" : " (" + t("pr_new") + ")"}`;
  }
  $("#prCategory").innerHTML = d.categories.map(k => `<option value="${k}">${t("kat_" + k)}</option>`).join("");
  if (!prMtime && !prDirty) loadFile(prFile);
}
function imagePreview(k) {
  const v = $("#prForm")[k].value.trim(), img = $(`#pr_${k}_img`);
  img.hidden = !v; if (v) img.src = "/doc?path=" + encodeURIComponent(v) + "&v=" + Date.now();
}
$("#prForm").foto.oninput = () => imagePreview("foto");
$("#prForm").unterschrift.oninput = () => imagePreview("unterschrift");
for (const k of ["foto", "unterschrift"]) $(`#pr_${k}_file`).onchange = async e => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  const rel = await upload(file, "Sonstiges");
  if (rel) { $("#prForm")[k].value = rel; imagePreview(k); }
};

$("#prForm").onsubmit = async e => {
  e.preventDefault();
  const f = e.target;
  const body = {regeln: f.regeln.value.split("\n"), ss_statt_sz: f.ss_statt_sz.checked,
    regelcheck: {woerter_min: +f.rc_min.value || 0, woerter_max: +f.rc_max.value || 0, zeichen: f.rc_chars.value.split("\n").filter(z => z !== "")},
    dateiname: {anschreiben: f.dn_anschreiben.value, lebenslauf: f.dn_lebenslauf.value},
    dateien: Object.fromEntries(FILE_KEYS.map(k => [k, f["df_" + k].value])),
    dokument_ordner: f.folders.value.split("\n").map(z => z.trim()).filter(Boolean).map(z => {
      const i = z.lastIndexOf(":"); return i > 0 ? {pfad: z.slice(0, i).trim(), kategorie: z.slice(i + 1).trim()} : {pfad: z, kategorie: ""};
    })};
  for (const k of TEXT_FIELDS) body[k] = f[k].value;
  try { await postJson("/api/profile", body); msg(t("saved")); prMtime = ""; await loadProfile(); S = await api("/api/settings"); rules(); }
  catch (err) { msg(t("error") + err.message, true); }
};

// ---------- Documents ----------
const readBase64 = file => new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result.split(",")[1]); r.onerror = fail; r.readAsDataURL(file); });
async function upload(file, category) {
  if (file.size > 30 * 1024 * 1024) { msg(t("pr_too_big"), true); return null; }
  try { const r = await postJson("/api/document", {category, name: file.name, data: await readBase64(file)}); return r.rel; }
  catch (e) { msg(t("error") + e.message, true); return null; }
}
async function uploadMany(files) {
  const category = $("#prCategory").value; let n = 0;
  for (const f of files) if (await upload(f, category)) n++;
  if (n) { msg(`${n} ${t("pr_uploaded")}`); loadDocuments(); }
}
$("#prFiles").onchange = e => { uploadMany([...e.target.files]); e.target.value = ""; };
const zone = $("#prDrop");
zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
zone.ondragleave = () => zone.classList.remove("over");
zone.ondrop = e => { e.preventDefault(); zone.classList.remove("over"); uploadMany([...e.dataTransfer.files]); };

async function loadDocuments() {
  const el = $("#prDocList");
  const docs = await api("/api/documents").catch(e => (el.textContent = t("error") + e.message, null));
  if (!docs) return;
  const groups = {};
  for (const d of docs) (groups[d.category] ||= []).push(d);
  $("#prDocCount").textContent = `${docs.length} ${t("pr_documents")}, ${docs.filter(d => d.text).length} ${t("pr_readable")}`;
  el.innerHTML = Object.keys(groups).length ? Object.entries(groups).map(([k, rows]) => `<h3 class="grp">${esc(t("kat_" + k) === "kat_" + k ? k : t("kat_" + k))} (${rows.length})</h3>` +
    `<table class="doctab">` + rows.map(d => `<tr><td><a href="/doc?path=${encodeURIComponent(d.rel)}" target="_blank" rel="noopener">${esc(d.name)}</a>` +
      `<small>${esc(d.folder)}${d.linked ? " · " + t("pr_linked") : ""}</small></td><td class="num">${fmtSize(d.size)}</td><td class="num">${esc(d.mtime)}</td>` +
      `<td class="acts">${d.text ? `<button data-text="${esc(d.rel)}">${t("pr_text")}</button>` : ""}${d.linked ? "" : `<button data-del="${esc(d.rel)}" title="${t("pa_delete")}">✕</button>`}</td></tr>`).join("") + `</table>`).join("")
    : `<p class="muted">${t("pr_no_docs")}</p>`;
  for (const b of el.querySelectorAll("[data-text]")) b.onclick = () => docText(b.dataset.text);
  for (const b of el.querySelectorAll("[data-del]")) b.onclick = () => toast(`${t("pr_really")}: ${b.dataset.del.split("/").pop()}`, true, {label: t("pa_delete"), fn: async () => {
    try { await postJson("/api/document/delete", {rel: b.dataset.del}); msg(t("pa_deleted")); loadDocuments(); } catch (e) { msg(t("error") + e.message, true); }
  }});
}
async function docText(rel) {
  const d = await api("/api/document/text?path=" + encodeURIComponent(rel)).catch(e => (msg(t("error") + e.message, true), null));
  if (!d) return;
  $("#fbhist .sheet").innerHTML = `<div class="shead"><h2>${esc(rel.split("/").pop())}</h2><button class="close">${t("toast_close")}</button></div>` +
    `<p class="hint">${t("pr_text_hint")}</p><pre class="doctext">${esc(d.text) || `<i>${t("pr_no_text")}</i>`}</pre>`;
  $("#fbhist").classList.add("on");
  $("#fbhist .close").onclick = () => $("#fbhist").classList.remove("on");
}

// ---------- Profile text: profil.md, stil.md, suchprofil.md, bewertung.md, lebenslauf.md ----------
async function loadFile(key) {
  if (prDirty && !(await saveFile())) return false;
  const d = await api("/api/profile/file?key=" + key).catch(e => (msg(t("error") + e.message, true), null));
  if (!d) return false;
  prFile = key; prMtime = d.mtime;
  prEd.__loading = true; prEd.setValue(d.content); prEd.clearHistory(); prEd.__loading = false;
  prDirty = false; $("#prDirty").hidden = true; $("#prFileSelect").value = key;
  $("#bPrSuggest").hidden = !AI_BUTTON[key];
  if (AI_BUTTON[key]) $("#bPrSuggest").textContent = t(AI_BUTTON[key]);
  showSuggestion(null);
  const j = await jobState("suggestion", key);
  if (j.state === "running") waitForSuggestion(key);
  return true;
}
async function saveFile() {
  try {
    const r = await postJson("/api/profile/file", {key: prFile, content: prEd.getValue(), mtime: prMtime});
    prMtime = r.mtime; prDirty = false; $("#prDirty").hidden = true; msg(t("saved")); return true;
  } catch (e) { msg(e.status === 409 ? t("conflict") : t("error") + e.message, true); return false; }
}
$("#prFileSelect").onchange = async e => { if (!(await loadFile(e.target.value))) e.target.value = prFile; };
$("#bPrSave").onclick = saveFile;
prEd.setOption("extraKeys", {"Ctrl-S": saveFile, "Cmd-S": saveFile});
{  // :w in the profile editor saves the profile file, otherwise the letter as before
  const saveLetter = CodeMirror.commands.save;
  CodeMirror.commands.save = cm => cm === prEd ? saveFile() : saveLetter(cm);
}

$("#bPrSuggest").onclick = async () => {
  const kind = prFile;
  if (prDirty && !(await saveFile())) return;
  try { await postJson("/api/profile/suggestion", {kind}); } catch (e) { msg(t("error") + e.message, true); return; }
  waitForSuggestion(kind);
};
async function waitForSuggestion(kind) {
  $("#bPrSuggest").disabled = true; $("#bPrSuggest").textContent = t("pr_ai_working");
  const done = runningToast(t("pr_ai_started"), "suggestion", kind);
  const j = await waitJob("suggestion", kind, 2500);
  done();
  $("#bPrSuggest").disabled = false; $("#bPrSuggest").textContent = t(AI_BUTTON[kind]);
  if (j.state === "cancelled") return;
  if (j.state === "error") { msg(t("error") + j.text, true); return; }
  if (kind === prFile) showSuggestion(j); else msg(t("pr_suggestion_ready"));
}
function showSuggestion(j) {
  const el = $("#prSuggestion");
  el.hidden = !j;
  if (!j) { el.innerHTML = ""; return; }
  el.innerHTML = `<h3>${t("pr_suggestion")}</h3>${j.summary ? `<div class="md">${md(j.summary)}</div>` : ""}` +
    `<pre class="diffbox">${diffHtml(prEd.getValue(), j.new)}</pre>` +
    `<div class="btns"><button class="primary" id="bPrAccept">${t("accept")}</button><button id="bPrDiscard">${t("lr_discard")}</button></div>`;
  $("#bPrAccept").onclick = async () => {
    prEd.setValue(j.new);  // lands as a change in the editor, Ctrl+Z undoes it
    if (await saveFile()) showSuggestion(null);
  };
  $("#bPrDiscard").onclick = () => showSuggestion(null);
}

// ---------- Templates: cover letter and CV ----------
let tplData = null;
async function loadTemplates() {
  tplData = await api("/api/templates").catch(e => (msg(t("error") + e.message, true), null));
  if (!tplData) return;
  for (const card of $$(".tplcard")) {
    const kind = card.dataset.kind, chosen = tplData.chosen[kind] || "";
    const own = tplData.list.filter(v => v.kind === kind);
    card.querySelector(".tplselect").innerHTML = `<option value="">${t("tpl_auto")}</option>` +
      own.map(v => `<option value="${esc(v.rel)}" ${v.rel === chosen ? "selected" : ""}>${v.check.ok ? "✓" : "✗"} ${esc(v.rel)}</option>`).join("");
    card.querySelector(".tpllayouts").innerHTML = tplData.layouts.map(k => `<button data-layout="${k}">${esc(t("layout_" + k))}</button>`).join(" ");
    showTemplate(card);
  }
  for (const b of $$(".tpllayouts button")) b.onclick = () => generateTemplate(b.closest(".tplcard").dataset.kind, b.dataset.layout, b);
}
function activeTemplate(kind) {
  // the chosen one, otherwise the newest earlier file (finalizing searches the same way, here without the language)
  const chosen = tplData.chosen[kind];
  return tplData.list.find(v => v.rel === chosen) || tplData.list.find(v => v.kind === kind && v.source === "application") || null;
}
async function showTemplate(card) {
  const kind = card.dataset.kind, v = activeTemplate(kind);
  const ul = card.querySelector(".tplcheck"), frame = card.querySelector(".tplpreview"), pdf = card.querySelector(".tplpdf");
  ul.innerHTML = v ? (tplData.chosen[kind] ? "" : `<li class="muted">${t("tpl_auto_is")} ${esc(v.rel)}</li>`) +
    v.check.points.map(([ok, s]) => `<li class="${ok ? "ok" : "err"}">${ok ? "✓" : "✗"} ${esc(s)}</li>`).join("")
    : `<li class="err">✗ ${t("tpl_none_" + kind)}</li>`;
  frame.hidden = pdf.hidden = true;
  if (!v) return;
  try {
    const r = await api("/api/templates/preview?path=" + encodeURIComponent(v.rel));
    const url = "/doc?path=" + encodeURIComponent(r.pdf) + "&v=" + Date.now();
    frame.src = url + "#toolbar=0&view=FitH"; pdf.href = url; frame.hidden = pdf.hidden = false;
  } catch (e) { ul.insertAdjacentHTML("beforeend", `<li class="err">✗ ${esc(e.message)}</li>`); }
}
for (const card of $$(".tplcard")) {
  const kind = card.dataset.kind;
  card.querySelector(".tplselect").onchange = async e => {
    try { await postJson("/api/profile", {vorlagen: {...tplData.chosen, [kind]: e.target.value}}); msg(t("saved")); await loadTemplates(); }
    catch (err) { msg(t("error") + err.message, true); }
  };
  card.querySelector("input[type=file]").onchange = async e => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    try { templateAdded(kind, await postJson("/api/templates/upload", {kind, name: file.name, data: await readBase64(file)})); }
    catch (err) { msg(t("error") + err.message, true); }
  };
}
async function generateTemplate(kind, layout, button) {
  button.disabled = true;
  try { templateAdded(kind, await postJson("/api/templates/generate", {kind, layout})); }
  catch (e) { msg(t("error") + e.message, true); }
  button.disabled = false;
}
async function templateAdded(kind, r) {
  // a new template that works becomes the active one right away
  if (r.check.ok) { await postJson("/api/profile", {vorlagen: {...tplData.chosen, [kind]: r.rel}}).catch(() => {}); msg(`${r.rel}: ${t("tpl_activated")}`); }
  else msg(`${r.rel}: ${r.check.points.filter(p => !p[0]).map(p => p[1]).join("; ")}`, true);
  await loadTemplates();
}

$("#bTplCvContent").onclick = async () => { profileSection("text"); await loadFile("lebenslauf"); };

// ---------- First setup: create or open a data folder ----------
let setupMode = "new";
function showSetup(info) {
  $("#setupPath").value = info.suggestion;
  $("#setup").classList.add("on");
  const mode = m => {
    setupMode = m;
    for (const b of $$("#setupMode button")) b.classList.toggle("on", b.dataset.m === m);
    $("#setupNewFields").hidden = m !== "new"; $("#setupDirs").hidden = m !== "open";
    $("#setupError").textContent = "";
    if (m === "open") setupBrowse($("#setupPath").value.replace(/\/Bewerbungen\/?$/, "") || info.home);
  };
  for (const b of $$("#setupMode button")) b.onclick = () => mode(b.dataset.m);
  mode("new");
  $("#setupName").focus();
}
async function setupBrowse(path) {
  // simple folder picker: click subfolders, folders with Bewerbungen/ are marked
  const d = await api("/api/dirs?path=" + encodeURIComponent(path)).catch(e => ($("#setupError").textContent = e.message, null));
  if (!d) return;
  $("#setupPath").value = d.path;
  $("#setupDirs").innerHTML = (d.parent ? `<button data-p="${esc(d.parent)}">↑ ..</button>` : "") +
    d.dirs.map(x => `<button data-p="${esc(d.path + "/" + x.name)}" class="${x.ok ? "ok" : ""}">${esc(x.name)}${x.ok ? " ✓" : ""}</button>`).join("") +
    (d.ok ? `<p class="ok">${t("su_fits")}</p>` : `<p class="muted">${t("su_look")}</p>`);
  for (const b of $$("#setupDirs button")) b.onclick = () => setupBrowse(b.dataset.p);
}
$("#setupPath").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); setupMode === "open" ? setupBrowse(e.target.value) : $("#setupGo").click(); } };
$("#setupGo").onclick = async () => {
  const body = {mode: setupMode, path: $("#setupPath").value, name: $("#setupName").value, email: $("#setupEmail").value, town: $("#setupTown").value};
  $("#setupGo").disabled = true; $("#setupError").textContent = "";
  try {
    await postJson("/api/setup", body);
    try { if (setupMode === "new") localStorage.setItem("rv-first-start", "1"); } catch {}
    location.reload();
  } catch (e) { $("#setupError").textContent = e.message; $("#setupGo").disabled = false; }
};

// ---------- First steps (on top of the profile until everything is done) ----------
async function firstSteps() {
  const el = $("#prSteps");
  const [docs, tpl, profileFile] = await Promise.all([api("/api/documents").catch(() => []), api("/api/templates").catch(() => null),
    api("/api/profile/file?key=profil").catch(() => ({content: ""}))]);
  const steps = [
    ["details", !!(prof?.name && prof?.email), "fs_details"],
    ["documents", docs.length > 0, "fs_documents"],
    // the placeholder text of the example folder (German or English) does not count as a profile
    ["text", profileFile.content.trim().length > 200 && !/Grundlage für alle Briefe\. Die KI nimmt nur Fakten von hier|Basis for all letters\. The AI only takes facts from here/.test(profileFile.content), "fs_profile"],
    ["templates", !!tpl?.list.some(v => v.kind === "anschreiben" && v.check.ok), "fs_template"],
  ];
  el.hidden = steps.every(s => s[1]);
  el.innerHTML = `<h3>${t("fs_title")}</h3><ol>` + steps.map(([sec, ok, k]) => `<li class="${ok ? "ok" : ""}"><button data-psec="${sec}">${ok ? "✓" : "○"} ${t(k)}</button></li>`).join("") + "</ol>";
  for (const b of el.querySelectorAll("button")) b.onclick = () => profileSection(b.dataset.psec);
}

function openProfile() {
  if (!profLoaded) loadProfile().then(firstSteps); else firstSteps();
  profileSection($("#prnav button.on")?.dataset.psec || "details");
}
