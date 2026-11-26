#!/usr/bin/env python3
"""Local board for open cover letters.

Start:  python3 server.py [port] [--open]   (then http://127.0.0.1:8765; --open opens the browser)
        Data folder: the one stored in the settings, otherwise APPLICATION_BOARD_ROOT, otherwise the current directory.
        It must contain a subfolder applications/ (or Bewerbungen/ in the German layout of the first versions, see layout.py).

Status and feedback live in <root>/applications/_review/state.json, text changes go straight into the letter_*.md.
The code and the API use English keys and values; layout.py translates them for folders of the German layout.
New letters that git does not know yet or that changed land in the column "open" automatically. Settings (data folder, theme, language, vim, providers, layout) live in
settings.json in the config folder (see storage.config_dir), keys in secrets.json next to it.
"""
import base64
import json
import tempfile
import time
import urllib.request
import zipfile
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import ai
import documents
import finalizing
import jobsearch
import layout
import learnings
import pipeline
import storage
import templates
import thunderbird
import userprofile

VERSION = "0.7.0"
DIR = Path(__file__).resolve().parent
SETTINGS = storage.config_file("settings.json")
STATIC = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/i18n.js": "i18n.js", "/boot.js": "boot.js", "/pipeline.js": "pipeline.js",
          "/learnings.js": "learnings.js", "/profile.js": "profile.js", "/icon.svg": "icon.svg"}
VENDOR = DIR / "vendor"  # CodeMirror, marked, DOMPurify and the fonts, stored locally (no CDN)
DEFAULT_ROOT = Path(storage.env("ROOT") or Path.cwd())  # app and data live in separate folders
ROOT = APPS = LAY = None  # set by set_root; LAY: layout.Layout of the data folder
STATUSES = ("open", "final", "sent", "archived")
THEME_RE = re.compile(r"[a-z0-9-]{1,40}")  # the list of themes lives in app.js/app.css, only the format here
LANGS = ("de", "en")
TASKS = ("feedback", "rate", "draft", "check", "facts", "prepare", "followup", "learnings", "profile")
OLD_TASKS = {"bewerten": "rate", "entwurf": "draft", "pruefen": "check", "vorbereitung": "prepare", "lernen": "learnings", "profil": "profile"}
OPTIONAL_TASKS = {"check", "facts"}  # "" means: no AI for this task
TABS = ("letters", "jobs")
DEFAULT_PROVIDER = {"id": "claude-code", "name": "Claude Code", "type": "claude-code", "model": "", "base_url": "", "cmd": "", "key_ref": "", "temperature": None, "max_tokens": None}
DEFAULTS = {"root": str(DEFAULT_ROOT), "theme": "system", "accent": "", "lang": "de", "vim": True, "spellcheck": True, "layout": None, "update_check": False,
            "providers": [dict(DEFAULT_PROVIDER)], "tasks": {t: "claude-code" for t in TASKS}, "tab": "letters"}
LOCK = threading.Lock()  # the state file and settings.json are written by the handler and by AI jobs
JOBS = {}  # "kind:key" -> {"state": "running"|"done"|"error"|"cancelled", "text", ...result, "thread": id}
SAVE_BACKUP_EVERY = timedelta(minutes=10)  # manual saves keep a version at most this often
LETTER_HEAD = re.compile(r"^# (Bewerbungsschreiben|Cover Letter)\s*$", re.M)
INLINE_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}  # everything else is downloaded, never rendered as a page
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
       "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")

PROMPT = """Apply the following feedback from {person} to the cover letter {path}.

Feedback:
<<<
{feedback}
>>>

Rules:
- First read the file as well as {profile_file} (profile) and {style_file} (writing style).
- Change only {path}, no other file.
- Follow the rules from CLAUDE.md (if present) and the writing style. No invented facts.
- Keep the language of the letter.
{rules}
- If the feedback asks for something the profile does not back up, invent nothing and say so in the answer.
- End with two to four sentences in {language} on what you changed."""

FEEDBACK_PROMPT = """Apply the following feedback from {person} to the cover letter {path}.

Feedback:
<<<
{feedback}
>>>

Rules:
- Change only what the feedback asks for; the rest of the file (job info, mapping, letter) stays word for word.
- Keep the language of the letter. No invented facts (only what is in the profile).
{rules}
- If the feedback asks for something the profile does not back up, invent nothing and say so in the summary.

Return the complete revised file between the markers, then the summary:
===FILE===
(complete file content)
===END===
===SUMMARY===
(two to four sentences in {language} on what you changed)

The earlier rounds show what {person} already asked for or rejected for this letter: do not contradict them.
The memories are lasting rules for all applications: follow them.

=== EARLIER ROUNDS FOR THIS LETTER ===
{rounds}

=== MEMORIES ===
{memory}

=== PROFILE ===
{profile}

=== STYLE ===
{style}

=== FILE {path} ===
{letter}
"""

FOLLOW_UP_PROMPT = """New round for the same cover letter {path}. Read the file again first: it may have been edited by hand since your last answer.{since}

New feedback:
<<<
{feedback}
>>>

The same rules as in the first round apply. Change only {path}. End with two to four sentences in {language} on what you changed."""


def lang():
    return load_settings()["lang"]


def language_name():
    return userprofile.LANGUAGE_NAMES.get(lang(), "German")


def rounds_text(rel, n=8):
    """Memory between the rounds: the last feedback rounds and undo steps for this letter."""
    lines = []
    for r in [r for r in learnings.read(ROOT) if r.get("path") == rel][-n:]:
        if r.get("kind") == "undo":
            lines.append(f"[{r.get('ts', '')[:16]}] The change of the previous round was rejected with undo.")
        else:
            lines.append(f"[{r.get('ts', '')[:16]}] Feedback: {r.get('feedback') or '(not stored)'}\nAnswer: {r.get('answer', '')}")
    return "\n\n".join(lines)


def memory_text(limit=25000):
    """Claude memory for providers that do not load it themselves (everything except Claude Code)."""
    md = memory_dir()
    parts = [f"--- {p.name} ---\n{frontmatter(p.read_text('utf-8'))[1].strip()}" for p in sorted(md.glob("*.md")) if p.name != "MEMORY.md"] if md.is_dir() else []
    return ai.clip("\n\n".join(parts), limit, "memories")


# ---------- Background jobs ----------

def start_job(kind, key, fn, *args):
    """Runs fn(*args) in a thread. Its dict result lands in JOBS["kind:key"] with state done; errors as error,
    a cancel as cancelled. Raises FileExistsError while the same job is still running."""
    k = f"{kind}:{key}"
    if JOBS.get(k, {}).get("state") == "running":
        raise FileExistsError("Already running")
    JOBS[k] = {"state": "running", "text": ""}

    def run():
        ai.reset()
        JOBS[k]["thread"] = threading.get_ident()
        try:
            JOBS[k] = {"state": "done", **(fn(*args) or {})}
        except Exception as e:  # show errors in the board instead of swallowing them
            cancelled = ai.cancelled()
            JOBS[k] = {"state": "cancelled" if cancelled else "error", "text": "" if cancelled else str(e)[:2000]}
            if not cancelled and not isinstance(e, (ai.AIError, ValueError, RuntimeError, FileNotFoundError)):
                traceback.print_exc()

    threading.Thread(target=run, daemon=True).start()
    return {k2: v for k2, v in JOBS[k].items() if k2 != "thread"}


def job_state(kind, key):
    j = JOBS.get(f"{kind}:{key}", {"state": "idle"})
    return {k: v for k, v in j.items() if k != "thread"}


