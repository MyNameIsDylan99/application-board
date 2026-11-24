"""Personal profile from the data folder: <data folder>/bewerbungs-board.json.

Everything that describes the person (name, town, e-mail, file names, writing rules, where the profile,
style and search profile live) is stored there, not in the code. Missing file or fields fall back to the
neutral values in DEFAULT. Paths under "dateien" are relative to the data folder.

The JSON keys are the stored data format and stay as they are (German) so existing data keeps working.
"""
import json
import re
import unicodedata
from pathlib import Path

import storage

FILE = "bewerbungs-board.json"
DEFAULT = {
    "name": "",                 # "First Last", appears under the letter and in the sender block of the Word template
    "ort": "",                  # town of the date line ("<town>, 12 November 2026") and after the title in the CV
    "email": "",                # anchor in the Word template and used to recognise one's own mails
    "telefon": "",
    "adresse": "",              # may span several lines, used in templates
    "foto": "",                 # application photo, relative to the data folder
    "unterschrift": "",         # signature image, relative to the data folder
    "dokument_ordner": [],      # linked folders [{"pfad": "Zeugnisse", "kategorie": "Zeugnisse"}], never copied
    "vorlagen": {"anschreiben": "", "lebenslauf": ""},  # chosen Word templates; empty: newest earlier file matching the file name pattern
    "dateiname": {"anschreiben": "CL_{name}_{firma}_{mmyy}", "lebenslauf": "CV_{name}_{firma}_{mmyy}"},
    "regeln": [],               # writing rules added to every AI prompt, one sentence per rule
    "ss_statt_sz": False,       # Swiss spelling: replace "ß" with "ss" in AI texts
    # rule check in the editor: word range of the letter text (0 = no check) and characters that must not appear
    "regelcheck": {"woerter_min": 0, "woerter_max": 0, "zeichen": []},
    "dateien": {                # relative to the data folder
        "profil": "Profil/profil.md",           # CV as text: positions, projects, skills
        "stil": "Profil/stil.md",               # writing style of the letters
        "suchprofil": "Profil/suchprofil.md",   # what to search for, search terms, exclusion criteria
        "bewertung": "Profil/bewertung.md",     # grading scheme of the job search
        "lebenslauf": "Profil/lebenslauf.md",   # CV content the CV template is generated from
    },
}
TEXT_FIELDS = ("name", "ort", "email", "telefon", "adresse", "foto", "unterschrift")


def _relative_ok(v):
    return isinstance(v, str) and not v.startswith(("/", "\\")) and ".." not in Path(v).parts and ":" not in v


