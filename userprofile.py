"""Personal profile from the data folder: <data folder>/application-board.json (bewerbungs-board.json in folders of
the German layout, see layout.py; there the German keys are kept on disk).

Everything that describes the person (name, town, e-mail, file names, writing rules, where the profile,
style and search profile live) is stored there, not in the code. Missing file or fields fall back to the
neutral values in DEFAULT. Paths under "files" are relative to the data folder.
"""
import json
import re
import unicodedata
from pathlib import Path

import layout

DEFAULT = {
    "name": "",                 # "First Last", appears under the letter and in the sender block of the Word template
    "town": "",                 # town of the date line ("<town>, 12 November 2026") and after the title in the CV
    "email": "",                # anchor in the Word template and used to recognise one's own mails
    "phone": "",
    "address": "",              # may span several lines, used in templates
    "photo": "",                # application photo, relative to the data folder
    "signature": "",            # signature image, relative to the data folder
    "document_folders": [],     # linked folders [{"path": "Certificates", "category": "certificates"}], never copied
    "templates": {"cover_letter": "", "cv": ""},  # chosen Word templates; empty: newest earlier file matching the file name pattern
    "file_names": {"cover_letter": "CL_{name}_{company}_{mmyy}", "cv": "CV_{name}_{company}_{mmyy}"},
    "rules": [],                # writing rules added to every AI prompt, one sentence per rule
    "swiss_ss": False,          # Swiss spelling: replace "ß" with "ss" in AI texts
    # rule check in the editor: word range of the letter text (0 = no check) and characters that must not appear
    "rule_check": {"words_min": 0, "words_max": 0, "chars": []},
    "files": {},                # profile, style, search_profile, grading, cv: relative to the data folder; defaults from layout.py
}
TEXT_FIELDS = ("name", "town", "email", "phone", "address", "photo", "signature")
KINDS = ("cover_letter", "cv")


def _relative_ok(v):
    return isinstance(v, str) and not v.startswith(("/", "\\")) and ".." not in Path(v).parts and ":" not in v


def save(root, new):
    """Takes the known fields from new, validates them and writes the profile file.
    Fields the app does not know are kept in the file."""
    lay = layout.get(root)
    old = lay.read_json("profile_file", {})
    for k in TEXT_FIELDS:
        if k in new:
            v = str(new[k] or "").strip()
            if k in ("photo", "signature") and v and not _relative_ok(v):
                raise ValueError(f"{k}: give a path relative to the data folder")
            old[k] = v
    if "rules" in new:
        old["rules"] = [str(x).strip() for x in new["rules"] if str(x).strip()]
    if "swiss_ss" in new:
        old["swiss_ss"] = bool(new["swiss_ss"])
    if "rule_check" in new:
        r = new["rule_check"] or {}
        try:
            lo, hi = int(r.get("words_min") or 0), int(r.get("words_max") or 0)
        except (TypeError, ValueError):
            raise ValueError("Rule check: word counts must be numbers")
        if lo < 0 or hi < 0 or (hi and lo > hi):
            raise ValueError("Rule check: the minimum must not be above the maximum")
        old["rule_check"] = {"words_min": lo, "words_max": hi, "chars": [str(z) for z in r.get("chars", []) if str(z)][:20]}
    if "file_names" in new:
        d = {k: str(new["file_names"].get(k, "")).strip() for k in KINDS}
        for k, v in d.items():
            if "{company}" not in v or re.search(r"[\\/:*?\"<>|]", v):
                raise ValueError(f"File name {k}: must contain {{company}} and no special characters such as / : *")
            try:
                v.format(name="x", company="x", mmyy="x")
            except (KeyError, IndexError, ValueError):
                raise ValueError(f"File name {k}: only the placeholders {{name}}, {{company}}, {{mmyy}} are allowed")
        old["file_names"] = d
    if "files" in new:
        d = {k: str(v).strip() for k, v in new["files"].items() if k in layout.PROFILE_FILES}
        if not all(_relative_ok(v) and v for v in d.values()):
            raise ValueError("Give file paths relative to the data folder")
        old["files"] = {**old.get("files", {}), **d}
    if "templates" in new:
        v = {k: str(new["templates"].get(k, "") or "").strip() for k in KINDS}
        if not all(not x or (_relative_ok(x) and x.endswith(".docx")) for x in v.values()):
            raise ValueError("Template: give a docx path relative to the data folder")
        old["templates"] = v
    if "document_folders" in new:
        o = [{"path": str(x.get("path", "")).strip(), "category": str(x.get("category", "")).strip()} for x in new["document_folders"]]
        if not all(_relative_ok(x["path"]) and x["path"] for x in o):
            raise ValueError("Give document folders relative to the data folder")
        old["document_folders"] = o
    lay.write_json("profile_file", old)
    return load(root)


