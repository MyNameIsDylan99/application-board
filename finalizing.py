"""Finalizing a letter: fill the Word template, create the PDFs, check the result.

The actual work is done by finalize.py in this folder (text replacement in an earlier cover letter/CV docx,
export through LibreOffice). That needs no AI, so finalizing works with any provider. Fixed checks then run on
the PDF text; optionally the model chosen for the "pruefen" task (any provider) proofreads the text and reports
issues as hints.

Hints are stored in review.json and shown in the UI, so they are written in the UI language (de/en).
"""
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import ai
import finalize
import storage
import userprofile

MONTHS_DE = "Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
MONTHS_EN = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b\d{{1,2}}\.?\s+(?:{MONTHS_DE}|{MONTHS_EN})\s+\d{{4}}\b|\b\d{{1,2}}\.\d{{1,2}}\.\d{{2,4}}\b")
LEGAL_FORM = re.compile(r"&\s*Cie\.?|\b(?:AG|GmbH|SA|Sagl|Ltd|Inc|LLC|KG)\b\.?")
SCRIPT = Path(__file__).resolve().parent / "finalize.py"
# Date line in the letter: "<town>, 12. November 2026" or "<town>, 12 November 2026", any town
DATE_LINE = rf"^([^\n,#*]{{1,60}},[ \t]*)(?:{DATE.pattern})[ \t]*$"

CHECK_PROMPT = """You are the final check of a cover letter that was just exported as PDF.
Compare the PDF text with the Markdown original and check:
- Is text missing or duplicated, is there an old date or a foreign company name left over from a template?
- Do company, salutation and job title match?
{rules}- Obvious typos or grammar mistakes.
Do not report: the sender block of the template (name, address, phone) and the missing ** around the subject.
Report real problems only, no style suggestions. Write each problem as a short sentence in {language}.
Answer only with JSON:
{{"probleme": ["short sentence per problem"]}}  (empty list if everything is fine)

Markdown original (letter part):
<<<
{letter}
>>>

PDF text of the cover letter:
<<<
{pdf}
>>>"""

# Messages shown in the UI; "Info:" hints are informational, all others are warnings.
TEXT = {
    "de": {"pages": "Anschreiben hat {n} Seiten statt einer.",
           "dates": "Im Anschreiben stehen {n} Datumsangaben ({found}), erwartet genau eine.", "none": "keine",
           "date_missing": "Datum aus dem Brief ({line}) fehlt im PDF.", "not_found": "Im PDF nicht gefunden: {line}",
           "paragraph": "Absatz fehlt oder ist abgeschnitten: {start} ...", "sz": 'Brief enthält "ß".',
           "cv_empty": "Lebenslauf-PDF ist leer.", "no_cv": "Info: Keine Lebenslauf-Vorlage, nur das Anschreiben erzeugt.",
           "subject": "Info: Betreff ergänzt ({subject}).", "date": "Info: Datum im Brief auf heute gesetzt ({line}).",
           "ai": "KI: {p}", "ai_json": "KI-Prüfung lieferte kein JSON: {out}", "ai_failed": "KI-Prüfung nicht möglich: {e}",
           "no_cl": "Keine Anschreiben-Vorlage: im Tab Profil unter Vorlagen eine erzeugen oder hochladen",
           "missing": "Nicht erzeugt: {files}",
           "no_poppler": "Info: Poppler (pdftotext) fehlt, das PDF wurde nicht geprüft."},
    "en": {"pages": "Cover letter has {n} pages instead of one.",
           "dates": "The cover letter contains {n} dates ({found}), expected exactly one.", "none": "none",
           "date_missing": "Date from the letter ({line}) is missing in the PDF.", "not_found": "Not found in the PDF: {line}",
           "paragraph": "Paragraph missing or cut off: {start} ...", "sz": 'Letter contains "ß".',
           "cv_empty": "CV PDF is empty.", "no_cv": "Info: No CV template, only the cover letter was created.",
           "subject": "Info: Subject line added ({subject}).", "date": "Info: Date in the letter set to today ({line}).",
           "ai": "AI: {p}", "ai_json": "AI check returned no JSON: {out}", "ai_failed": "AI check not possible: {e}",
           "no_cl": "No cover letter template: create or upload one in the Profile tab under Templates",
           "missing": "Not created: {files}",
           "no_poppler": "Info: Poppler (pdftotext) is missing, the PDF was not checked."},
}
LANGUAGE_NAMES = {"de": "German", "en": "English"}