def cancel_job(kind, key):
    j = JOBS.get(f"{kind}:{key}")
    if not j or j.get("state") != "running":
        return {"state": (j or {}).get("state", "idle")}
    if j.get("thread"):
        ai.cancel(j["thread"])
    return {"state": "running", "cancelling": True}


# ---------- Settings and data folder ----------

def load_settings():
    try:
        saved = json.loads(SETTINGS.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    s = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    s["providers"] = [dict(DEFAULT_PROVIDER, **p) for p in s["providers"] if isinstance(p, dict) and p.get("id")] or [dict(DEFAULT_PROVIDER)]
    if saved.get("model") and not any(p.get("model") for p in s["providers"] if p["type"] == "claude-code"):
        s["providers"][0]["model"] = saved["model"]  # old setting from before the providers
    saved_tasks = {OLD_TASKS.get(k, k): v for k, v in (s["tasks"] or {}).items()}  # task ids before the English rename
    saved_tasks.setdefault("followup", saved_tasks.get("prepare", ""))  # new task: same provider as the preparation
    saved_tasks.setdefault("facts", "")  # the AI fact check costs a call per round: off until chosen
    s["tasks"] = {t: "" if t in OPTIONAL_TASKS and saved_tasks.get(t) == "" else saved_tasks.get(t) or "claude-code" for t in TASKS}
    s["tab"] = s["tab"] if s["tab"] in TABS else "letters"  # "bewerbungen" before the English rename
    s["theme"] = {"minecraft": "pixel"}.get(s["theme"], s["theme"])  # old theme id
    return s


def save_settings(s):
    storage.write_atomic(SETTINGS, json.dumps(s, ensure_ascii=False, indent=2) + "\n")


def set_root(path):
    """Checks the folder and switches everything (letters, state, claude -p) to it."""
    global ROOT, APPS, LAY
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("Path must be absolute")
    p = p.resolve()
    if not p.is_dir():
        raise ValueError("Folder does not exist")
    if not data_folder(p):
        raise ValueError("Folder has no subfolder applications (or Bewerbungen)")
    ROOT, LAY = p, layout.get(p)
    APPS = LAY.apps
    return p


def update_settings(body):
    s = load_settings()
    for k, v in body.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        if k == "root":
            v = str(set_root(v))
        elif k == "theme" and not THEME_RE.fullmatch(str(v)) or k == "lang" and v not in LANGS or k == "tab" and v not in TABS:
            raise ValueError(f"invalid value for {k}")
        elif k == "tasks":
            ids = {p["id"] for p in s["providers"]}
            if not isinstance(v, dict) or any(t not in TASKS or not (pid in ids or pid == "" and t in OPTIONAL_TASKS) for t, pid in v.items()):
                raise ValueError("unknown task or provider")
            v = {**s["tasks"], **v}
        elif k == "providers":
            raise ValueError("change providers through /api/providers")
        elif k in ("vim", "spellcheck", "update_check"):
            v = bool(v)
        elif k == "accent" and not re.fullmatch(r"(#[0-9a-fA-F]{6})?", v or ""):
            raise ValueError("accent color must be #rrggbb")
        elif k == "layout" and not (v is None or isinstance(v, dict)):
            raise ValueError("layout must be an object")
        s[k] = v
    save_settings(s)
    return s


def provider_for(task):
    s = load_settings()
    pid = s["tasks"].get(task)
    p = next((p for p in s["providers"] if p["id"] == pid), None)
    if not p:
        raise ValueError(f"No provider chosen for task {task}")
    return {**p, "_task": task}  # the task goes into the usage log


def save_provider(body):
    """Create or change. The key goes to secrets.json (0600) or stays an env:NAME reference, never into settings.json."""
    s = load_settings()
    p = body.get("provider") or {}
    if p.get("type") not in ai.TYPES:
        raise ValueError("unknown provider type")
    pid = re.sub(r"[^a-z0-9]+", "-", (p.get("id") or p.get("name") or "").lower()).strip("-")
    if not pid:
        raise ValueError("name missing")
    old = next((x for x in s["providers"] if x["id"] == pid), None) or dict(DEFAULT_PROVIDER, key_ref="")
    base = str(p.get("base_url") or "").strip()
    if base and not re.match(r"https?://", base):
        raise ValueError("base URL must start with http:// or https://")
    temp = p.get("temperature")
    if temp not in (None, "") and not 0 <= float(temp) <= 2:
        raise ValueError("temperature between 0 and 2")
    mt = p.get("max_tokens")
    new = {**old, "id": pid, "name": str(p.get("name") or pid)[:60], "type": p["type"], "model": str(p.get("model") or "").strip()[:100],
           "base_url": base, "cmd": str(p.get("cmd") or "").strip()[:500],
           "temperature": None if temp in (None, "") else float(temp), "max_tokens": None if mt in (None, "") else int(mt)}
    key = str(body.get("key") or "")
    secrets = ai.load_secrets()
    if body.get("clear_key"):
        new["key_ref"] = ""
        secrets.pop(pid, None)
    elif key.startswith("env:"):
        new["key_ref"] = key.strip()
        secrets.pop(pid, None)
    elif key:
        new["key_ref"] = "secret"
        secrets[pid] = key
    if secrets != ai.load_secrets():
        ai.save_secrets(secrets)
    s["providers"] = [new if x["id"] == pid else x for x in s["providers"]] if old.get("id") == pid else s["providers"] + [new]
    save_settings(s)
    return s


def delete_provider(pid):
    s = load_settings()
    if len(s["providers"]) <= 1:
        raise ValueError("The last provider stays")
    s["providers"] = [x for x in s["providers"] if x["id"] != pid]
    fallback = s["providers"][0]["id"]
    s["tasks"] = {t: (v if v != pid else fallback) for t, v in s["tasks"].items()}
    secrets = ai.load_secrets()
    if secrets.pop(pid, None) is not None:
        ai.save_secrets(secrets)
    save_settings(s)
    return s


def public_settings(s=None):
    s = s or load_settings()
    return {**s, "providers": [ai.public(p) for p in s["providers"]], "version": VERSION, "config_dir": str(storage.config_dir()),
            "tools": {t: bool(storage.tool(t)) for t in ("soffice", "pdftotext")}, "thunderbird": bool(thunderbird.program()),
            "rulecheck": userprofile.load(ROOT)["rule_check"] if ROOT else userprofile.DEFAULT["rule_check"]}


def data_folder(p):
    """Is p a data folder (English or German layout)?"""
    return (p / layout.PATHS["apps"][0]).is_dir() or (p / layout.PATHS["apps"][1]).is_dir()


def list_dirs(path):
    p = Path(path or ROOT or Path.home()).expanduser().resolve()
    if not p.is_dir():
        raise ValueError("Folder does not exist")
    dirs = sorted((d for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.lower())
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "ok": data_folder(p),
            "dirs": [{"name": d.name, "ok": data_folder(d)} for d in dirs]}


# ---------- First setup ----------
# Without a valid data folder the server still starts; the page then shows the welcome screen.
OPEN_WITHOUT_FOLDER = {"/api/setup", "/api/settings", "/api/dirs", "/api/providers"}


def setup_info():
    return {"needed": ROOT is None, "suggestion": str(Path.home() / ("Bewerbungen" if lang() == "de" else "Applications")), "home": str(Path.home())}


def setup(body):
    """Create a data folder (from example/<language>/) or open an existing one, then remember it in the settings."""
    path = Path(str(body.get("path", "")).strip()).expanduser()
    en = lang() == "en"
    if not path.is_absolute():
        raise ValueError("Please give a full path, e.g. ~/Applications" if en else "Bitte einen vollständigen Pfad angeben, z.B. ~/Bewerbungen")
    if body.get("mode") == "new":
        if not str(body.get("name", "")).strip() or "@" not in str(body.get("email", "")):
            raise ValueError("Give name and e-mail" if en else "Name und E-Mail angeben")
        if data_folder(path):
            raise ValueError("There already is a data folder there: choose \"Open existing folder\"" if en else
                             "Dort gibt es schon einen Datenordner: \"Vorhandenen Ordner öffnen\" wählen")
        if path.exists() and any(path.iterdir()):
            raise ValueError("The folder is not empty; choose a new or empty folder" if en else "Der Ordner ist nicht leer; einen neuen oder leeren Ordner wählen")
        shutil.copytree(DIR / "example" / ("en" if en else "de"), path, dirs_exist_ok=True)
        with LOCK:
            update_settings({"root": str(path)})
        userprofile.save(ROOT, {"name": body.get("name", ""), "email": body.get("email", ""), "town": body.get("town", "")})
    else:
        with LOCK:
            update_settings({"root": str(path)})  # checks that it is a data folder
    return setup_info()


# ---------- Fact check ----------

def check_facts(new, before, sources):
    """Token check always, plus the AI check when the optional task "facts" has a provider. Returns (found, warning)."""
    found = finalizing.unverified(new, sources, before)
    parts = [finalizing.fact_warning(found, lang())]
    if load_settings()["tasks"].get("facts"):
        try:
            claims = finalizing.ai_facts(provider_for("facts"), new, before, sources, ROOT, lang())
        except ai.AIError as e:
            ai.check_cancelled()
            claims = [f"({e})"]
        if claims:
            found = found + claims
            parts.append(("AI fact check: " if lang() == "en" else "KI-Fakten-Check: ") + " ".join(claims))
    return found, "\n\n".join(filter(None, parts))


# ---------- Feedback job ----------

def run_job(rel, feedback):
    p = resolve(rel)
    before = p.read_text("utf-8")
    backup = storage.backup(LAY.backups, p)
    prov = provider_for("feedback")
    prof = userprofile.load(ROOT)
    rules = userprofile.rules(prof)
    session, warning = "", ""
    try:
        if prov["type"] == "claude-code":  # agentic: Claude Code reads and edits the file itself
            # memory: continue the same Claude conversation per letter; when it is gone, start fresh with the rounds in the prompt
            old = (load_state().get(rel) or {}).get("claude_session", "")
            rounds = [r for r in learnings.read(ROOT) if r.get("path") == rel]
            after = rounds[max((i for i, r in enumerate(rounds) if r.get("kind") != "undo"), default=-1) + 1:]
            since = "\nSince your last answer your change was rejected with undo." if after else ""
            # only this letter may be edited; no shell, no web (feedback may quote postings, they must not send data out)
            kw = dict(allowed=["Read", "Glob", "Grep", f"Edit({rel})"], disallowed=["Bash", "Write", *ai.NO_WEB])
            snap = storage.snapshot(ROOT)
            info, text = {}, None
            if old:
                try:
                    text = ai.run_claude(FOLLOW_UP_PROMPT.format(path=rel, feedback=feedback, since=since, language=language_name()),
                                         ROOT, prov.get("model", ""), resume=old, info=info, task="feedback", **kw)
                except ai.AIError:
                    ai.check_cancelled()
                    info, text = {}, None
            if text is None:
                history = rounds_text(rel)
                text = ai.run_claude(PROMPT.format(path=rel, feedback=feedback, person=userprofile.person(prof), rules=rules, language=language_name(),
                                                   profile_file=prof["files"]["profile"], style_file=prof["files"]["style"])
                                     + (f"\n\nEarlier rounds for this letter (do not contradict them):\n{history}" if history else ""),
                                     ROOT, prov.get("model", ""), info=info, task="feedback", **kw)
            session = info.get("session_id", "")
            other = storage.changed_since(ROOT, snap, {rel})
            if other:
                warning = ("Warning: the AI also changed: " if lang() == "en" else "Achtung, die KI hat ausserdem geändert: ") + ", ".join(other[:10])
        else:  # plain language model: gets everything in the prompt and returns the whole file
            out = ai.complete(prov, FEEDBACK_PROMPT.format(path=rel, feedback=feedback, letter=before, person=userprofile.person(prof), rules=rules,
                                                           language=language_name(), profile=ai.clip(userprofile.text(ROOT, prof, "profile"), 30000, "profile"),
                                                           style=ai.clip(userprofile.text(ROOT, prof, "style"), 10000, "style"),
                                                           rounds=rounds_text(rel) or "(first round)", memory=memory_text() or "(none)"),
                              max_tokens=8000, timeout=600, cwd=ROOT)
            ai.check_cancelled()
            new = ai.between(out, "===FILE===", "===END===").strip()
            if not new or not LETTER_HEAD.search(new):
                raise RuntimeError("Answer without a complete file (markers or the letter heading missing): " + out[:200])
            storage.write_atomic(p, userprofile.ss(new, prof) + "\n")
            text = out.split("===SUMMARY===")[-1].strip() if "===SUMMARY===" in out else ("Feedback applied." if lang() == "en" else "Feedback eingearbeitet.")
    except Exception:
        if ai.cancelled() and p.read_text("utf-8") != before:  # cancelled halfway: back to the text from before the round
            storage.write_atomic(p, before)
        raise
    after_text = p.read_text("utf-8")
    # sources: what the person wrote (this and earlier feedback), never the AI's answers, which repeat earlier warnings
    asked = [r.get("feedback", "") for r in learnings.read(ROOT) if r.get("path") == rel]
    facts, fact_text = check_facts(after_text, before, "\n".join([userprofile.facts_text(ROOT, prof), p.parent.name, feedback, *asked]))
    warning = "\n\n".join(filter(None, [warning, fact_text]))
    if warning:
        text = f"{text}\n\n{warning}"
    with LOCK:
        state = load_state()
        learnings.read(ROOT, state)  # the very first time, back-fill old answers before the new round is added
        entry = state.setdefault(rel, {"status": "open", "feedback": ""})
        if entry.get("feedback", "") == feedback:  # only clear when nothing new was added meanwhile
            entry["feedback"] = ""
        entry["answer"] = text
        if session:
            entry["claude_session"] = session
        save_state(state)
        changed = before != after_text
        learnings.append(ROOT, {"path": rel, "company": p.parent.name, "feedback": feedback, "answer": text, "changed": changed,
                                "backup": backup.relative_to(ROOT).as_posix(), "provider": prov.get("name", ""),
                                **({"session": session} if session else {})})
    return {"text": text, "before": before, "backup": backup.relative_to(ROOT).as_posix(), "changed": changed, "facts": facts}


# ---------- Letters and status ----------

def load_state():
    """{letter path: {"status", "feedback", "since", "phase", "files", "hints", "answer", ...}} with English keys and values."""
    return LAY.read_json("state", {})


def save_state(state):
    LAY.write_json("state", state)


def git_new_or_changed():
    """None when the folder is no git repo: then all letters without an entry count as new."""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--others", "--modified", "--exclude-standard", "--", LAY.rel("apps")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:  # git not installed
        return None
    if r.returncode:
        return None
    return set(filter(None, r.stdout.split("\0")))


def field(text, names):
    """Value of a **Name:** line; names is a regex alternation such as "Titel|Title"."""
    m = re.search(rf"^\*\*(?:{names}):\*\*\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def run_finalize(rel):
    """Letter into the Word layout, PDFs, checks. Only on success the letter moves to "final"."""
    pid = load_settings()["tasks"].get("check")
    res = finalizing.finalize_letter(ROOT, resolve(rel), provider_for("check") if pid else None, ui=lang())
    ai.check_cancelled()
    with LOCK:
        state = load_state()
        entry = state.setdefault(rel, {"status": "open", "feedback": ""})
        entry.update(status="final", since=date.today().isoformat(), files=res["files"], hints=res["hints"],
                     finalized=datetime.now().isoformat(timespec="seconds"))
        save_state(state)
    return {"files": res["files"], "hints": res["hints"]}


def migrate_moved(state, present):
    """A letter whose folder was renamed keeps its status, feedback history and Claude session: an entry whose file
    is gone moves to the one new letter with the same file name. Returns True when something moved."""
    gone = [rel for rel in state if rel.startswith(LAY.rel("apps") + "/") and rel not in present and not (ROOT / rel).exists()]
    moved = {}
    for rel in gone:
        name = Path(rel).name
        cands = [p for p in present if p not in state and Path(p).name == name]
        if len(cands) == 1:
            state[cands[0]] = state.pop(rel)
            moved[rel] = cands[0]
    if moved:
        log = LAY.feedback_log
        if log.is_file():
            lines = []
            for line in log.read_text("utf-8").splitlines():
                try:
                    r = LAY.to_en(json.loads(line), "feedback_log")
                    if r.get("path") in moved:
                        r["path"] = moved[r["path"]]
                        r["company"] = Path(r["path"]).parent.name
                        line = json.dumps(LAY.to_disk(r, "feedback_log"), ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
                lines.append(line)
            storage.write_atomic(log, "\n".join(lines) + "\n")
    return bool(moved)


# ---------- Pipeline after sending ----------

def log_title(p):
    t = field(p.read_text("utf-8"), "Titel|Title")
    return p.parent.name + (f", {t}" if t else "")


def thunderbird_mails():
    """The Thunderbird index, [] without a profile, None while it is still being built the first time."""
    if not thunderbird.ready():
        return None
    try:
        return thunderbird.index()
    except FileNotFoundError:
        return []


def pipeline_list():
    out, mails, prof = [], None, userprofile.load(ROOT)
    for it in letters():
        if it["status"] != "sent":
            continue
        p = resolve(it["path"])
        d = pipeline.read(p)
        phase = it.get("phase") or "sent"
        if mails is None and phase != "rejected":
            mails = thunderbird_mails()
        new = [] if phase == "rejected" else None if mails is None else pipeline.new_mails(mails, it["company"], p.read_text("utf-8"), d, it.get("since", ""), prof)
        s = pipeline.summary(d, it.get("since", ""))
        if pipeline.ghosted(phase, s):  # long silence: moved to "no reply" like a manual change, with a status entry in the log
            phase = "no_reply"
            with LOCK:
                state = load_state()
                state.setdefault(it["path"], {"status": "sent", "feedback": ""})["phase"] = phase
                pipeline.add_event(p, log_title(p), "status", pipeline.phase_name(phase, p, lang()) + f" ({pipeline.GHOST_DAYS}+)", lang=lang())
                save_state(state)
            s = pipeline.summary(pipeline.read(p), it.get("since", ""))
        out.append({**{k: it.get(k) for k in ("path", "company", "title", "link", "score", "note", "since", "files", "check")},
                    "phase": phase, "mails": new, **s})
    return out


def run_prepare(rel):
    p = resolve(rel)
    text = pipeline.prepare(provider_for("prepare"), ROOT, p, lang())
    ai.check_cancelled()
    with LOCK:
        d = pipeline.read(p)
        if d["preparation"]:  # back up the old version like with feedback
            b = storage.backup(LAY.backups, pipeline.path(p), f"{p.parent.name}_preparation.md")
            b.write_text(d["preparation"], "utf-8")
        d["preparation"] = text
        pipeline.write(p, log_title(p), d, lang=lang())
    return {"text": ""}


def run_followup(rel):
    p = resolve(rel)
    since = (load_state().get(rel) or {}).get("since", "")
    return pipeline.follow_up(provider_for("followup"), ROOT, p, since, lang())


def run_learnings(everything):
    with LOCK:
        rounds = learnings.read(ROOT, load_state())
    until = learnings.evaluated_until(ROOT)
    new = rounds if everything or not until else [r for r in rounds if r.get("ts", "") > until]
    if not new:
        return {"suggestions": [], "n": 0, "until": until}
    md = memory_dir()
    memory = [{"file": p.name, "content": p.read_text("utf-8")} for p in sorted(md.glob("*.md")) if p.name != "MEMORY.md"] if md.is_dir() else []
    v = learnings.summarize(provider_for("learnings"), ROOT, new, memory, lang())
    return {"suggestions": v, "n": len(new), "until": max(r.get("ts", "") for r in new), "existing": [m["file"] for m in memory]}


def run_draft(job):
    res = jobsearch.draft(ROOT, provider_for("draft"), job, lang())
    posting = res.pop("posting", "")
    res["facts"], warning = check_facts((ROOT / res["path"]).read_text("utf-8"), "",
                                        "\n".join([userprofile.facts_text(ROOT, userprofile.load(ROOT)), job.get("company", ""), posting]))
    if warning:
        res["text"] += "\n\n" + warning
    return res


def run_appmail(rel):
    p = resolve(rel)
    files = (load_state().get(rel) or {}).get("files") or {}
    names = [Path(files[k]).name for k in ("cl_pdf", "cv_pdf") if files.get(k)]
    return pipeline.application_mail(provider_for("followup"), ROOT, p, names, lang())


def output_file(rel):
    """Only PDF and docx directly in a company folder (where finalizing puts its files)."""
    p = (ROOT / rel).resolve()
    if not p.is_file() or p.parent.parent != APPS.resolve() or p.suffix.lower() not in (".pdf", ".docx"):
        raise ValueError("Path not allowed")
    return p


def resolve(rel):
    p = (ROOT / rel).resolve()
    if p.parent.parent != APPS.resolve() or not LAY.is_letter(p.name):
        raise ValueError("Path not allowed")
    return p


def pdf_current(p, entry):
    """Is there a cover letter PDF newer than the letter? Known from finalizing; otherwise only guess when the
    folder has exactly one letter."""
    rel = (entry.get("files") or {}).get("cl_pdf")
    if rel:
        cl = ROOT / rel
        return cl.is_file() and cl.stat().st_mtime >= p.stat().st_mtime
    if len(list(p.parent.glob(f"{LAY.letter}*.md"))) > 1:
        return False
    return any(c.stat().st_mtime >= p.stat().st_mtime for c in p.parent.glob(userprofile.prefix(userprofile.load(ROOT), "cover_letter") + "*.pdf"))


def letters():
    with LOCK:
        return _letters()


def norm_url(u):
    m = re.match(r"https?://(?:www\.)?([^/?#]+)([^?#]*)", u.strip())
    return (m.group(1).lower() + m.group(2).rstrip("/")) if m else u


_SCORES = {"key": None, "value": {}}


def scores():
    """URL of the posting -> {note, score} from all job search runs, the newest wins. Cached until a run file changes."""
    runs = jobsearch.runs(ROOT)
    key = tuple((r["rel"], (ROOT / r["rel"]).stat().st_mtime_ns) for r in runs)
    if key == _SCORES["key"]:
        return _SCORES["value"]
    out = {}
    for r in reversed(runs):  # runs() is newest first
        for s in jobsearch.parse_run((ROOT / r["rel"]).read_text("utf-8"))["jobs"]:
            if s.get("link"):
                out[norm_url(s["link"])] = {"note": s["note"], "score": s.get("score")}
    _SCORES.update(key=key, value=out)
    return out


def _letters():
    state, fresh, out = load_state(), git_new_or_changed(), []
    sc, checks = scores(), jobsearch.statuses(ROOT)
    files = LAY.letters()
    changed = migrate_moved(state, {p.relative_to(ROOT).as_posix() for p in files})
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        entry = state.get(rel)
        if entry is None:
            if fresh is not None and rel not in fresh:
                continue  # old, checked-in letters count as done
            entry = state[rel] = {"status": "open", "feedback": "", "since": date.today().isoformat()}
            changed = True
        text = p.read_text("utf-8")
        link = re.search(r"\]\((https?://[^)\s]+)\)", field(text, "Link"))
        out.append({
            "path": rel, "company": p.parent.name, "file": p.name,
            "title": field(text, "Titel|Title"), "link": link.group(1) if link else "",
            "lang": finalizing.language(text),
            "pdf": pdf_current(p, entry),  # only PDFs exported after the last change of the letter count
            **(sc.get(norm_url(link.group(1)), {}) if link else {}),
            "check": checks.get(jobsearch.job_history.normalize(link.group(1))) if link else None,  # "Posting still open?"
            **entry,
        })
    if changed:
        save_state(state)
    return out


def save_letter(p, rel, content):
    """Manual save: keeps a version at most every SAVE_BACKUP_EVERY, then writes atomically."""
    v = storage.versions(LAY.backups, p)
    if not v or datetime.now() - datetime.fromisoformat(v[0]["ts"]) > SAVE_BACKUP_EVERY:
        storage.backup(LAY.backups, p)
    storage.write_atomic(p, content)


# ---------- Memories, skill knowledge, profile files ----------

def frontmatter(text):
    """Flat key: value parser for the head of the memory files, nesting is ignored."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                m = re.match(r"\s*([\w-]+):\s*(.*)$", line)
                if m and m.group(2):
                    meta[m.group(1)] = m.group(2).strip().strip("\"'")
            body = text[end + 4:].lstrip("\n")
    return meta, body


def memory_dir():
    """Claude Code names a project folder after its path with every character other than A-Z, a-z, 0-9 replaced by "-"
    (also on Windows: C:\\Users\\x -> C--Users-x)."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT)) / "memory"


def memory_files():
    """All readable (and editable) knowledge files: memory, skill references, CLAUDE.md."""
    out = []

    def add(p, group, name=None):
        text = p.read_text("utf-8")
        meta, body = frontmatter(text)
        head = re.search(r"^#\s+(.+)$", body, re.M)
        out.append({"path": str(p), "group": group, "name": meta.get("name") or name or p.stem,
                    "description": meta.get("description") or (head.group(1) if head and not meta else ""),
                    "type": meta.get("type") or ("index" if p.name == "MEMORY.md" else "reference"),
                    "content": text, "mtime": str(p.stat().st_mtime_ns), "modified": meta.get("modified", "")})

    md = memory_dir()
    for p in sorted(md.glob("*.md")) if md.is_dir() else []:
        add(p, "memory")
    for p in sorted((ROOT / ".claude" / "skills").glob("*/references/*.md")):
        add(p, "skill", f"{p.parents[1].name}/{p.name}")
    seen = {o["path"] for o in out}
    prof = userprofile.load(ROOT)
    for k in prof["files"]:  # profile files from the profile, when they live elsewhere
        f = userprofile.path(ROOT, prof, k)
        if f.is_file() and str(f) not in seen:
            add(f, "skill", f"{k}: {prof['files'][k]}")
    if (ROOT / "CLAUDE.md").is_file():
        add(ROOT / "CLAUDE.md", "claude", "CLAUDE.md")
    return out


def save_profile_file(key, content, mtime):
    """Write a profile file (profile.md, style.md, ...), old version to the backups."""
    prof = userprofile.load(ROOT)
    if key not in prof["files"]:
        raise ValueError("unknown profile file")
    p = userprofile.path(ROOT, prof, key)
    current = str(p.stat().st_mtime_ns) if p.is_file() else ""
    if str(mtime or "") != current:
        raise FileExistsError("File was changed elsewhere in the meantime")
    if p.is_file():
        storage.backup(LAY.backups, p, f"{LAY.profile_dir.name}_{p.name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    storage.write_atomic(p, content)
    return str(p.stat().st_mtime_ns)


def run_suggestion(kind):
    return userprofile.suggestion(provider_for("profile"), ROOT, kind, lang())


# ---------- HTTP routes ----------
# Each route gets (query dict, body dict) and returns a JSON-able value, or a (bytes, type, headers) tuple for files.

def q1(q, name, default=None):
    if name in q:
        return q[name][0]
    if default is None:
        raise KeyError(name)
    return default


def get_letter(q, _):
    rel = q1(q, "path")
    p = resolve(rel)
    entry = load_state().get(rel, {})
    return {"content": p.read_text("utf-8"), "mtime": str(p.stat().st_mtime_ns), "feedback": entry.get("feedback", ""),
            "status": entry.get("status", "open"), "answer": entry.get("answer", "")}


def get_search_run(q, _):
    name = q1(q, "name")
    text = (ROOT / run_file(name)).read_text("utf-8")
    r, st = jobsearch.parse_run(text), jobsearch.statuses(ROOT)
    for j in r["jobs"]:
        j["check"] = st.get(jobsearch.job_history.normalize(j.get("link", ""))) if j.get("link") else None
    return {**r, "md": text, "name": name, "hidden": jobsearch.load_config(ROOT)["hidden"]}


def run_file(name):
    """Relative path of a result file of the job search by its name (jobsearch_<date>), only in the job search folder."""
    d = LAY.search
    p = (d / (name + ".md")).resolve()
    if p.parent != d.resolve() or not name.startswith(LAY.run + "_") or not p.is_file():
        raise ValueError("Path not allowed")
    return p.relative_to(ROOT).as_posix()


def post_posting_check(q, body):
    """Is the posting of one application still online? Its link comes from the letter, never from the browser."""
    it = next((i for i in letters() if i["path"] == body["path"]), None)
    if not it or not re.match(r"https?://", it.get("link") or ""):
        raise ValueError("This application has no link to a posting")
    return next(iter(jobsearch.check_links(ROOT, [it["link"]]).values()))


def post_search_check(q, body):
    rel = run_file(str(body["name"]))
    return start_job("jobcheck", body["name"], lambda: jobsearch.check_run(ROOT, rel))


def get_search_status(q, _):
    since, run = int(q1(q, "since", "0")), jobsearch.RUN
    return {**{k: v for k, v in run.items() if k != "log"}, "log": run["log"][since:], "next": len(run["log"])}


def get_rounds(q, _):
    with LOCK:
        rounds = learnings.read(ROOT, load_state())
    rel = q1(q, "path", "")
    return {"rounds": [r for r in rounds if not rel or r.get("path") == rel], "until": learnings.evaluated_until(ROOT)}


def get_profile(q, _):
    prof = userprofile.load(ROOT)
    return {"profile": prof, "categories": documents.CATEGORIES, "root": str(ROOT),
            "files": {k: {"path": v, "exists": userprofile.path(ROOT, prof, k).is_file()} for k, v in prof["files"].items()}}


def get_profile_file(q, _):
    prof, key = userprofile.load(ROOT), q1(q, "key")
    if key not in prof["files"]:
        raise ValueError("unknown profile file")
    p = userprofile.path(ROOT, prof, key)
    return {"content": p.read_text("utf-8") if p.is_file() else "", "path": prof["files"][key], "mtime": str(p.stat().st_mtime_ns) if p.is_file() else ""}


def get_templates(q, _):
    items = templates.listing(ROOT)
    for v in items:
        v["check"] = templates.check(ROOT, v["rel"], v["kind"], lang())
    return {"list": items, "chosen": userprofile.load(ROOT)["templates"], "layouts": list(templates.LAYOUTS)}


def get_template_preview(q, _):
    try:
        return {"pdf": templates.preview(ROOT, q1(q, "path"))}
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:  # LibreOffice missing or failing
        raise RuntimeError(f"Preview not possible (is LibreOffice installed?): {e}"[:300])


def get_log(q, _):
    p = resolve(q1(q, "path"))
    return {**pipeline.read(p), "file": pipeline.path(p).relative_to(ROOT).as_posix()}


def get_thunderbird(q, _):
    try:
        hits, total = thunderbird.search(q1(q, "q", "")[:200])
        return {"hits": hits, "total": total}
    except FileNotFoundError as e:
        return {"hits": [], "missing": str(e)}


def get_versions(q, _):
    return storage.versions(LAY.backups, resolve(q1(q, "path")))


def get_version(q, _):
    return {"content": storage.version_text(LAY.backups, resolve(q1(q, "path")), q1(q, "name"))}


def get_calendar(q, _):
    """All appointments and deadlines (or those of one letter) as .ics: for download and for subscribing in a calendar app."""
    if ROOT is None:
        raise ValueError("No data folder set up yet")
    entries = []
    sent = [it for it in letters() if it["status"] == "sent"] if "path" not in q else [{"path": q1(q, "path"), "company": resolve(q1(q, "path")).parent.name}]
    for it in sent:
        entries += [(it["company"], e) for e in pipeline.read(resolve(it["path"]))["events"]
                    if e["type"] in pipeline.DATE_TYPES and not (q1(q, "upcoming", "") and e["date"] < date.today().isoformat())]
    disp = "attachment" if q1(q, "download", "") else "inline"
    return pipeline.ics(entries, lang()).encode(), "text/calendar; charset=utf-8", [("Content-Disposition", f"{disp}; filename=applications.ics")]


EXPORT_SKIP = {".git", ".textcache", ".vorschau", "__pycache__", "node_modules"}


def get_export(q, _):
    """The whole data folder as one zip (backup). Written to a temp file and streamed, the folder can be large."""
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in EXPORT_SKIP]
            for f in files:
                full = Path(d) / f
                z.write(full, Path(ROOT.name) / full.relative_to(ROOT))
    name = f"{ROOT.name}_{date.today().isoformat()}.zip"
    return Path(tmp), "application/zip", [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")]


UPDATE = {"ts": 0, "value": None}


def repo_slug():
    """owner/name on GitHub: from the REPO file of a release zip, else from the git remote of the app folder."""
    if (DIR / "REPO").is_file():
        return (DIR / "REPO").read_text("utf-8").strip()
    try:
        url = subprocess.run(["git", "-C", str(DIR), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s*$", url)
    return m.group(1) if m else ""


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def get_update(q, _):
    """Newest release on GitHub, only when the user switched the check on; asked at most every 6 hours."""
    if not load_settings()["update_check"]:
        return {"enabled": False}
    slug = repo_slug()
    if not slug:
        return {"enabled": True, "current": VERSION, "error": "no repository known"}
    if time.time() - UPDATE["ts"] > 6 * 3600:
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{slug}/releases/latest", headers={"User-Agent": f"Application-Board/{VERSION}"})
            with urllib.request.urlopen(req, timeout=8) as r:
                j = json.loads(r.read())
            UPDATE["value"] = {"latest": j.get("tag_name", ""), "url": j.get("html_url", "")}
        except (OSError, ValueError) as e:
            UPDATE["value"] = {"error": str(e)[:200]}
        UPDATE["ts"] = time.time()
    v = UPDATE["value"] or {}
    return {"enabled": True, "current": VERSION, **v, "newer": bool(v.get("latest")) and vtuple(v["latest"]) > vtuple(VERSION)}


def file_response(p, inline):
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    disp = "inline" if inline and p.suffix.lower() in INLINE_TYPES else "attachment"
    return p.read_bytes(), ctype, [("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(p.name)}")]


GET = {
    "/api/setup": lambda q, b: setup_info(),
    "/api/letters": lambda q, b: letters(),
    "/api/letter": get_letter,
    "/api/job": lambda q, b: job_state(q1(q, "kind"), q1(q, "key", "")),
    "/api/settings": lambda q, b: public_settings(),
    "/api/providers": lambda q, b: public_settings()["providers"],
    "/api/search/runs": lambda q, b: jobsearch.runs(ROOT),
    "/api/search/run": get_search_run,
    "/api/search/config": lambda q, b: jobsearch.load_config(ROOT),
    "/api/search/sources": lambda q, b: jobsearch.sources.BUILTIN,
    "/api/search/status": get_search_status,
    "/api/dirs": lambda q, b: list_dirs(q1(q, "path", "")),
    "/api/rounds": get_rounds,
    "/api/memory": lambda q, b: {"dir": str(memory_dir()), "files": memory_files()},
    "/api/profile": get_profile,
    "/api/profile/file": get_profile_file,
    "/api/documents": lambda q, b: documents.listing(ROOT, userprofile.load(ROOT)),
    "/api/document/text": lambda q, b: {"text": documents.text(ROOT, documents.allowed(ROOT, userprofile.load(ROOT), q1(q, "path")))[:200000]},
    "/api/templates": get_templates,
    "/api/templates/preview": get_template_preview,
    "/api/pipeline": lambda q, b: pipeline_list(),
    "/api/log": get_log,
    "/api/thunderbird": get_thunderbird,
    "/api/versions": get_versions,
    "/api/usage": lambda q, b: ai.usage(),
    "/api/export": get_export,
    "/api/update": get_update,
    "/calendar.ics": get_calendar,
    "/api/version": get_version,
    "/file": lambda q, b: file_response(output_file(q1(q, "path")), True),
    "/doc": lambda q, b: file_response(documents.allowed(ROOT, userprofile.load(ROOT), q1(q, "path")), True),
}


def post_settings(q, body):
    with LOCK:
        return public_settings(update_settings(body))


def post_providers(q, body):
    with LOCK:
        return public_settings(delete_provider(body["delete"]) if "delete" in body else save_provider(body))


def post_provider_test(q, body):
    p = next((x for x in load_settings()["providers"] if x["id"] == body.get("id")), None)
    if not p:
        raise ValueError("unknown provider")
    try:
        return ai.test(p, cwd=ROOT)
    except ai.AIError as e:
        return {"ok": False, "text": str(e)}


def post_search_config(q, body):
    cfg = jobsearch.load_config(ROOT)
    for k, v in body.items():
        if k not in cfg:
            raise ValueError(f"unknown field {k}")
        cfg[k] = v
    try:
        jobsearch.check_config(cfg)
    except ValueError as e:
        raise ValueError((f"Diese Quelle oder Firma passt nicht: {e}" if lang() == "de" else f"This source or company does not fit: {e}"))
    jobsearch.save_config(ROOT, cfg)
    return cfg


def post_search_test(q, body):
    """Asks one source (an id of the catalogue, or an own source that need not be saved yet) for its first search term."""
    cfg, src = jobsearch.load_config(ROOT), body["source"]
    src = {**jobsearch.sources.clean_custom(src), "kind": "feed"} if isinstance(src, dict) else jobsearch.sources.BY_ID[src]
    return jobsearch.sources.preview(src, jobsearch.terms_of(cfg), cfg)


def post_search_start(q, body):
    mode = body.get("mode", "builtin")
    jobsearch.start(ROOT, provider_for("rate") if mode == "builtin" else None, mode, lang())
    return {"state": "running"}


def post_search_hide(q, body):
    cfg = jobsearch.load_config(ROOT)
    cfg["hidden"] = [u for u in cfg["hidden"] if u != body["url"]] + ([body["url"]] if body.get("hidden") else [])
    jobsearch.save_config(ROOT, cfg)
    return {"hidden": cfg["hidden"]}


def post_draft(q, body):
    job = {k: str(v)[:60000] for k, v in body["job"].items() if k in ("url", "text", "company", "title", "place", "md", "tone")}
    if job.get("url") and not re.match(r"https?://", job["url"]):
        raise ValueError("The link must start with http:// or https://")
    if not job.get("url") and not job.get("text", "").strip():
        raise ValueError("Link or posting text missing")
    if not job.get("company", "").strip() and not re.search(r"jobs\.ch/|jobup\.ch/|job-room\.ch/|linkedin\.com/jobs", job.get("url", "")):
        raise ValueError("Company missing")
    if not job.get("company", "").strip():  # the portals know the company
        try:
            meta = jobsearch.load_posting(job["url"])
            job["text"] = meta
            job["company"] = re.search(r"^Company:\s*(.+)$", meta, re.M).group(1).strip()
        except (AttributeError, OSError, ValueError) as e:
            raise ValueError(f"Company not found in the posting, please enter it ({e})")
    provider_for("draft")
    key = jobsearch.draft_key(job)
    return {**start_job("draft", key, run_draft, job), "key": key}


def post_memfile(q, body):
    allowed = {f["path"] for f in memory_files()}
    p = Path(body["path"]).resolve()
    if str(p) not in allowed:
        raise ValueError("Path not allowed")
    if str(body.get("mtime")) != str(p.stat().st_mtime_ns):  # as a string, JS loses precision with ns numbers
        raise FileExistsError("File was changed elsewhere")
    storage.write_atomic(p, body["content"])
    return {"mtime": str(p.stat().st_mtime_ns)}


def post_templates_generate(q, body):
    kind = body.get("kind", "cover_letter")
    if body.get("layout") not in templates.LAYOUTS or kind not in ("cover_letter", "cv"):
        raise ValueError("unknown layout")
    rel = (templates.cover_letter if kind == "cover_letter" else templates.cv)(ROOT, body["layout"], ui=lang())
    return {"rel": rel, "check": templates.check(ROOT, rel, kind, lang())}


def post_templates_upload(q, body):
    rel = templates.upload(ROOT, body["kind"], str(body["name"]), base64.b64decode(str(body["data"]), validate=True), lang())
    return {"rel": rel, "check": templates.check(ROOT, rel, body["kind"], lang())}


def post_suggestion(q, body):
    kind = body.get("kind")
    if kind not in userprofile.SUGGESTION:
        raise ValueError("unknown kind")
    provider_for("profile")  # fail early when no provider is chosen
    return start_job("suggestion", kind, run_suggestion, kind)


def post_learnings(q, body):
    provider_for("learnings")
    return start_job("learnings", "", run_learnings, bool(body.get("all")))


def post_learnings_write(q, body):
    files = learnings.write(memory_dir(), body.get("entries", []))
    if body.get("until"):
        learnings.set_evaluated_until(ROOT, str(body["until"]))
    return {"files": files}


def post_eml(q, body):
    raw = thunderbird.raw(body["token"]) if "token" in body else str(body["raw"])[:2_000_000]  # token: mail from the Thunderbird search
    ev = pipeline.eml(raw, userprofile.load(ROOT))
    return {**ev, "phase": pipeline.suggest_phase(ev["title"] + "\n" + ev["text"]) if ev["type"] == "Mail rein" else ""}


def post_log(q, body):
    p = resolve(body["path"])
    with LOCK:
        return {"mtime": pipeline.write(p, log_title(p), body, body.get("mtime", ""), lang())}


def busy(rel):
    return job_state("feedback", rel)["state"] == "running" or job_state("finalize", rel)["state"] == "running"


def post_finalize(q, body):
    rel = body["path"]
    resolve(rel)
    if busy(rel):
        raise FileExistsError("A job is already running for this letter")
    return start_job("finalize", rel, run_finalize, rel)


def post_apply(q, body):
    rel = body["path"]
    resolve(rel)
    if busy(rel):
        raise FileExistsError("The AI is already working on this letter")
    feedback = load_state().get(rel, {}).get("feedback", "")
    if not feedback.strip():
        raise ValueError("No stored feedback")
    provider_for("feedback")
    return start_job("feedback", rel, run_job, rel, feedback)


def post_appmail(q, body):
    resolve(body["path"])
    provider_for("followup")
    return start_job("appmail", body["path"], run_appmail, body["path"])


def post_compose(q, body):
    """Opens the mail in Thunderbird. Attachments only from the finalized files of this letter, never paths from the browser."""
    rel = body["path"]
    resolve(rel)
    to = str(body.get("to", "")).strip()
    if to and not re.fullmatch(r"[^\s@,;'\"<>]+@[^\s@,;'\"<>]+", to):
        raise ValueError("Invalid recipient")
    files = (load_state().get(rel) or {}).get("files") or {}
    attach = [ROOT / files[k] for k in ("cl_pdf", "cv_pdf") if body.get("attach") and files.get(k) and (ROOT / files[k]).is_file()]
    thunderbird.compose(to, str(body.get("subject", ""))[:500], str(body.get("body", ""))[:20000], [output_file(a.relative_to(ROOT).as_posix()) for a in attach])
    return {"ok": True, "attached": [a.name for a in attach]}


def post_prepare(q, body):
    resolve(body["path"])
    provider_for("prepare")
    return start_job("prepare", body["path"], run_prepare, body["path"])


def post_followup(q, body):
    resolve(body["path"])
    provider_for("followup")
    return start_job("followup", body["path"], run_followup, body["path"])


def post_letter(q, body):
    rel = body["path"]
    p = resolve(rel)
    if busy(rel) and "content" in body:
        raise FileExistsError("The AI is working on this letter right now")
    reply = {}
    if "content" in body:
        # protection against overwriting when the file was changed elsewhere meanwhile
        if str(body.get("mtime")) != str(p.stat().st_mtime_ns):  # as a string, JS loses precision with ns numbers
            raise FileExistsError("File was changed elsewhere")
        save_letter(p, rel, body["content"])
        reply["mtime"] = str(p.stat().st_mtime_ns)
        if body.get("undo"):  # for the memory of the next round and the learnings
            learnings.append(ROOT, {"path": rel, "company": p.parent.name, "kind": "undo", "feedback": "",
                                    "answer": "The change of this round was rejected."})
    with LOCK:
        state = load_state()
        entry = state.setdefault(rel, {"status": "open", "feedback": ""})
        if "feedback" in body:
            entry["feedback"] = body["feedback"]
        if "status" in body:
            if body["status"] not in STATUSES:
                raise ValueError("unknown status")
            if body["status"] == "sent" and entry.get("status") != "sent":
                entry.setdefault("phase", "sent")
                pipeline.add_event(p, log_title(p), "status", pipeline.phase_name("sent", p, lang()), lang=lang())
            entry["status"] = body["status"]
            entry["since"] = date.today().isoformat()
        if "pinned" in body:
            if body["pinned"]:
                entry["pinned"] = True
            else:
                entry.pop("pinned", None)
        if "phase" in body:
            if body["phase"] not in pipeline.PHASES:
                raise ValueError("unknown phase")
            if entry.get("phase", "sent") != body["phase"]:
                pipeline.add_event(p, log_title(p), "status", pipeline.phase_name(body["phase"], p, lang()), lang=lang())
            entry["phase"] = body["phase"]
            reply["log_mtime"] = pipeline.read(p)["mtime"]
        save_state(state)
    return reply


POST = {
    "/api/settings": post_settings,
    "/api/providers": post_providers,
    "/api/providers/test": post_provider_test,
    "/api/search/config": post_search_config,
    "/api/search/start": post_search_start,
    "/api/search/stop": lambda q, b: (jobsearch.stop(), {"state": jobsearch.RUN["state"]})[1],
    "/api/search/hide": post_search_hide,
    "/api/search/check": post_search_check,
    "/api/search/test": post_search_test,
    "/api/posting/check": post_posting_check,
    "/api/search/draft": post_draft,
    "/api/memfile": post_memfile,
    "/api/profile": lambda q, b: {"profile": userprofile.save(ROOT, b)},
    "/api/profile/file": lambda q, b: {"mtime": save_profile_file(b["key"], str(b["content"]), b.get("mtime"))},
    "/api/profile/suggestion": post_suggestion,
    "/api/document": lambda q, b: {"rel": documents.store(ROOT, b["category"], str(b["name"]), base64.b64decode(str(b["data"]), validate=True))},
    "/api/document/delete": lambda q, b: (documents.delete(ROOT, str(b["rel"])), {})[1],
    "/api/setup": lambda q, b: setup(b),
    "/api/templates/generate": post_templates_generate,
    "/api/templates/upload": post_templates_upload,
    "/api/learnings": post_learnings,
    "/api/learnings/write": post_learnings_write,
    "/api/eml": post_eml,
    "/api/log": post_log,
    "/api/prepare": post_prepare,
    "/api/followup": post_followup,
    "/api/appmail": post_appmail,
    "/api/compose": post_compose,
    "/api/finalize": post_finalize,
    "/api/apply": post_apply,
    "/api/cancel": lambda q, b: cancel_job(str(b["kind"]), str(b.get("key", ""))),
    "/api/letter": post_letter,
}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json; charset=utf-8", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")  # an uploaded file is never sniffed into HTML
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)  # second line of defence behind DOMPurify
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def foreign(self, writing):
        """Protection against other web pages in the same browser: without this check any page could POST to
        127.0.0.1 (e.g. create a cli provider with any command) or read answers through DNS rebinding.
        Returns an error text or None."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if self.headers.get("Host", "") not in allowed:  # DNS rebinding: a foreign name pointing to 127.0.0.1
            return "Host not allowed"
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in allowed:
            return "Origin not allowed"
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and urlparse(self.path).path.startswith("/api/"):
            return "Request from a foreign site"
        # own header: foreign pages can only set it after a CORS preflight, which this server never allows
        if writing and self.headers.get("X-Bewerben") != "1":
            return "Header X-Bewerben missing"
        return None

    def static(self, path):
        if path in STATIC:
            f = DIR / STATIC[path]
        elif path.startswith("/vendor/"):
            f = (DIR / path.lstrip("/")).resolve()
            if not f.is_relative_to(VENDOR) or not f.is_file():
                return False
        else:
            return False
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith(("text/", "application/javascript", "image/svg")):
            ctype += "; charset=utf-8"
        self.send(200, f.read_bytes(), ctype, [("Cache-Control", "max-age=86400")] if path.startswith("/vendor/") else ())
        return True

    def dispatch(self, routes, route, q, body):
        if ROOT is None and route.startswith("/api/") and route not in OPEN_WITHOUT_FOLDER:
            return self.send(409, {"error": "No data folder set up yet", "setup": True})
        fn = routes.get(route)
        if not fn:
            return self.send(404, {"error": "not found"})
        try:
            res = fn(q, body)
        except FileExistsError as e:  # conflict: changed elsewhere or already running
            return self.send(409, {"error": str(e)})
        except (KeyError, ValueError, TypeError, FileNotFoundError, PermissionError, json.JSONDecodeError, ai.AIError, RuntimeError) as e:
            return self.send(400, {"error": f"missing field {e}" if isinstance(e, KeyError) else str(e)})
        except Exception as e:  # unexpected: answer anyway so the page does not report "server not reachable"
            traceback.print_exc()
            return self.send(500, {"error": f"{type(e).__name__}: {e}"[:500]})
        if isinstance(res, tuple) and isinstance(res[0], Path):  # large file (export): stream it, then remove the temp file
            try:
                self.send_response(200)
                for k, v in [("Content-Type", res[1]), ("Content-Length", str(res[0].stat().st_size)), ("Cache-Control", "no-store"),
                             ("X-Content-Type-Options", "nosniff"), *res[2]]:
                    self.send_header(k, v)
                self.end_headers()
                with res[0].open("rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            finally:
                res[0].unlink(missing_ok=True)
            return
        if isinstance(res, tuple):
            return self.send(200, res[0], res[1], res[2])
        self.send(200, res)

    def do_GET(self):
        url = urlparse(self.path)
        if error := self.foreign(False):
            return self.send(403, {"error": error})
        if not url.path.startswith("/api/") and self.static(url.path):
            return
        self.dispatch(GET, url.path, parse_qs(url.query), {})

    def do_POST(self):
        url = urlparse(self.path)
        if error := self.foreign(True):
            return self.send(403, {"error": error})
        if url.path not in POST:
            return self.send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        if length > 45 * 1024 * 1024:  # documents come base64 encoded, a 30 MB file is about 40 MB
            return self.send(413, {"error": "Request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send(400, {"error": "invalid JSON"})
        self.dispatch(POST, url.path, {}, body)

    def log_message(self, *args):
        pass


def daily_search(every=300):
    """Starts the built-in job search once a day when it is switched on in the search settings (while the server runs)."""
    tried = ""
    while True:
        time.sleep(every)
        try:
            today = date.today().isoformat()
            if ROOT and tried != today and jobsearch.due(ROOT, jobsearch.load_config(ROOT)):
                tried = today  # one attempt per day, also when it fails
                jobsearch.start(ROOT, provider_for("rate"), "builtin", lang())
        except Exception:
            traceback.print_exc()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    open_browser = "--open" in args
    args = [a for a in args if a != "--open"]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    try:
        set_root(load_settings()["root"])
    except ValueError as e:
        print(f"Stored folder not usable ({e}), using {DEFAULT_ROOT}")
        try:
            set_root(DEFAULT_ROOT)
        except ValueError:
            print("No data folder yet: the setup starts in the browser.")
    port = int(args[0]) if args else 8765
    thunderbird.CACHE = storage.config_file("mailindex.json.gz")  # the mail index survives a restart
    thunderbird.warm_up()  # mail index in the background so the first search in the pipeline does not wait
    threading.Thread(target=daily_search, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Application Board {VERSION}: {url}  Folder: {ROOT or '(none yet)'}  Settings: {storage.config_dir()}  (Ctrl+C quits)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