def save(root, new):
    """Takes the known fields from new, validates them and writes bewerbungs-board.json.
    Fields the app does not know are kept in the file."""
    f = Path(root) / FILE
    try:
        old = json.loads(f.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        old = {}
    for k in TEXT_FIELDS:
        if k in new:
            v = str(new[k] or "").strip()
            if k in ("foto", "unterschrift") and v and not _relative_ok(v):
                raise ValueError(f"{k}: give a path relative to the data folder")
            old[k] = v
    if "regeln" in new:
        old["regeln"] = [str(x).strip() for x in new["regeln"] if str(x).strip()]
    if "ss_statt_sz" in new:
        old["ss_statt_sz"] = bool(new["ss_statt_sz"])
    if "regelcheck" in new:
        r = new["regelcheck"] or {}
        try:
            lo, hi = int(r.get("woerter_min") or 0), int(r.get("woerter_max") or 0)
        except (TypeError, ValueError):
            raise ValueError("Rule check: word counts must be numbers")
        if lo < 0 or hi < 0 or (hi and lo > hi):
            raise ValueError("Rule check: the minimum must not be above the maximum")
        old["regelcheck"] = {"woerter_min": lo, "woerter_max": hi, "zeichen": [str(z) for z in r.get("zeichen", []) if str(z)][:20]}
    if "dateiname" in new:
        d = {k: str(new["dateiname"].get(k, "")).strip() for k in ("anschreiben", "lebenslauf")}
        for k, v in d.items():
            if "{firma}" not in v or re.search(r"[\\/:*?\"<>|]", v):
                raise ValueError(f"File name {k}: must contain {{firma}} and no special characters such as / : *")
            try:
                v.format(name="x", firma="x", mmyy="x")
            except (KeyError, IndexError, ValueError):
                raise ValueError(f"File name {k}: only the placeholders {{name}}, {{firma}}, {{mmyy}} are allowed")
        old["dateiname"] = d
    if "dateien" in new:
        d = {k: str(v).strip() for k, v in new["dateien"].items() if k in DEFAULT["dateien"]}
        if not all(_relative_ok(v) and v for v in d.values()):
            raise ValueError("Give file paths relative to the data folder")
        old["dateien"] = {**old.get("dateien", {}), **d}
    if "vorlagen" in new:
        v = {k: str(new["vorlagen"].get(k, "") or "").strip() for k in ("anschreiben", "lebenslauf")}
        if not all(not x or (_relative_ok(x) and x.endswith(".docx")) for x in v.values()):
            raise ValueError("Template: give a docx path relative to the data folder")
        old["vorlagen"] = v
    if "dokument_ordner" in new:
        o = [{"pfad": str(x.get("pfad", "")).strip(), "kategorie": str(x.get("kategorie", "")).strip()} for x in new["dokument_ordner"]]
        if not all(_relative_ok(x["pfad"]) and x["pfad"] for x in o):
            raise ValueError("Give document folders relative to the data folder")
        old["dokument_ordner"] = o
    storage.write_atomic(f, json.dumps(old, ensure_ascii=False, indent=2) + "\n")
    return load(root)


SUGGESTION = {
    "profil": """Create or extend the job application profile of {person}. The profile is the only source of facts that
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
(complete profil.md)
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
    "lebenslauf": """Create the content of a CV for {person} from the profile, the details and earlier CVs.
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
(complete lebenslauf.md)
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
    "stil": """Derive a style description from the earlier cover letters of {person} so that an AI can write future letters
in the same voice.

Describe in Markdown: tone and attitude, structure (opening, middle, closing), typical phrases and sentence length,
how evidence is woven in, how gaps are handled, what is avoided. Back every rule with a short quote from the
letters. Keep existing style rules unless the letters contradict them. Write in the language of the letters.
{rules}
Output the complete file between the markers, then a summary:
===FILE===
(complete stil.md)
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
    """AI suggestion for profil.md (from documents), lebenslauf.md (from profile and old CVs) or stil.md
    (from earlier letters). Writes nothing; returns {"new", "old", "summary"}."""
    import ai
    import documents
    p = load(root)
    old = text(root, p, kind)
    details = "\n".join(f"{k}: {p[k]}" for k in TEXT_FIELDS if p.get(k) and k not in ("foto", "unterschrift"))
    if kind == "profil":
        sources = documents.collect(root, p)
    elif kind == "lebenslauf":
        sources = f"--- Profile ---\n{text(root, p, 'profil')}\n\n" + documents.collect(root, p, categories={"Lebenslaeufe"}, total=30000)
    elif kind == "stil":
        letters = sorted(Path(root).glob("Bewerbungen/*/Bewerbungsschreiben*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:8]
        sources = "\n\n".join(f"--- {b.parent.name} ---\n{b.read_text('utf-8')[b.read_text('utf-8').rfind('# Bewerbungsschreiben'):][:5000]}" for b in letters)
        sources += "\n\n" + documents.collect(root, p, categories={"Bewerbungsschreiben"}, total=20000)
        details = ""
    else:
        raise ValueError("unknown kind")
    if not sources.strip():
        raise ValueError("No readable documents found" if kind == "profil" else "No earlier letters found")
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
    """Profile merged with the defaults (nested dicts field by field)."""
    try:
        own = json.loads((Path(root) / FILE).read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        own = {}
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT.items()}
    for k, v in own.items():
        if isinstance(p.get(k), dict) and isinstance(v, dict):
            p[k].update(v)
        else:
            p[k] = v
    return p


def path(root, p, key):
    return Path(root) / p["dateien"][key]


def text(root, p, key, limit=None):
    f = path(root, p, key)
    t = f.read_text("utf-8") if f.is_file() else ""
    return t[:limit] if limit else t


def facts_text(root, p):
    """Everything the AI may take facts from, for the fact check: profile, CV content and the details."""
    return "\n".join([text(root, p, "profil"), text(root, p, "lebenslauf"), json.dumps(p, ensure_ascii=False)])


def person(p):
    """How the person is called in prompts: their name or a neutral phrase."""
    return p.get("name") or "the applicant"


def rules(p):
    """Writing rules as a "-" list for prompts; empty when none are set."""
    r = [str(x).strip() for x in p.get("regeln", []) if str(x).strip()]
    return "\n".join(f"- {x}" for x in r)


def ss(t, p):
    return t.replace("ß", "ss") if p.get("ss_statt_sz") else t


def _name_for_file(p):
    s = p.get("name") or "Application"
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()  # ë -> e
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "Application"


def file_name(p, kind, company, mmyy):
    """kind: "anschreiben" (cover letter) or "lebenslauf" (CV); without extension."""
    return p["dateiname"][kind].format(name=_name_for_file(p), firma=company, mmyy=mmyy)


def prefix(p, kind):
    """Fixed start of the file name up to {firma}, used to find earlier files as templates."""
    return p["dateiname"][kind].split("{firma}")[0].format(name=_name_for_file(p), mmyy="")


def own_mail(p, sender):
    """Was a mail sent by the person themselves (for "Mail raus")?"""
    a = sender.lower()
    return bool((p.get("email") and p["email"].lower() in a) or (p.get("name") and p["name"].lower() in a))


if __name__ == "__main__":  # self-test
    import tempfile
    d = Path(tempfile.mkdtemp())
    empty = load(d)
    assert empty["name"] == "" and empty["dateien"]["profil"] == "Profil/profil.md" and person(empty) == "the applicant"
    (d / FILE).write_text(json.dumps({"name": "Zoë Müller", "ort": "Bern", "email": "zoe@x.ch", "ss_statt_sz": True,
                                      "regeln": ["No dashes", " "], "dateien": {"stil": "x/stil.md"}}), "utf-8")
    p = load(d)
    assert p["dateien"] == {**DEFAULT["dateien"], "stil": "x/stil.md"}, p["dateien"]
    assert file_name(p, "anschreiben", "ACME", "1126") == "CL_Zoe_Mueller_ACME_1126", file_name(p, "anschreiben", "ACME", "1126")
    assert prefix(p, "lebenslauf") == "CV_Zoe_Mueller_"
    assert rules(p) == "- No dashes" and ss("Grüße", p) == "Grüsse"
    assert own_mail(p, "Zoë Müller <zoe@x.ch>") and not own_mail(p, "HR <hr@acme.ch>")
    q = save(d, {"telefon": "+41 1", "regeln": ["a", ""], "dateiname": {"anschreiben": "Letter_{firma}", "lebenslauf": "CV_{name}_{firma}"}})
    assert q["telefon"] == "+41 1" and q["regeln"] == ["a"] and q["name"] == "Zoë Müller" and q["dateien"]["stil"] == "x/stil.md"
    for bad in ({"foto": "../x.jpg"}, {"dateiname": {"anschreiben": "CL", "lebenslauf": "CV_{firma}"}},
                {"dateiname": {"anschreiben": "CL_{firma}_{x}", "lebenslauf": "CV_{firma}"}}, {"dokument_ordner": [{"pfad": "/etc"}]}):
        try:
            save(d, bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    print("ok")
