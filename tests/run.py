#!/usr/bin/env python3
"""All tests in one command: python3 tests/run.py [--ui] [--keep]

1. the self-test of every module (each prints "ok")
2. an API test against a real server on a free port, with a demo data folder built from example/ and a mock AI
   provider (a small script, no network, no costs)
3. with --ui: a browser test (tests/ui_smoke.mjs, needs node and chromium)

Nothing touches your own data or settings: data folder and config folder are temporary (--keep leaves them).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
# every process started by the tests gets its own config folder: never the real settings, keys or mail index
os.environ["APPLICATION_BOARD_CONFIG"] = tempfile.mkdtemp(prefix="application-board-test-config-")
os.environ.pop("BEWERBUNGS_BOARD_CONFIG", None)
MODULES = ["storage", "ai", "userprofile", "documents", "templates", "thunderbird", "learnings", "pipeline", "finalizing", "jobsearch"]
FAILED = []

LETTER_DE = """# Infos zur Jobausschreibung
**Titel:** Junior Developer (80–100%)
**Firma:** Muster AG
**Kontakt:** Anna Beispiel, anna.beispiel@example.com
**Link:** [jobs.ch](https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-000000000001/)

## Aufgaben
- Software entwickeln

# Requirements & Skills Mapping
**C#**
- Studienprojekte

# Bewerbungsschreiben

Vorname Nachname
Bern

Muster AG
Bern

Bern, 1. Januar 2026

Sehr geehrte Frau Beispiel

Ich bewerbe mich auf die Stelle als Junior Developer. alt

Ich freue mich auf ein Gespräch.

Freundliche Grüsse
Vorname Nachname
"""
LETTER_EN = LETTER_DE.replace("# Bewerbungsschreiben", "# Cover Letter").replace("Sehr geehrte Frau Beispiel", "Dear Ms Example") \
    .replace("Bern, 1. Januar 2026", "Bern, 1 January 2026").replace("**Titel:**", "**Title:**").replace("Muster AG", "Example Ltd")
RUN = """# Stellensuche 2026-01-02

**Quellen:** test

## A, passt direkt

