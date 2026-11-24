"""Pipeline after sending: log (mails, dates, status), interview preparation, notes.

One file per letter next to it: letter_X.md -> log_X.md (Bewerbungsschreiben_X.md -> Verlauf_X.md in folders of the
German layout). It is plain Markdown so it can be
read and edited without the board:

    # Verlauf: Company, Title                 # Log: Company, Title
    ## Ereignisse                              ## Events
    ### 2026-11-12 14:30 · Mail rein · ...     ### 2026-11-12 14:30 · Mail in · ...
    ## Vorbereitung                            ## Preparation
    ## Notizen                                 ## Notes

German and English files are both read; a file is written back in the language it already has, new files in the
UI language. Internally and in the API, event types and phases are English ids (mail_in, appointment, ...; sent,
replied, ...); the phase is stored in the state file with the letter (German values in folders of the German layout).
"""
import email
import email.policy
import re
import unicodedata
from datetime import date
from email.utils import parsedate_to_datetime

import ai
import layout
import storage
import userprofile

PHASES = ("sent", "no_reply", "replied", "interview", "offer", "rejected")
GHOST_DAYS = 30  # this long without any activity after sending (or after a reply): phase "no_reply"
PHASE_NAMES = {
    "de": {"sent": "Abgeschickt", "no_reply": "Keine Antwort", "replied": "Antwort erhalten", "interview": "Gespräch", "offer": "Angebot", "rejected": "Absage / Zurückgezogen"},
    "en": {"sent": "Sent", "no_reply": "No reply", "replied": "Reply received", "interview": "Interview", "offer": "Offer", "rejected": "Rejected / Withdrawn"},
}
TYPES = ("mail_in", "mail_out", "appointment", "deadline", "status", "note")
TYPE_NAMES = {  # as written in the log files
    "de": {"mail_in": "Mail rein", "mail_out": "Mail raus", "appointment": "Termin", "deadline": "Frist", "status": "Status", "note": "Notiz"},
    "en": {"mail_in": "Mail in", "mail_out": "Mail out", "appointment": "Appointment", "deadline": "Deadline", "status": "Status", "note": "Note"},
}
DATE_TYPES = ("appointment", "deadline")
HEADINGS = {
    "de": {"title": "Verlauf", "events": "Ereignisse", "preparation": "Vorbereitung", "notes": "Notizen"},
    "en": {"title": "Log", "events": "Events", "preparation": "Preparation", "notes": "Notes"},
}
SECTION_KEYS = {name: key for h in HEADINGS.values() for key, name in h.items() if key != "title"}
EVENT_HEAD = re.compile(r"^### (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? · ([^·\n]+?)(?: · (.*))?$", re.M)


def path(letter):
    """Log file of a letter (Path): letter_X.md -> log_X.md, Bewerbungsschreiben_X.md -> Verlauf_X.md."""
    for (le, lo) in zip(layout.NAMES["letter"], layout.NAMES["log"]):
        if letter.name.startswith(le):
            return letter.with_name(lo + letter.name[len(le):])
    raise ValueError(f"not a letter: {letter.name}")


def parse(text):
    """Markdown -> {"events", "preparation", "notes", "lang"}; German and English headings both work."""
    names = "|".join(SECTION_KEYS)
    parts = {"events": "", "preparation": "", "notes": ""}
    for m in re.finditer(rf"^## ({names})[ \t]*\n(.*?)(?=^## (?:{names})[ \t]*$|\Z)", text, re.M | re.S):
        parts[SECTION_KEYS[m.group(1)]] = m.group(2).strip()
    canonical = {name: t for names in TYPE_NAMES.values() for t, name in names.items()}
    events, heads = [], list(EVENT_HEAD.finditer(parts["events"]))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(parts["events"])
        body = parts["events"][m.end():end].strip()
        kind = m.group(3).strip()
        events.append({"date": m.group(1), "time": m.group(2) or "", "type": canonical.get(kind, kind), "title": (m.group(4) or "").strip(),
                       "text": re.sub(r"^\\(#)", r"\1", body, flags=re.M)})
    lang = "en" if re.search(r"^## (Events|Preparation|Notes)\b", text, re.M) else "de"
    return {"events": ordered(events), "preparation": parts["preparation"], "notes": parts["notes"], "lang": lang}


def ordered(events):
    return sorted(events, key=lambda e: (e["date"], e.get("time", "")), reverse=True)