SUGGESTION = {
    "profile": """Create or extend the job application profile of {person}. The profile is the only source of facts that
cover letters and CVs are later written from, so make it complete, concrete and backed by the sources.

Rules:
- Only facts from the documents, the details and the existing profile. Invent nothing, embellish nothing.
- Keep the text and structure of the existing profile; only add what is new and correct what the documents clearly contradict.
- Without an existing profile, use these sections: education, work experience, projects, skills, languages, other.
- Keep numbers, grades, periods, technologies and results; they make later letters credible.
- Mark contradictions or unclear points as a line "> Check: ..." instead of guessing.
- Write in the language of the existing profile; if there is none, in {language}.
{rules}
Output the complete new profile (Markdown) between the markers, then a summary:
===FILE===
(complete profile file)
===END===
===SUMMARY===
(two to four sentences in {language}: what is new or changed, what needs checking)

=== DETAILS ===
{details}

=== EXISTING PROFILE ===
{old}

=== DOCUMENTS ===
{sources}
""",
    "cv": """Create the content of a CV for {person} from the profile, the details and earlier CVs.
A Word CV is generated from it automatically, so use exactly this Markdown format:

# Title under the name (job title, short; take it from the existing CV if there is one)
Short profile in two or three sentences (optional).

## Section (e.g. work experience, education, projects, skills, languages)
### Period | Title or role | Company or school, town
- Point with a result, number or technology
## Skills
- **Label:** values

Rules:
- Only facts from the sources, invent nothing. Newest entries first.
- One to four points per entry, short and concrete; choose points and sections so it fits on two pages.
- Language as in the profile. Keep the existing CV (below) where the sources bring nothing new.
{rules}
Output the complete file between the markers, then a summary:
===FILE===
(complete CV file)
===END===
===SUMMARY===
(two to four sentences in {language}: structure, what needs checking)

=== DETAILS ===
{details}

=== EXISTING CV ===
{old}

=== PROFILE AND EARLIER CVS ===
{sources}
""",
    "style": """Derive a style description from the earlier cover letters of {person} so that an AI can write future letters
in the same voice.

Describe in Markdown: tone and attitude, structure (opening, middle, closing), typical phrases and sentence length,
how evidence is woven in, how gaps are handled, what is avoided. Back every rule with a short quote from the
letters. Keep existing style rules unless the letters contradict them. Write in the language of the letters.
{rules}
Output the complete file between the markers, then a summary:
===FILE===
(complete style file)
===END===
===SUMMARY===
(two to four sentences in {language}: what is new or changed)

=== EXISTING STYLE ===
{old}

=== LETTERS ===
{sources}
""",
}
LANGUAGE_NAMES = {"de": "German", "en": "English"}


def suggestion(provider, root, kind, lang="de"):
    """AI suggestion for the profile (from documents), the CV content (from profile and old CVs) or the style
    (from earlier letters). Writes nothing; returns {"new", "old", "summary"}."""
    import ai
    import documents
    p = load(root)
    old = text(root, p, kind)
    details = "\n".join(f"{k}: {p[k]}" for k in TEXT_FIELDS if p.get(k) and k not in ("photo", "signature"))
    if kind == "profile":
        sources = documents.collect(root, p)
    elif kind == "cv":
        sources = f"--- Profile ---\n{text(root, p, 'profile')}\n\n" + documents.collect(root, p, categories={"cvs"}, total=30000)
    elif kind == "style":
        letters = sorted(layout.get(root).letters(), key=lambda x: x.stat().st_mtime, reverse=True)[:8]
        sources = "\n\n".join(f"--- {b.parent.name} ---\n{letter_part(b.read_text('utf-8'))[:5000]}" for b in letters)
        sources += "\n\n" + documents.collect(root, p, categories={"letters"}, total=20000)
        details = ""
    else:
        raise ValueError("unknown kind")
    if not sources.strip():
        raise ValueError("No readable documents found" if kind == "profile" else "No earlier letters found")
    r = rules(p)
    prompt = SUGGESTION[kind].format(person=person(p), rules=f"- Writing rules:\n{r}\n" if r else "", details=details or "(none)",
                                     old=old or "(still empty)", sources=sources[:70000], language=LANGUAGE_NAMES.get(lang, "German"))
    out = ai.complete(provider, prompt, max_tokens=8000, timeout=900, cwd=root, disallowed=["Bash", "Write", "Edit"])
    new = ai.between(out, "===FILE===", "===END===").strip()
    if not new:
        raise RuntimeError("Answer without a file between the markers: " + out[:300])
    summary = out.split("===SUMMARY===")[-1].strip() if "===SUMMARY===" in out else ""
    return {"new": ss(new, p) + "\n", "old": old, "summary": summary}


def load(root):
    """Profile merged with the defaults (nested dicts field by field), with English keys."""
    lay = layout.get(root)
    own = lay.read_json("profile_file", {})
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT.items()}
    p["files"] = dict(lay.profile_files)
    for k, v in own.items():
        if isinstance(p.get(k), dict) and isinstance(v, dict):
            p[k].update(v)
        else:
            p[k] = v
    return p


def path(root, p, key):
    return Path(root) / p["files"][key]