### Junior Developer bei Muster AG, Bern `NEU`
- **Link:** https://www.jobs.ch/de/stellenangebote/detail/00000000-0000-0000-0000-000000000001/
- **Wertung:** 9/10
- **Dafuer:** <img src=x onerror="window.__xss=1">C#
- **Luecken:** keine wesentliche
"""
MOCK = r'''import sys, json, time
sys.stdout.reconfigure(encoding="utf-8")  # the board talks UTF-8 with every command, also on Windows
p = sys.stdin.buffer.read().decode("utf-8")
open(__file__ + ".log", "a", encoding="utf-8").write("=====\n" + p[:4000] + "\n")
if "SLOW" in p:
    time.sleep(30)
if "claim about the applicant" in p:
    print(json.dumps({"claims": ["Die Zahl 2031 steht nicht im Profil."]}))
elif "FEEDBACK ROUNDS" in p:
    print(json.dumps({"learnings": [{"file": "feedback_test.md", "name": "test", "title": "Test", "description": "d", "type": "feedback", "body": "Rule."}]}))
elif "final check" in p:
    print(json.dumps({"probleme": ["Betreff zu lang"]}))
elif "You rate a job posting" in p:
    print(json.dumps({"note": "B", "score": 6, "pros": "x", "gaps": "y"}))
elif "job interview" in p:
    print("### About\n- x")
elif "goes with the application" in p:
    print("SUBJECT: Bewerbung als Junior Developer\nGuten Tag Frau Beispiel\n\nAnbei meine Unterlagen.\n\nFreundliche Grüsse")
elif "follow-up e-mail" in p:
    print("SUBJECT: Nachfrage\nGuten Tag\n\nWie ist der Stand?\n\nGruss")
elif "=== FILE " in p:
    letter = p.split("=== FILE ", 1)[1].split("===\n", 1)[1]
    print("===FILE===\n" + letter.replace(" alt\n", " neu, seit 2031 mit AWS\n" if "ERFINDE" in p else " neu\n") + "\n===END===\n===SUMMARY===\nalt durch neu ersetzt")
elif "Write a complete application file" in p:
    print("===FILE===\n# Infos zur Jobausschreibung\n**Titel:** Dev\n\n# Bewerbungsschreiben\n\nText\n===END===\n===SUMMARY===\nok")
else:
    print("===FILE===\n# Profil\n\nNeu\n===END===\n===SUMMARY===\nok")
'''


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f": {detail}"))
    if not ok:
        FAILED.append(name)


def self_tests():
    print("self-tests")
    for m in MODULES:
        r = subprocess.run([sys.executable, str(APP / f"{m}.py")], capture_output=True, text=True, cwd=APP,
                           env={**os.environ, "APPLICATION_BOARD_CONFIG": tempfile.mkdtemp()})
        check(m, r.returncode == 0 and r.stdout.strip().endswith("ok"), (r.stdout + r.stderr)[-400:])


def demo_folder(tmp):
    data = tmp / "data"
    shutil.copytree(APP / "example" / "de", data)
    for folder, text in (("Muster AG", LETTER_DE), ("Example Ltd", LETTER_EN)):
        (data / "Bewerbungen" / folder).mkdir(parents=True, exist_ok=True)
        (data / "Bewerbungen" / folder / f"Bewerbungsschreiben_{folder.split()[0]}.md").write_text(text, "utf-8")
    (data / "Bewerbungen" / "_Stellensuche").mkdir()
    (data / "Bewerbungen" / "_Stellensuche" / "Stellensuche_2026-01-02.md").write_text(RUN, "utf-8")
    (data / "Profil" / "Dokumente" / "Sonstiges").mkdir(parents=True)
    (data / "Profil" / "Dokumente" / "Sonstiges" / "evil.html").write_text("<script>alert(1)</script>")
    prof = json.loads((data / "bewerbungs-board.json").read_text("utf-8"))
    prof.update(name="Vorname Nachname", email="vorname@example.com", regelcheck={"woerter_min": 300, "woerter_max": 350, "zeichen": ["ß"]})
    (data / "bewerbungs-board.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), "utf-8")
    return data


class Api:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, path, body=None, headers=None, raw=False):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Bewerben": "1", "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return (r.status, dict(r.headers), data) if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            if raw:
                return e.code, dict(e.headers), data
            return {"_status": e.code, **json.loads(data or b"{}")}

    def wait(self, kind, key, limit=60):
        for _ in range(limit * 4):
            j = self.call(f"/api/job?kind={kind}&key=" + urllib.parse.quote(key))
            if j["state"] != "running":
                return j
            time.sleep(0.25)
        return j


def api_tests(tmp):
    print("API")
    data, cfg = demo_folder(tmp), tmp / "config"
    cfg.mkdir()
    (tmp / "mock.py").write_text(MOCK, "utf-8")
    prov = {"id": "mock", "name": "Mock", "type": "cli", "cmd": f'"{sys.executable}" "{tmp / "mock.py"}"'}
    (cfg / "settings.json").write_text(json.dumps({"root": str(data), "lang": "de", "providers": [prov],
                                                    "tasks": {t: "mock" for t in ("feedback", "rate", "draft", "prepare", "followup", "learnings", "profile", "check")}}))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp / "bin").mkdir()  # a fake thunderbird that only writes down its arguments
    fake = tmp / "bin" / "thunderbird"
    fake.write_text(f"#!{sys.executable}\nimport sys, json\nopen({str(tmp / 'tb.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
    fake.chmod(0o755)
    env = {**os.environ, "APPLICATION_BOARD_CONFIG": str(cfg), "HOME": str(tmp / "home"),  # HOME: no real Claude memory, no Thunderbird
           "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
    (tmp / "home").mkdir()
    server = subprocess.Popen([sys.executable, str(APP / "server.py"), str(port)], env=env, stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT)
    a = Api(port)
    try:
        for _ in range(40):
            try:
                a.call("/api/settings")
                break
            except OSError:
                time.sleep(0.25)
        run_api(a, data, tmp)
    finally:
        server.terminate()
        server.wait(5)
    return port, data, cfg, env


def run_api(a, data, tmp):
    de, en = "Bewerbungen/Muster AG/Bewerbungsschreiben_Muster.md", "Bewerbungen/Example Ltd/Bewerbungsschreiben_Example.md"
    letters = {i["path"]: i for i in a.call("/api/letters")}
    check("letters listed with language and score", letters.get(de, {}).get("lang") == "de" and letters.get(en, {}).get("lang") == "en"
          and letters[de].get("score") == 9, letters)
    # security
    status, headers, _ = a.call("/", raw=True)
    check("CSP and nosniff on the page", "script-src 'self'" in headers.get("Content-Security-Policy", "") and headers.get("X-Content-Type-Options") == "nosniff", headers)
    status, headers, _ = a.call("/doc?path=" + urllib.parse.quote("Profil/Dokumente/Sonstiges/evil.html"), raw=True)
    check("uploaded HTML is a download, not a page", status == 200 and headers.get("Content-Disposition", "").startswith("attachment"), headers)
    check("no path traversal out of vendor/", a.call("/vendor/../server.py", raw=True)[0] == 404)
    check("POST without header refused", a.call("/api/letter", {"path": de}, headers={"X-Bewerben": "0"}).get("_status") == 403)
    check("foreign host refused", a.call("/api/letters", headers={"Host": "evil.example:80"}).get("_status") == 403)
    check("unknown field gives 400, not a dropped connection", a.call("/api/letter", {}).get("_status") == 400)
    # feedback round, versions, undo
    a.call("/api/letter", {"path": de, "feedback": "alt ersetzen"})
    a.call("/api/apply", {"path": de})
    j = a.wait("feedback", de)
    check("feedback applied", j["state"] == "done" and j.get("changed") and " neu" in (data / de).read_text("utf-8"), j)
    v = a.call("/api/versions?path=" + urllib.parse.quote(de))
    check("version kept before the round", len(v) == 1 and " alt" in a.call(f"/api/version?path={urllib.parse.quote(de)}&name={urllib.parse.quote(v[0]['name'])}")["content"], v)
    letter = a.call("/api/letter?path=" + urllib.parse.quote(de))
    r = a.call("/api/letter", {"path": de, "content": j["before"], "mtime": letter["mtime"], "undo": True})
    rounds = a.call("/api/rounds?path=" + urllib.parse.quote(de))["rounds"]
    check("undo noted in the history", "mtime" in r and rounds[-1].get("kind") == "undo", rounds)
    stale = a.call("/api/letter", {"path": de, "content": "x", "mtime": "1"})
    check("stale save is a conflict", stale.get("_status") == 409, stale)
    # cancel
    a.call("/api/letter", {"path": en, "feedback": "SLOW"})
    a.call("/api/apply", {"path": en})
    time.sleep(1)
    a.call("/api/cancel", {"kind": "feedback", "key": en})
    j = a.wait("feedback", en, 10)
    check("running job cancelled", j["state"] == "cancelled" and "Dear Ms Example" in (data / en).read_text("utf-8"), j)
    # fact check: invented numbers and names are reported
    a.call("/api/letter", {"path": en, "feedback": "ERFINDE etwas"})
    a.call("/api/apply", {"path": en})
    j = a.wait("feedback", en)
    check("invented facts reported", j["state"] == "done" and {"2031", "AWS"} <= set(j.get("facts", [])) and "AWS" in j.get("text", ""), j)
    letter = a.call("/api/letter?path=" + urllib.parse.quote(en))
    a.call("/api/letter", {"path": en, "content": j["before"], "mtime": letter["mtime"], "feedback": "ERFINDE nochmals"})
    a.call("/api/settings", {"tasks": {"facts": "mock"}})
    a.call("/api/apply", {"path": en})
    j = a.wait("feedback", en)
    a.call("/api/settings", {"tasks": {"facts": ""}})
    check("AI fact check when chosen", j["state"] == "done" and "KI-Fakten-Check: Die Zahl 2031" in j.get("text", ""), j)
    usage = a.call("/api/usage")
    check("usage recorded per task", any(u["task"] == "feedback" and u["n"] >= 2 for u in usage) and any(u["task"] == "facts" for u in usage), usage)
    # rule check comes from the profile
    check("rule check from the profile", a.call("/api/settings")["rulecheck"]["woerter_min"] == 300)
    # status, pipeline, follow-up
    a.call("/api/letter", {"path": de, "status": "abgeschickt"})
    a.call("/api/letter", {"path": de, "phase": "gespraech"})
    log = a.call("/api/log?path=" + urllib.parse.quote(de))
    check("status events in the log", [e["title"] for e in log["events"]] == ["Gespräch", "Abgeschickt"], log["events"])
    pipe = a.call("/api/pipeline")
    check("pipeline lists the sent letter", [p["path"] for p in pipe] == [de] and pipe[0]["mails"] == [], pipe)
    a.call("/api/followup", {"path": de})
    f = a.wait("followup", de)
    check("follow-up drafted with contact", f["state"] == "done" and f.get("to") == "anna.beispiel@example.com" and f.get("subject") == "Nachfrage", f)
    log["events"].insert(0, {"date": "2099-01-05", "time": "09:30", "type": "Termin", "title": "Gespräch", "text": ""})
    a.call("/api/log", {"path": de, "mtime": log["mtime"], **{k: log[k] for k in ("events", "preparation", "notes")}})
    status, headers, cal = a.call("/calendar.ics", raw=True)
    check("calendar to subscribe", status == 200 and headers.get("Content-Type", "").startswith("text/calendar") and b"DTSTART:20990105T093000" in cal
          and "Bewerbung: Muster AG\\, Gespräch".encode() in cal, cal[:400])
    a.call("/api/appmail", {"path": de})
    am = a.wait("appmail", de)
    check("application mail drafted", am["state"] == "done" and am.get("to") == "anna.beispiel@example.com" and am.get("subject") == "Bewerbung als Junior Developer", am)
    check("compose refuses a bad recipient", a.call("/api/compose", {"path": de, "to": "x@y.ch,evil@z.ch"}).get("_status") == 400)
    a.call("/api/prepare", {"path": de})
    check("interview preparation", a.wait("prepare", de)["state"] == "done" and "### About" in a.call("/api/log?path=" + urllib.parse.quote(de))["preparation"])
    a.call("/api/letter", {"path": de, "status": "archiv"})
    check("archive keeps the letter, leaves the pipeline", a.call("/api/pipeline") == [] and any(i["status"] == "archiv" for i in a.call("/api/letters")))
    # learnings, draft from text, job search parse
    a.call("/api/learnings", {"all": True})
    lj = a.wait("learnings", "")
    check("learnings summarized", lj["state"] == "done" and lj["suggestions"][0]["file"] == "feedback_test.md", lj)
    d = a.call("/api/search/draft", {"job": {"text": "Wir suchen eine Person für C#.", "company": "Neu GmbH", "title": "Dev", "tone": "concise"}})
    dj = a.wait("draft", d.get("key", ""))
    check("application drafted from pasted text", dj["state"] == "done" and (data / dj.get("path", "x")).is_file() and "posting" not in dj, dj)
    check("tone goes into the draft prompt", "Tone of the letter: short and direct" in (tmp / "mock.py.log").read_text("utf-8"))
    check("update check off by default", a.call("/api/update") == {"enabled": False})
    status, headers, z = a.call("/api/export", raw=True)
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z)).namelist() if status == 200 else []
    check("data folder exported as zip", any(n.endswith("Bewerbungsschreiben_Muster.md") for n in names) and not any("/.git/" in n for n in names), names[:5])
    mcp_tests(data)
    run = a.call("/api/search/run?name=Stellensuche_2026-01-02")
    check("job search run parsed", run["jobs"][0]["score"] == 9 and run["jobs"][0]["company"] == "Muster AG", run)
    gone = a.base + "/no-such-posting"  # the board itself answers 404: a posting that is gone
    (data / "Bewerbungen" / "_Stellensuche" / "Stellensuche_2026-01-03.md").write_text(f"# Stellensuche\n\n## A, passt direkt\n\n### Dev bei Weg AG, Ort\n- **Link:** {gone}\n- **Wertung:** 9/10\n", "utf-8")
    a.call("/api/search/check", {"name": "Stellensuche_2026-01-03"})
    cj = a.wait("jobcheck", "Stellensuche_2026-01-03")
    run = a.call("/api/search/run?name=Stellensuche_2026-01-03")
    (data / "Bewerbungen" / "Weg AG").mkdir()
    (data / "Bewerbungen" / "Weg AG" / "Bewerbungsschreiben_Weg.md").write_text(f"**Titel:** Dev\n**Link:** [Anzeige]({gone})\n\n# Bewerbungsschreiben\n\nText\n", "utf-8")
    pc = a.call("/api/posting/check", {"path": "Bewerbungen/Weg AG/Bewerbungsschreiben_Weg.md"})
    check("posting of one application checked", pc.get("status") == "gone" and pc.get("grund") == "HTTP 404", pc)
    check("posting that is gone is marked", cj.get("gone") == 1 and run["jobs"][0]["check"]["status"] == "gone", (cj, run["jobs"]))
    # moved folder keeps its state
    (data / "Bewerbungen" / "Muster AG").rename(data / "Bewerbungen" / "Muster AG neu")
    moved = {i["path"]: i for i in a.call("/api/letters")}.get("Bewerbungen/Muster AG neu/Bewerbungsschreiben_Muster.md", {})
    check("renamed folder keeps status", moved.get("status") == "archiv", moved)
    check("history follows the rename", len(a.call("/api/rounds?path=" + urllib.parse.quote("Bewerbungen/Muster AG neu/Bewerbungsschreiben_Muster.md"))["rounds"]) >= 2)
    # finalize, only with LibreOffice
    sys.path.insert(0, str(APP))
    import storage
    if storage.tool("soffice"):
        g = a.call("/api/templates/generate", {"kind": "anschreiben", "layout": "klassisch"})
        a.call("/api/profile", {"vorlagen": {"anschreiben": g["rel"], "lebenslauf": ""}})
        a.call("/api/finalize", {"path": en})
        fj = a.wait("finalize", en, 120)
        check("finalized as docx and pdf", fj["state"] == "done" and (data / fj["files"]["cl_pdf"]).is_file(), fj)
        if os.name != "nt":
            r = a.call("/api/compose", {"path": en, "to": "hr@example.com", "subject": "Application", "body": "Hello", "attach": True})
            for _ in range(40):
                if (tmp / "tb.json").is_file():
                    break
                time.sleep(0.1)
            arg = json.loads((tmp / "tb.json").read_text())
            check("mail opens in Thunderbird with the PDFs", r["attached"] and arg[0] == "-compose" and "to='hr@example.com'" in arg[1]
                  and Path(fj["files"]["cl_pdf"]).name.replace(" ", "%20") in arg[1], (r, arg))
    else:
        print("  skip finalize (LibreOffice not found)")


def mcp_tests(data):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_applications", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "save_draft", "arguments": {"company": "MCP AG", "content": "# Bewerbungsschreiben\n\nText"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "read_application", "arguments": {"path": "../etc/passwd"}}}]
    r = subprocess.run([sys.executable, str(APP / "mcp_server.py"), str(data)], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": tempfile.mkdtemp()})
    out = {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}
    check("MCP: initialize and tools", out[1]["result"]["serverInfo"]["name"] == "application-board" and len(out[2]["result"]["tools"]) == 5 and len(out) == 5, r.stderr[-300:])
    apps = json.loads(out[3]["result"]["content"][0]["text"])
    check("MCP: list applications", any(x["company"] == "Muster AG" for x in apps), apps)
    check("MCP: save draft, never overwriting", (data / json.loads(out[4]["result"]["content"][0]["text"])["path"]).is_file(), out[4])
    check("MCP: no path outside the letters", out[5]["result"]["isError"] is True, out[5])


def ui_tests(port, env):
    print("UI")
    node, chromium = shutil.which("node"), next((shutil.which(c) for c in ("chromium", "chromium-browser", "google-chrome") if shutil.which(c)), None)
    if not node or not chromium:
        print("  skip (node or chromium missing)")
        return
    server = subprocess.Popen([sys.executable, str(APP / "server.py"), str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        r = subprocess.run([node, str(APP / "tests" / "ui_smoke.mjs"), str(port), chromium], capture_output=True, text=True, timeout=240)
        print(r.stdout.rstrip())
        check("browser test", r.returncode == 0, r.stderr[-800:])
    finally:
        server.terminate()
        server.wait(5)


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="application-board-test-"))
    self_tests()
    port, data, cfg, env = api_tests(tmp)
    if "--ui" in sys.argv:
        ui_tests(port, env)
    if "--keep" in sys.argv:
        print("kept:", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