def render(title, d, lang="de"):
    h = HEADINGS.get(lang, HEADINGS["de"])
    lines = [f"# {h['title']}: {title}", "", f"## {h['events']}", ""]
    for e in ordered(d.get("events", [])):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", "")):
            raise ValueError(f"Invalid date: {e.get('date')!r}")
        if e.get("type") not in TYPES:
            raise ValueError(f"Invalid type: {e.get('type')!r}")
        time = e.get("time", "")
        if time and not re.fullmatch(r"\d{2}:\d{2}", time):
            raise ValueError(f"Invalid time: {time!r}")
        kind = TYPE_NAMES.get(lang, TYPE_NAMES["de"])[e["type"]]
        head = " ".join(filter(None, [e["date"], time])) + " · " + kind
        event_title = " ".join(str(e.get("title", "")).replace("·", "-").split())
        lines.append(f"### {head}" + (f" · {event_title}" if event_title else ""))
        text = str(e.get("text", "")).strip()
        if text:  # lines starting with # would otherwise break the structure as headings
            lines.append(re.sub(r"^#", r"\\#", text, flags=re.M))
        lines.append("")
    for key in ("preparation", "notes"):
        content = str(d.get(key, "")).strip()
        lines += [f"## {h[key]}", ""] + ([content, ""] if content else [])
    return "\n".join(lines).rstrip() + "\n"


def read(letter):
    p = path(letter)
    if not p.is_file():
        return {"events": [], "preparation": "", "notes": "", "lang": "", "mtime": ""}
    return {**parse(p.read_text("utf-8")), "mtime": str(p.stat().st_mtime_ns)}


def write(letter, title, d, mtime=None, lang="de"):
    """mtime=None means: without conflict check (server-side additions under LOCK). An existing file keeps its
    language; lang applies to new files."""
    p = path(letter)
    current = str(p.stat().st_mtime_ns) if p.is_file() else ""
    if mtime is not None and str(mtime) != current:
        raise FileExistsError("The log was changed elsewhere in the meantime")
    if p.is_file():
        lang = parse(p.read_text("utf-8"))["lang"] or lang
    storage.write_atomic(p, render(title, d, lang))
    return str(p.stat().st_mtime_ns)


def add_event(letter, title, kind, event_title, text="", lang="de"):
    """Adds an event dated today (e.g. a status change)."""
    d = read(letter)
    d["events"].insert(0, {"date": date.today().isoformat(), "time": "", "type": kind, "title": event_title, "text": text})  # newest first; sorting is stable
    write(letter, title, d, lang=lang)


def phase_name(phase, letter, lang="de"):
    """Name of a phase for a status event, in the language of the letter's log (or lang for a new log)."""
    use = read(letter)["lang"] or lang
    return PHASE_NAMES.get(use, PHASE_NAMES["de"])[phase]


def summary(d, since=""):
    """Last activity, next date and all dates for board and agenda."""
    today = date.today().isoformat()
    dates = [e for e in d["events"] if e["type"] in DATE_TYPES]
    upcoming = sorted((e for e in dates if e["date"] >= today), key=lambda e: (e["date"], e["time"]))
    past = [e["date"] for e in d["events"] if e["date"] <= today]
    replies = sorted(e["date"] for e in d["events"] if e["type"] == "mail_in" and (not since or e["date"] >= since))
    mails = [e["date"] for e in d["events"] if e["type"] in ("mail_in", "mail_out") and e["date"] <= today]
    return {"last": max(past + ([since] if since else []), default=""), "next": upcoming[0] if upcoming else None,
            "dates": [{k: e[k] for k in ("date", "time", "type", "title")} for e in dates], "n": len(d["events"]),
            "first_reply": replies[0] if replies else "", "last_mail": max(mails, default="")}


def ghosted(phase, s, today=None):
    """Sent or answered, nothing happened for GHOST_DAYS and no date ahead: the application counts as without reply."""
    today = today or date.today()
    return (phase in ("sent", "replied") and not s["next"] and bool(s["last"])
            and (today - date.fromisoformat(s["last"])).days >= GHOST_DAYS)