def _t(ui, key, **kw):
    return TEXT.get(ui, TEXT["en"])[key].format(**kw)


def language(md_text):
    """Language of the letter: "en" when it starts with "Dear" or closes with "Best regards"."""
    letter = md_text[finalize.letter_start(md_text):]
    return "en" if re.search(r"^(Dear |Best regards)", letter, re.M) else "de"


# ---------- Fact check after an AI round ----------
# Numbers and names that are easy to invent: digits (years, percentages, counts) and product or tech names written with
# a second capital, a digit, + or # (TypeScript, Unity3D, C#, C++) or as an acronym (AWS, SQL). German nouns start with a
# capital too, so plain capitalized words are not checked.
# ponytail: a token check, not understanding; misses invented facts in plain words ("three years").
FACT = re.compile(r"\d+(?:[.,'’]\d+)*\s?%?|\b[A-Z][A-Za-z]*[A-Z0-9+#][\w+#.]*|\b[A-Z]{2,}\b")
NAME_EN = re.compile(r"(?<=[a-z,;:] )[A-Z][a-z]{2,}")  # English: a capitalized word inside a sentence is a name
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b(?:" + MONTHS_EN + r")\s+\d{1,2},\s*\d{4}\b")


def _facts(text, names=False):
    out, text = {}, ISO_DATE.sub(" ", DATE.sub(" ", text))
    body = "\n".join(x for x in text.splitlines() if x.rstrip().endswith((".", "!", "?")))  # sentences, not heading, address, salutation
    for m in [*FACT.finditer(text), *(NAME_EN.finditer(body) if names else [])]:
        s = m.group(0).strip().rstrip(".,")
        key = re.sub(r"[.,'’%\s]", "", s) if s[0].isdigit() else s.lower()
        if key:
            out.setdefault(key, s)
    return out


def unverified(new, sources, before=""):
    """Facts in the letter part of `new` that appear neither in `sources` (profile, posting, feedback) nor anywhere in
    `before` (the file before the round, with job info and mapping). Dates are left out. Returns them as written."""
    known, src = {**_facts(sources), **_facts(before)}, (sources + "\n" + before).lower()
    start = finalize.letter_start(new)
    letter = new[max(start, 0):]
    return [s for k, s in _facts(letter, language(new) == "en").items() if k not in known and (k[0].isdigit() or k not in src)][:15]


