// ---------- Feedback history and learnings ----------
// Uses the helpers from app.js ($, $$, t, api, postJson, msg, esc, md, cur, loadMemory, locale).
const fmtTs = ts => { const d = new Date(ts); return isNaN(d) ? ts : d.toLocaleString(locale(), {dateStyle: "short", timeStyle: "short"}); };

function roundHtml(r, withCompany) {
  if (r.kind === "undo") return `<div class="round undo"><span class="when">${esc(fmtTs(r.ts))}</span>${withCompany ? `<b>${esc(r.company || "")}</b>` : ""}<span>↶ ${t("lr_undone")}</span></div>`;
  return `<div class="round${r.backfilled ? " old" : ""}"><div class="rhead"><span class="when">${esc(fmtTs(r.ts))}</span>` +
    (withCompany ? `<b>${esc(r.company || "")}</b>` : "") + (r.provider ? `<small>${esc(r.provider)}</small>` : "") +
    (r.changed === false ? `<small class="warn">${t("lr_unchanged")}</small>` : "") + `</div>` +
    `<div class="you"><span>${t("lr_you")}</span>${r.feedback ? esc(r.feedback).replace(/\n/g, "<br>") : `<i>${t(r.backfilled ? "lr_lost" : "lr_empty")}</i>`}</div>` +
    `<div class="ai"><span>${t("lr_ai")}</span>${md(r.answer || "")}</div></div>`;
}

// History of one letter (button in the feedback panel)
async function letterHistory() {
  if (!cur) return;
  const d = await api("/api/rounds?path=" + encodeURIComponent(cur.path)).catch(e => (msg(t("error") + e.message, true), null));
  if (!d) return;
  const rounds = d.rounds.slice().reverse();
  $("#fbhist .sheet").innerHTML = `<div class="shead"><h2>${t("lr_hist")}: ${esc(cur.company)}</h2><button class="close">${t("toast_close")}</button></div>` +
    (rounds.length ? rounds.map(r => roundHtml(r)).join("") : `<p class="muted">${t("lr_none_letter")}</p>`);
  $("#fbhist").classList.add("on");
  $("#fbhist .close").onclick = () => $("#fbhist").classList.remove("on");
}
$("#fbhist").onclick = e => { if (e.target === $("#fbhist")) $("#fbhist").classList.remove("on"); };
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#fbhist").classList.contains("on")) { e.preventDefault(); $("#fbhist").classList.remove("on"); } }, true);
$("#bFbHist").onclick = letterHistory;

// Knowledge -> Learnings: all rounds, summary, edit the suggestions and write them to the memory
let learnData = null, suggestions = [], learnUntil = "", learnExisting = [];
async function loadLearnings() {
  const d = await api("/api/rounds").catch(e => (msg(t("error") + e.message, true), null));
  if (!d) return;
  learnData = d;
  const fresh = d.rounds.filter(r => !d.until || r.ts > d.until).length;
  $("#lrState").textContent = `${d.rounds.length} ${t("lr_rounds")}, ${fresh} ${t("lr_new")}` + (d.until ? ` (${t("lr_since")} ${fmtTs(d.until)})` : "");
  $("#bLrNew").disabled = !fresh;
  $("#lrRounds").innerHTML = d.rounds.length ? d.rounds.slice().reverse().map(r => roundHtml(r, true)).join("") : `<p class="muted">${t("lr_none")}</p>`;
  const j = await jobState("learnings", "");
  if (j.state === "running") { learnBusy(true); learnPoll(); }
}
function learnBusy(on) {
  $("#bLrNew").disabled = on || !learnData?.rounds.length; $("#bLrAll").disabled = on;
  $("#bLrNew").textContent = on ? t("lr_running") : t("lr_sum_new");
}
async function learnStart(all) {
  try { await postJson("/api/learnings", {all}); } catch (e) { msg(t("error") + e.message, true); return; }
  learnBusy(true); learnPoll();
}
async function learnPoll() {
  const done = runningToast(t("lr_started"), "learnings", "");
  const j = await waitJob("learnings", "", 2500);
  done(); learnBusy(false);
  if (j.state === "cancelled") return;
  if (j.state === "error") { msg(t("error") + j.text, true); return; }
  suggestions = (j.suggestions || []).map(v => ({...v, on: true})); learnUntil = j.until || ""; learnExisting = j.existing || [];
  if (!suggestions.length) msg(j.n ? t("lr_nothing") : t("lr_no_new"));
  renderSuggestions();
}
function renderSuggestions() {
  const el = $("#lrSuggestions");
  el.hidden = !suggestions.length;
  if (!suggestions.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<h3>${t("lr_proposals")} (${suggestions.length})</h3><p class="hint">${t("lr_proposals_hint")}</p>` +
    suggestions.map((v, i) => `<div class="suggestion${v.on ? "" : " off"}" data-i="${i}">` +
      `<label class="sughead"><input type="checkbox" class="on" ${v.on ? "checked" : ""}><input class="title" value="${esc(v.title)}">` +
      `<select class="type">${["feedback", "user", "project", "reference"].map(x => `<option ${x === v.type ? "selected" : ""}>${x}</option>`).join("")}</select></label>` +
      `<input class="desc" value="${esc(v.description)}" placeholder="${t("lr_desc_ph")}">` +
      `<textarea class="body" rows="6">${esc(v.body)}</textarea>` +
      `<div class="sugfoot"><code>${esc(v.file)}</code>${learnExisting.includes(v.file) ? `<span class="warn">${t("lr_replaces")}</span>` : `<span class="ok">${t("lr_newfile")}</span>`}` +
      (v.sources?.length ? `<small>${t("lr_from")}: ${esc(v.sources.join(", "))}</small>` : "") + `</div></div>`).join("") +
    `<div class="btns"><button class="primary" id="bLrWrite">${t("lr_write")}</button><button id="bLrDiscard">${t("lr_discard")}</button></div>`;
  for (const box of el.querySelectorAll(".suggestion")) {
    const v = suggestions[+box.dataset.i];
    box.querySelector(".on").onchange = e => { v.on = e.target.checked; box.classList.toggle("off", !v.on); };
    box.querySelector(".title").oninput = e => v.title = e.target.value;
    box.querySelector(".type").onchange = e => v.type = e.target.value;
    box.querySelector(".desc").oninput = e => v.description = e.target.value;
    box.querySelector(".body").oninput = e => v.body = e.target.value;
  }
  $("#bLrWrite").onclick = writeSuggestions;
  $("#bLrDiscard").onclick = () => { suggestions = []; renderSuggestions(); };
}
async function writeSuggestions() {
  const entries = suggestions.filter(v => v.on && v.body.trim());
  if (!entries.length) { msg(t("lr_pick"), true); return; }
  try {
    const r = await postJson("/api/learnings/write", {entries, until: learnUntil});
    msg(`${r.files.length} ${t("lr_written")}`);
    suggestions = []; renderSuggestions();
    loadMemory(); loadLearnings();
  } catch (e) { msg(t("error") + e.message, true); }
}
$("#bLrNew").onclick = () => learnStart(false);
$("#bLrAll").onclick = () => learnStart(true);
$("#bLrReload").onclick = loadLearnings;