def eml(raw, prof=None):
    """.eml text or raw bytes (e.g. from Thunderbird) -> fields of a mail event."""
    parser = email.message_from_bytes if isinstance(raw, bytes) else email.message_from_string
    msg = parser(raw, policy=email.policy.default)
    try:
        dt = parsedate_to_datetime(msg["date"]).astimezone()
        day, time = dt.date().isoformat(), dt.strftime("%H:%M")
    except (TypeError, ValueError):
        day, time = date.today().isoformat(), ""
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<(br|/p|/div)[^>]*>", "\n", text, flags=re.I))
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    sender, to = str(msg["from"] or ""), str(msg["to"] or "")
    outgoing = userprofile.own_mail(prof or {}, sender)  # own address or own name as sender
    head = f"From: {sender}\nTo: {to}" if sender or to else ""
    return {"date": day, "time": time, "type": "mail_out" if outgoing else "mail_in", "title": str(msg["subject"] or ""),
            "text": (head + "\n\n" + text).strip()}


PREPARATION_PROMPT = """Prepare {person} for a job interview. Below are the application (job details, requirements
mapping and letter), the log so far with mails and dates, the profile and the existing preparation.

Write in {language}, in Markdown, with exactly these four sections (headings translated into {language}):
{rules}
### About the company and the position
3 to 5 bullet points, only what the posting and the mails say, invent nothing.

### Likely questions
8 to 12 questions. For each a short answer sketch with concrete evidence from the profile (project, position,
number). Address gaps from the mapping honestly and show how they can be closed.

### Questions for the company
5 to 7 questions that show real interest in exactly this position.

### Logistics
Date, place or link, contact persons, documents to bring, as far as the log says. Note missing things as open questions.

Invent no facts about {person} that are not in the profile or the application.

=== APPLICATION ===
{letter}

=== LOG ===
{log}

=== EXISTING PREPARATION (keep what was noted by hand) ===
{old}

=== PROFILE ===
{profile}
"""
LANGUAGE_NAMES = {"de": "German", "en": "English"}


def prepare(provider, root, letter, lang="de"):
    """Returns the new text for the preparation section."""
    prof = userprofile.load(root)
    d = read(letter)
    log = "\n\n".join(f"{e['date']} {e['time']} {e['type']}: {e['title']}\n{e['text']}" for e in reversed(d["events"])) or "(nothing yet)"
    r = userprofile.rules(prof)
    out = ai.complete(provider, PREPARATION_PROMPT.format(person=userprofile.person(prof), rules=f"Writing rules:\n{r}\n" if r else "",
                                                          language=LANGUAGE_NAMES.get(d["lang"] or lang, "German"),
                                                          letter=ai.clip(letter.read_text("utf-8"), 20000, "letter"), log=ai.clip(log, 20000, "log"),
                                                          old=ai.clip(d["preparation"], 8000, "old preparation") or "(empty)", profile=ai.clip(userprofile.text(root, prof, "profile"), 15000, "profile")),
                      max_tokens=6000, timeout=600, cwd=root, disallowed=["Bash", "Write", "Edit"])
    text = re.sub(r"^```(?:markdown)?\n(.*)\n```$", r"\1", out.strip(), flags=re.S).strip()
    if "###" not in text:
        raise RuntimeError("Answer without the expected sections: " + text[:200])
    return userprofile.ss(text, prof)


FOLLOW_UP_PROMPT = """Write a short, polite follow-up e-mail from {person} about the application below. It was sent {days}
days ago{reply}. Ask about the state of the process and repeat the interest in one concrete sentence that fits the position.
Language: {language}. At most 120 words, no empty phrases, invent nothing.
{rules}
Answer exactly in this form:
SUBJECT: <subject line>
<mail text with salutation and closing>

=== APPLICATION ===
{letter}

=== LOG ===
{log}
"""
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def contact_mail(letter_text, d):
    """Address to write to: the contact field of the letter, otherwise the last mail that did not come from a no-reply address."""
    m = re.search(r"^\*\*(?:Kontakt|Contact):\*\*(.*)$", letter_text, re.M)
    found = EMAIL.findall(m.group(1)) if m else []
    if not found:
        for e in d["events"]:
            if e["type"] == "mail_in":
                found = [a for a in EMAIL.findall(e["text"].split("\n\n", 1)[0]) if not re.search(r"no.?reply", a, re.I)][:1]
                if found:
                    break
    return found[0] if found else ""