def new_sentences(new, before):
    """Sentences of the letter part of `new` that are not in `before`."""
    split = lambda t: [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", t[max(finalize.letter_start(t), 0):]) if len(x.strip()) > 3]
    old = set(split(before)) if before else set()
    return [x for x in split(new) if x not in old]


FACTS_PROMPT = """Below are sentences that were just written into a cover letter, and the sources: the profile and the posting.
List every claim about the applicant in these sentences that the sources do not back up: invented experience, numbers,
tools, employers, degrees, projects. Ignore style, wishes, and statements about the company.
Answer only with JSON: {{"claims": ["one short sentence per unbacked claim, in {language}"]}} (empty list if everything is backed).

=== NEW SENTENCES ===
{sentences}

=== SOURCES ===
{sources}"""


def ai_facts(provider, new, before, sources, root, ui="de"):
    """Optional second look by an AI: claims in the new sentences that the sources do not back. [] when nothing is new."""
    sentences = new_sentences(new, before)
    if not sentences:
        return []
    out = ai.complete(provider, FACTS_PROMPT.format(language=LANGUAGE_NAMES.get(ui, "German"), sentences="\n".join(f"- {x}" for x in sentences[:60]),
                                                    sources=ai.clip(sources, 40000, "sources")),
                      max_tokens=1500, timeout=300, cwd=root, disallowed=["Bash", "Write", "Edit"])
    try:
        return [str(x).strip() for x in ai.extract_json(out).get("claims", []) if str(x).strip()][:10]
    except (ValueError, AttributeError):
        return []


def fact_warning(found, ui="de"):
    if not found:
        return ""
    return ("Please check, not found in profile, posting or feedback: " if ui == "en" else
            "Bitte prüfen, nicht in Profil, Anzeige oder Feedback gefunden: ") + ", ".join(found)


def short_name(md):
    """Short company name for the file name ({firma} placeholder). With several letters in the folder the
    file name tells them apart; otherwise the company folder without its legal form is enough."""
    several = len(list(md.parent.glob("Bewerbungsschreiben*.md"))) > 1
    base = md.stem.removeprefix("Bewerbungsschreiben").strip("_") if several else ""
    s = base or LEGAL_FORM.sub("", md.parent.name)
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "Company"


def template(root, prof, kind, lang, exclude_dir):
    """The template chosen in the profile, otherwise the newest earlier file matching the file name pattern
    in the letter's language (English is recognised by "Dear" in a cover letter and "EXPERIENCE" in a CV).
    None when there is none."""
    chosen = (prof.get("vorlagen") or {}).get(kind)
    if chosen and (root / chosen).is_file():
        return root / chosen
    prefix = userprofile.prefix(prof, kind)
    candidates = [p for p in (root / "Bewerbungen").glob(f"*/{prefix}*.docx") if p.parent != exclude_dir]
    if not candidates:
        return None

    def english(p):
        try:
            xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8", "ignore")
        except (zipfile.BadZipFile, KeyError):
            return None
        return ("Dear" in xml) if kind == "anschreiben" else ("EXPERIENCE" in xml)

    matching = [p for p in candidates if english(p) == (lang == "en")]
    return max(matching or candidates, key=lambda p: p.stat().st_mtime)


def _pdf_text(pdf):
    return subprocess.run([storage.tool("pdftotext"), "-layout", str(pdf), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def _pages(pdf):
    if not storage.tool("pdfinfo"):  # count the page breaks of pdftotext instead
        return _pdf_text(pdf).rstrip("\f").count("\f") + 1
    out = subprocess.run([storage.tool("pdfinfo"), str(pdf)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def _norm(s):
    return re.sub(r"\s+", " ", s.replace("­", "")).strip()


def checks(cl_pdf, cv_pdf, letter, md_text, prof=None, ui="de"):
    """Fixed checks that need no AI. Returns the hints as a list of sentences and the PDF text."""
    hints = []
    if not storage.tool("pdftotext"):
        return [_t(ui, "no_poppler")], ""
    n = _pages(cl_pdf)
    if n != 1:
        hints.append(_t(ui, "pages", n=n))
    text = _pdf_text(cl_pdf)
    flat = _norm(text)
    dates = DATE.findall(text)
    if len(dates) != 1:
        hints.append(_t(ui, "dates", n=len(dates), found=", ".join(dates) or _t(ui, "none")))
    if letter["date_line"].split(",")[-1].strip() not in flat:
        hints.append(_t(ui, "date_missing", line=letter["date_line"]))
    for line in letter["recipient_lines"][:1] + [letter["subject"], letter["salutation"]]:
        if line and _norm(line) not in flat:
            hints.append(_t(ui, "not_found", line=line))
    for paragraph in letter["body_paragraphs"]:
        start = " ".join(paragraph.split()[:6])
        if _norm(start) not in flat:
            hints.append(_t(ui, "paragraph", start=start))
    if (prof or {}).get("ss_statt_sz") and "ß" in md_text[finalize.letter_start(md_text):]:
        hints.append(_t(ui, "sz"))
    if cv_pdf and _pages(cv_pdf) == 0:
        hints.append(_t(ui, "cv_empty"))
    return hints, text


def ai_check(provider, md_text, pdf_text, root, prof=None, ui="de"):
    letter = md_text[finalize.letter_start(md_text):]
    r = userprofile.rules(prof or {})
    prompt = CHECK_PROMPT.format(letter=letter, pdf=pdf_text, rules=f"- Violations of these writing rules:\n{r}\n" if r else "",
                                 language=LANGUAGE_NAMES.get(ui, "English"))
    out = ai.complete(provider, prompt, max_tokens=1500, timeout=300, cwd=root, disallowed=["Bash", "Write", "Edit"])
    try:
        problems = ai.extract_json(out).get("probleme", [])
    except ValueError:
        return [_t(ui, "ai_json", out=out[:200])]
    return [_t(ui, "ai", p=p) for p in problems if isinstance(p, str) and p.strip()]


def set_date_today(md_text, lang):
    """Sets the date line of the letter to today ("<town>, 11. November 2026"), because the letter goes out
    on the day it is finalized. Returns (text, changed)."""
    today = date.today()
    if lang == "en":
        new = f"{today.day} {MONTHS_EN.split('|')[today.month - 1]} {today.year}"
    else:
        new = f"{today.day}. {MONTHS_DE.replace('|Maerz', '').split('|')[today.month - 1]} {today.year}"
    i = finalize.letter_start(md_text)
    head, letter = md_text[:i], md_text[i:]
    letter2, n = re.subn(DATE_LINE, lambda m: m.group(1) + new, letter, count=1, flags=re.M)
    return head + letter2, n == 1 and letter2 != letter


def add_subject(md_text, lang):
    """If the bold subject line after the date is missing, inserts "Bewerbung als <title>" (or "Application for
    the position of <title>"), with the title from "**Titel:**" in the header. Returns (text, changed)."""
    i = finalize.letter_start(md_text)
    head, letter = md_text[:i], md_text[i:]
    title = re.search(r"^\*\*(?:Titel|Title):\*\*\s*(.+)$", head, re.M)
    m = re.search(DATE_LINE[:-1] + r"\n\s*\n(\*\*)?", letter, re.M)
    if not title or not m or m.group(2):
        return md_text, False
    t = re.sub(r"\s*[–—-]\s*(?=\d)", " to " if lang == "en" else " bis ", title.group(1).strip())  # no dashes in the letter
    line = f"**Application for the position of {t}**" if lang == "en" else f"**Bewerbung als {t}**"
    return head + letter[:m.end()] + line + "\n\n" + letter[m.end():], True


def finalize_letter(root, md, provider=None, ui="de"):
    """Creates cover letter and CV as docx and pdf next to the letter. Raises on hard errors, otherwise returns
    {"dateien": {...}, "pruefung": [hints], "vorlagen": {...}} (keys as stored in review.json)."""
    prof = userprofile.load(root)
    finalize.set_profile(prof)  # first: raises a clear error when name or e-mail are missing, before anything is written
    md_text = md.read_text("utf-8")
    md_text, date_changed = set_date_today(md_text, language(md_text))
    md_text, subject_added = add_subject(md_text, language(md_text))
    if date_changed or subject_added:
        md.write_text(md_text, "utf-8")
    letter = finalize.parse_letter_markdown(md_text)  # raises early when the letter part does not fit
    lang = language(md_text)
    short, mmyy = short_name(md), date.today().strftime("%m%y")
    cl_template = template(root, prof, "anschreiben", lang, md.parent)
    cv_template = template(root, prof, "lebenslauf", lang, md.parent)
    if not cl_template:
        raise RuntimeError(_t(ui, "no_cl"))
    cmd = [sys.executable, str(SCRIPT), md.parent.name, "--md", md.name, "--company-short", short, "--mmyy", mmyy,
           "--repo-root", str(root), "--cl-template", str(cl_template)] + (["--cv-template", str(cv_template)] if cv_template else ["--skip-cv"])
    # without --cv-title: the title in the CV header stays as in the template, never adapted to the job
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-1500:] or f"finalize.py exit {r.returncode}")
    files = {f"{k}_{ext}": md.parent / f"{userprofile.file_name(prof, kind, short, mmyy)}.{ext}"
             for k, kind in (("cl", "anschreiben"), ("cv", "lebenslauf")) for ext in ("docx", "pdf") if k == "cl" or cv_template}
    missing = [p.name for p in files.values() if not p.is_file()]
    if missing:
        raise RuntimeError(_t(ui, "missing", files=", ".join(missing)))
    hints, pdf_text = checks(files["cl_pdf"], files.get("cv_pdf"), letter, md_text, prof, ui)
    if not cv_template:
        hints.insert(0, _t(ui, "no_cv"))
    if subject_added:
        hints.insert(0, _t(ui, "subject", subject=letter["subject"]))
    if date_changed:
        hints.insert(0, _t(ui, "date", line=letter["date_line"]))
    if provider:
        try:
            hints += ai_check(provider, md_text, pdf_text, root, prof, ui)
        except Exception as e:  # the AI check is an extra; its failure must not break finalizing
            hints.append(_t(ui, "ai_failed", e=str(e)[:200]))
    return {"dateien": {k: p.relative_to(root).as_posix() for k, p in files.items()}, "pruefung": hints,
            "vorlagen": {"sprache": lang, "kurz": short}}


if __name__ == "__main__":  # self-test without files
    assert LEGAL_FORM.sub("", "Hch. Kündig & Cie AG").strip() == "Hch. Kündig"
    assert len(DATE.findall("Bern, 9. November 2026 und 27.10.2026")) == 2
    assert language("x\n# Bewerbungsschreiben\n\nDear Team\n") == "en"
    t, ok = set_date_today("**Datum:** 1. Mai 2026\n# Bewerbungsschreiben\n\nBern, 9. November 2026\n\nSehr geehrte", "de")
    assert ok and "1. Mai 2026" in t and f"Bern, {date.today().day}." in t, t
    t, ok = set_date_today("# Cover Letter\n\nBern, 9 November 2026\n\nDear", "en")
    assert ok and str(date.today().year) in t
    t, ok = set_date_today("# Bewerbungsschreiben\n\nBern, 9. November 2026\n\n**Bewerbung als X**\n", "de")
    assert "2026\n\n**Bewerbung" in t, repr(t)  # the blank line before the subject stays
    assert language("x\n# Bewerbungsschreiben\n\nSehr geehrte Damen\n") == "de"
    t, ok = add_subject("**Titel:** Dev (80–100%)\n# Bewerbungsschreiben\n\nBern, 9. November 2026\n\nSehr geehrte", "de")
    assert ok and "2026\n\n**Bewerbung als Dev (80 bis 100%)**\n\nSehr geehrte" in t, repr(t)
    assert not add_subject(t, "de")[1]  # an existing subject stays
    t, ok = add_subject("**Title:** Dev (80–100%)\n# Cover Letter\n\nBern, 9 November 2026\n\nDear", "en")
    assert ok and "**Application for the position of Dev (80 to 100%)**" in t, repr(t)
    assert _t("de", "no_cv").startswith("Info:") and _t("en", "ai", p="x") == "AI: x"
    before = "**Titel:** Dev\n- Kubernetes erwünscht\n# Bewerbungsschreiben\n\nBern, 9. November 2026\n\nIch kenne Unity.\n"
    new = before.replace("Ich kenne Unity.", "Seit 2019 nutze ich C# und TypeScript, 40 % schneller, mit AWS und Kubernetes. Am 12.11.2026 ab 2024-01-01.")
    assert unverified(new, "Profil: C# seit 2019", before) == ["TypeScript", "40 %", "AWS"], unverified(new, "Profil: C# seit 2019", before)
    assert unverified(before, "", before) == [] and fact_warning([]) == "" and "AWS" in fact_warning(["AWS"], "en")
    en = "# Cover Letter\n\nDear Ms Example\n\nI worked at Google and at Unity.\n\nBest regards"
    assert unverified(en, "profile: Unity") == ["Google"], unverified(en, "profile: Unity")
    assert new_sentences(en, en.replace(" and at Unity", "")) == ["I worked at Google and at Unity."]
    assert ai_facts({"type": "cli", "cmd": "echo '{\"claims\": [\"Google is not in the profile\"]}'"}, en, "", "x", None) == ["Google is not in the profile"]
    print("ok")