def text(root, p, key, limit=None):
    f = path(root, p, key)
    t = f.read_text("utf-8") if f.is_file() else ""
    return t[:limit] if limit else t


def facts_text(root, p):
    """Everything the AI may take facts from, for the fact check: profile, CV content and the details."""
    return "\n".join([text(root, p, "profile"), text(root, p, "cv"), json.dumps(p, ensure_ascii=False)])


LETTER_HEAD = re.compile(r"^# (Bewerbungsschreiben|Cover Letter)\s*$", re.M)  # the letter itself starts here (content language)


def letter_part(text):
    """The letter without job info and mapping: from the heading "# Cover Letter" / "# Bewerbungsschreiben" on."""
    m = list(LETTER_HEAD.finditer(text))
    return text[m[-1].start():] if m else text


def person(p):
    """How the person is called in prompts: their name or a neutral phrase."""
    return p.get("name") or "the applicant"


def rules(p):
    """Writing rules as a "-" list for prompts; empty when none are set."""
    r = [str(x).strip() for x in p.get("rules", []) if str(x).strip()]
    return "\n".join(f"- {x}" for x in r)


def ss(t, p):
    return t.replace("ß", "ss") if p.get("swiss_ss") else t


def _name_for_file(p):
    s = p.get("name") or "Application"
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()  # ë -> e
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "Application"


def file_name(p, kind, company, mmyy):
    """kind: "cover_letter" or "cv"; without extension."""
    return p["file_names"][kind].format(name=_name_for_file(p), company=company, mmyy=mmyy)


def prefix(p, kind):
    """Fixed start of the file name up to {company}, used to find earlier files as templates."""
    return p["file_names"][kind].split("{company}")[0].format(name=_name_for_file(p), mmyy="")


def own_mail(p, sender):
    """Was a mail sent by the person themselves (for "mail out")?"""
    a = sender.lower()
    return bool((p.get("email") and p["email"].lower() in a) or (p.get("name") and p["name"].lower() in a))


if __name__ == "__main__":  # self-test, English and German layout
    import tempfile
    d = Path(tempfile.mkdtemp())
    empty = load(d)
    assert empty["name"] == "" and empty["files"]["profile"] == "profile/profile.md" and person(empty) == "the applicant"
    (d / "application-board.json").write_text(json.dumps({"name": "Zoë Müller", "town": "Bern", "email": "zoe@x.ch", "swiss_ss": True,
                                                        "rules": ["No dashes", " "], "files": {"style": "x/style.md"}}), "utf-8")
    p = load(d)
    assert p["files"] == {**layout.get(d).profile_files, "style": "x/style.md"}, p["files"]
    assert file_name(p, "cover_letter", "ACME", "1126") == "CL_Zoe_Mueller_ACME_1126", file_name(p, "cover_letter", "ACME", "1126")
    assert prefix(p, "cv") == "CV_Zoe_Mueller_"
    assert rules(p) == "- No dashes" and ss("Grüße", p) == "Grüsse"
    assert own_mail(p, "Zoë Müller <zoe@x.ch>") and not own_mail(p, "HR <hr@acme.ch>")
    q = save(d, {"phone": "+41 1", "rules": ["a", ""], "file_names": {"cover_letter": "Letter_{company}", "cv": "CV_{name}_{company}"}})
    assert q["phone"] == "+41 1" and q["rules"] == ["a"] and q["name"] == "Zoë Müller" and q["files"]["style"] == "x/style.md"
    for bad in ({"photo": "../x.jpg"}, {"file_names": {"cover_letter": "CL", "cv": "CV_{company}"}},
                {"file_names": {"cover_letter": "CL_{company}_{x}", "cv": "CV_{company}"}}, {"document_folders": [{"path": "/etc"}]}):
        try:
            save(d, bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    g = Path(tempfile.mkdtemp())  # German layout: read and written with the German keys
    (g / "Bewerbungen").mkdir()
    (g / "bewerbungs-board.json").write_text(json.dumps({"name": "Zoe", "ort": "Bern", "ss_statt_sz": True, "dateiname": {"anschreiben": "CL_{firma}"},
                                                        "dokument_ordner": [{"pfad": "Zeugnisse", "kategorie": "Zeugnisse"}]}), "utf-8")
    p = load(g)
    assert p["town"] == "Bern" and p["swiss_ss"] and p["file_names"]["cover_letter"] == "CL_{company}" and p["files"]["profile"] == "Profil/profil.md"
    assert p["document_folders"] == [{"path": "Zeugnisse", "category": "certificates"}]
    save(g, {"phone": "1"})
    raw = json.loads((g / "bewerbungs-board.json").read_text("utf-8"))
    assert raw["telefon"] == "1" and raw["ort"] == "Bern" and raw["dateiname"]["anschreiben"] == "CL_{firma}" and "town" not in raw, raw
    assert letter_part("# Infos\nx\n# Cover Letter\nDear") == "# Cover Letter\nDear"
    print("ok")