def follow_up(provider, root, letter, since, lang="de"):
    """Draft of a follow-up mail: {"to", "subject", "body"}. Language of the letter, not of the interface."""
    import finalizing
    prof = userprofile.load(root)
    d = read(letter)
    text = letter.read_text("utf-8")
    days = (date.today() - date.fromisoformat(since)).days if since else 0
    replies = [e for e in d["events"] if e["type"] == "mail_in"]
    log = "\n\n".join(f"{e['date']} {e['type']}: {e['title']}\n{e['text'][:1500]}" for e in reversed(d["events"])) or "(nothing yet)"
    r = userprofile.rules(prof)
    out = ai.complete(provider, FOLLOW_UP_PROMPT.format(person=userprofile.person(prof), days=days, rules=f"Writing rules:\n{r}\n" if r else "",
                                                        reply=", the last answer came on " + replies[0]["date"] if replies else ", there was no answer yet",
                                                        language=LANGUAGE_NAMES.get(finalizing.language(text), "German"),
                                                        letter=ai.clip(text, 15000, "letter"), log=ai.clip(log, 10000, "log")),
                      max_tokens=1500, timeout=300, cwd=root, disallowed=["Bash", "Write", "Edit"])
    return {"to": contact_mail(text, d), **mail_answer(out, prof)}


def mail_answer(out, prof):
    """SUBJECT: line plus text -> {"subject", "body"}."""
    out = re.sub(r"^```\w*\n(.*)\n```$", r"\1", out.strip(), flags=re.S).strip()
    m = re.match(r"(?:SUBJECT|BETREFF):[ \t]*([^\n]+)\n+(.*)", out, re.S | re.I)
    subject, body = (m.group(1).strip(), m.group(2).strip()) if m else ("", out)
    return {"subject": userprofile.ss(subject, prof), "body": userprofile.ss(body, prof)}


APPLICATION_MAIL_PROMPT = """Write the short e-mail from {person} that goes with the application below. The cover letter and the CV are
attached as PDF ({attachments}). Name the position, give one concrete sentence why it fits, and say that the documents are attached.
Language: {language}. At most 100 words, no empty phrases, do not repeat the cover letter, invent nothing.
{rules}
Answer exactly in this form:
SUBJECT: <subject line>
<mail text with salutation and closing>

=== APPLICATION ===
{letter}
"""


def application_mail(provider, root, letter, attachments, lang="de"):
    """Draft of the e-mail to send the application with: {"to", "subject", "body", "attachments"}. Language of the letter."""
    import finalizing
    prof = userprofile.load(root)
    text = letter.read_text("utf-8")
    r = userprofile.rules(prof)
    out = ai.complete(provider, APPLICATION_MAIL_PROMPT.format(person=userprofile.person(prof), attachments=", ".join(attachments) or "PDF",
                                                               rules=f"Writing rules:\n{r}\n" if r else "", letter=ai.clip(text, 15000, "letter"),
                                                               language=LANGUAGE_NAMES.get(finalizing.language(text), "German")),
                      max_tokens=1500, timeout=300, cwd=root, disallowed=["Bash", "Write", "Edit"])
    return {"to": contact_mail(text, read(letter)), "attachments": attachments, **mail_answer(out, prof)}


# ---------- Calendar (.ics), for download and for subscribing ----------
ICS_LABEL = {"de": {"appointment": "Bewerbung", "deadline": "Frist"}, "en": {"appointment": "Application", "deadline": "Deadline"}}


def ics(entries, lang="de"):
    """entries: [(company, event)] with event type Termin or Frist. Times are local without time zone, 1 hour long."""
    from datetime import datetime, timedelta, timezone
    z = lambda x: re.sub(r"([\\;,])", r"\\\1", str(x)).replace("\n", "\\n")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Application Board//EN", "CALSCALE:GREGORIAN", "X-WR-CALNAME:Bewerbungen"]
    for company, e in entries:
        d = e["date"].replace("-", "")
        uid = re.sub(r"[^A-Za-z0-9]", "", f"{d}{e.get('time', '')}{company}{e.get('title', '')}")[:60]
        out += ["BEGIN:VEVENT", f"UID:{uid}@application-board", f"DTSTAMP:{stamp}"]
        if e.get("time"):
            start = datetime.strptime(e["date"] + e["time"], "%Y-%m-%d%H:%M")
            out += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%S}"]
        else:
            nxt = date.fromisoformat(e["date"]) + timedelta(days=1)
            out += [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nxt:%Y%m%d}"]
        label = ICS_LABEL.get(lang, ICS_LABEL["de"]).get(e["type"], e["type"])
        out += ["SUMMARY:" + z(f"{label}: {company}" + (f", {e['title']}" if e.get("title") else "")), "END:VEVENT"]
    return "\r\n".join(out + ["END:VCALENDAR"]) + "\r\n"


# ---------- Mails that probably belong to an application ----------
# Matched without AI: the sender or recipient domain of known contacts, or the company name in subject, addresses or text.
GENERIC_DOMAINS = re.compile(r"(?:^|\.)(?:gmail|googlemail|outlook|hotmail|live|yahoo|gmx|web|bluewin|icloud|me|proton|protonmail|"
                             r"jobs|jobup|linkedin|indeed|xing|stepstone|join|personio|smartrecruiters|greenhouse|lever|workday|myworkday)\.[a-z.]+$")
LEGAL = re.compile(r"&\s*cie\.?|\((.*?)\)|\b(?:ag|gmbh|sa|sagl|ltd|inc|llc|kg|schweiz|switzerland)\b\.?")
# folded text (lower case, no accents) -> phase; the first that matches wins, None means: no phase change
PHASE_HINTS = (("rejected", r"leider|absage|nicht (?:weiter )?berucksichtig|anderen? (?:kandidat|bewerber)|unfortunately|regret|"
                          r"not (?:to )?(?:move|proceed|continue)|other candidates"),
               ("offer", r"arbeitsvertrag|vertragsangebot|job offer|offer letter|employment contract"),
               ("interview", r"einladung|vorstellungsgesprach|interview|kennenlernen|invitation|invite you"),
               (None, r"eingangsbestatigung|eingegangen|erhalten|verify your e-?mail|confirm your e-?mail|e-?mail-?adresse bestatigen|bestatigen sie ihre|passwor[dt]|account|konto|received your application|thank you for (?:your )?appl"))


def fold(s):
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()


def suggest_phase(text):
    """Phase a mail text suggests: rejected, offer, interview, replied or "" (confirmation of receipt, nothing new)."""
    t = fold(text)
    for phase, pattern in PHASE_HINTS:
        if re.search(pattern, t):
            return phase or ""
    return "replied"


def mail_keys(company, letter_text, d):
    """Company name without legal form (folded, at least 3 letters) and the mail domains of known contacts."""
    name = " ".join(LEGAL.sub(" ", fold(company)).split())
    m = re.search(r"^\*\*(?:Kontakt|Contact):\*\*(.*)$", letter_text, re.M)
    addrs = EMAIL.findall(m.group(1)) if m else []
    for e in d["events"]:
        if e["type"] in ("mail_in", "mail_out"):
            addrs += EMAIL.findall(e["text"].split("\n\n", 1)[0])
    domains = {a.split("@")[1].lower() for a in addrs} - {""}
    return (name if len(name) >= 3 else ""), {x for x in domains if not GENERIC_DOMAINS.search(x)}


def new_mails(mails, company, letter_text, d, since="", prof=None, limit=10):
    """Mails from the Thunderbird index (newest first) since sending that probably belong to this application and are
    not in the log yet, each with the phase it suggests ("" for own mails and confirmations)."""
    prof = prof or {}
    name, domains = mail_keys(company, letter_text, d)
    domains -= {prof.get("email", "").lower().partition("@")[2]}
    if not name and not domains:
        return []
    word = re.compile(rf"\b{re.escape(name)}\b") if name else None
    have = {(e["date"], e["time"], e["title"]) for e in d["events"]}
    out = []
    for m in mails:
        if since and m["date"] < since:
            break
        if (m["date"], m["time"], m["subject"]) in have:
            continue
        ds = {a.split("@")[1].lower() for a in EMAIL.findall(m["from"] + " " + m.get("to", ""))}
        if ds & domains or (word and word.search(m["search"])):
            out.append({**{k: m[k] for k in ("token", "date", "time", "folder", "from", "subject")},
                        "phase": "" if userprofile.own_mail(prof, m["from"]) else suggest_phase(m["search"])})
            if len(out) >= limit:
                break
    return out


if __name__ == "__main__":  # self-test: round trip Markdown <-> data, both languages, .eml
    d = {"events": [{"date": "2026-11-10", "time": "", "type": "status", "title": "Abgeschickt", "text": ""},
                    {"date": "2026-11-12", "time": "14:30", "type": "mail_in", "title": "Einladung · Gespräch",
                     "text": "Hallo\n# no heading\n## Notizen\nLine"}],
         "preparation": "### Questions\n- Why us?", "notes": "free"}
    for lang in ("de", "en"):
        md = render("Company, Title", d, lang)
        r = parse(md)
        assert r["lang"] == lang and [e["date"] for e in r["events"]] == ["2026-11-12", "2026-11-10"], r
        assert r["events"][0]["text"] == d["events"][1]["text"] and r["events"][0]["title"] == "Einladung - Gespräch", r["events"][0]
        assert r["events"][0]["type"] == "mail_in" and r["preparation"] == "### Questions\n- Why us?" and r["notes"] == "free", r
        assert parse(render("x", r, lang)) == r
    assert "### 2026-11-12 14:30 · Mail in · " in render("x", d, "en") and "## Events" in render("x", d, "en")
    assert eml("From: Max Muster <max@x.ch>\nSubject: A\n\nx", {"email": "max@x.ch"})["type"] == "mail_out"
    m = eml("From: Anna <anna@firma.ch>\nTo: max@x.ch\nSubject: Invitation\nDate: Thu, 12 Nov 2026 14:30:00 +0100\n"
            "Content-Type: text/plain; charset=utf-8\n\nHello\n")
    assert m["type"] == "mail_in" and m["date"] == "2026-11-12" and m["title"] == "Invitation" and "Hello" in m["text"], m
    s = summary({"events": [{"date": "2099-01-01", "time": "09:00", "type": "appointment", "title": "G", "text": ""}]}, "2026-11-01")
    assert s["next"]["date"] == "2099-01-01" and s["last"] == "2026-11-01", s
    assert contact_mail("**Kontakt:** Anna Muster, anna@firma.ch", {"events": []}) == "anna@firma.ch"
    assert contact_mail("**Kontakt:** keiner", {"events": [{"type": "mail_in", "text": "From: x <noreply@jobs.ch>\n\nHi"},
                                                         {"type": "mail_in", "text": "From: HR <hr@firma.ch>\n\nHi"}]}) == "hr@firma.ch"
    assert suggest_phase("Leider müssen wir Ihnen mitteilen") == "rejected" and suggest_phase("Einladung zum Vorstellungsgespräch") == "interview"
    assert suggest_phase("Wir haben Ihre Bewerbung erhalten") == "" and suggest_phase("Rückfrage zu Ihrem Lebenslauf") == "replied"
    assert mail_keys("Nordwind Systems AG", "**Kontakt:** hr@nordwind.example, x@gmail.com", {"events": []}) == ("nordwind systems", {"nordwind.example"})
    mail = lambda day, frm, subj, text, to="me@x.ch": {"token": day + subj, "date": day, "time": "10:00", "folder": "INBOX", "from": frm,
                                                       "to": to, "subject": subj, "search": fold(f"{subj} {frm} {to} {text}")}
    box = [mail("2026-11-14", "Anna <anna@nordwind.example>", "Termin", "Einladung zum Interview"),
           mail("2026-11-13", "jobs.ch <noreply@jobs.ch>", "Bewerbung", "Ihre Bewerbung bei Nordwind Systems ist eingegangen"),
           mail("2026-11-12", "Shop <news@shop.ch>", "Sale", "nothing about nordwindsurfing"),
           mail("2026-11-11", "Me <me@x.ch>", "Bewerbung", "Anbei", to="hr@nordwind.example"),
           mail("2026-10-01", "Anna <anna@nordwind.example>", "Alt", "vor dem Senden")]
    log = {"events": [{"date": "2026-11-11", "time": "10:00", "title": "Bewerbung", "type": "mail_out", "text": ""}]}
    hits = new_mails(box, "Nordwind Systems AG", "**Kontakt:** hr@nordwind.example", log, "2026-11-10", {"email": "me@x.ch"})
    assert [(h["subject"], h["phase"]) for h in hits] == [("Termin", "interview"), ("Bewerbung", "")], hits
    cal = ics([("A, B AG", {"date": "2026-11-20", "time": "10:00", "type": "appointment", "title": "Gespräch"}), ("C", {"date": "2026-11-21", "time": "", "type": "deadline", "title": ""})])
    assert "DTSTART:20261120T100000\r\nDTEND:20261120T110000" in cal and "SUMMARY:Bewerbung: A\\, B AG\\, Gespräch" in cal and "DTEND;VALUE=DATE:20261122" in cal, cal
    assert mail_answer("SUBJECT: Bewerbung\n\nGuten Tag", {}) == {"subject": "Bewerbung", "body": "Guten Tag"}
    from datetime import date as _d
    s0 = {"next": None, "last": "2026-01-01"}
    assert ghosted("sent", s0, _d(2026, 2, 1)) and not ghosted("sent", s0, _d(2026, 1, 20))
    assert not ghosted("interview", s0, _d(2026, 3, 1)) and not ghosted("sent", {**s0, "next": {"date": "2026-03-01"}}, _d(2026, 3, 1))
    print("ok")
