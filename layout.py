"""Where things live in a data folder, in the English layout and in the German one of the app's first versions.

New data folders use English folder, file and key names:

    application-board.json                profile (name, town, rules, file names, ...)
    profile/profile.md, style.md, ...     profile texts
    profile/documents/<category>/         uploaded documents
    profile/templates/                    Word templates
    applications/<Company>/letter_X.md    job info, mapping and letter
    applications/<Company>/log_X.md       pipeline log
    applications/_review/                 state.json, feedback_log.jsonl, learnings_state.json, backups/
    applications/_jobsearch/              jobsearch_<date>.md, search.json, history.jsonl, postings.json

A folder of the old German layout (Bewerbungen/, Profil/, bewerbungs-board.json, review.json, suche.json, ...) keeps
working as it is and is written back in its own format: the code works with the English names only, and this
module translates the JSON keys and values when such a folder is read or written. Nothing is renamed on disk.
"""
import json
from pathlib import Path

import storage

# English name -> German name of the first versions
PATHS = {
    "profile_file": ("application-board.json", "bewerbungs-board.json"),
    "profile_dir": ("profile", "Profil"),
    "documents": ("profile/documents", "Profil/Dokumente"),
    "templates": ("profile/templates", "Profil/Vorlagen"),
    "textcache": ("profile/.textcache", "Profil/.textcache"),
    "apps": ("applications", "Bewerbungen"),
    "review": ("applications/_review", "Bewerbungen/_Review"),
    "state": ("applications/_review/state.json", "Bewerbungen/_Review/review.json"),
    "feedback_log": ("applications/_review/feedback_log.jsonl", "Bewerbungen/_Review/feedback_log.jsonl"),
    "learnings_state": ("applications/_review/learnings_state.json", "Bewerbungen/_Review/lernstand.json"),
    "backups": ("applications/_review/backups", "Bewerbungen/_Review/backups"),
    "search": ("applications/_jobsearch", "Bewerbungen/_Stellensuche"),
    "search_config": ("applications/_jobsearch/search.json", "Bewerbungen/_Stellensuche/suche.json"),
    "history": ("applications/_jobsearch/history.jsonl", "Bewerbungen/_Stellensuche/verlauf.jsonl"),
    "postings": ("applications/_jobsearch/postings.json", "Bewerbungen/_Stellensuche/status.json"),
}
NAMES = {  # parts of file names
    "letter": ("letter", "Bewerbungsschreiben"),    # letter_X.md / Bewerbungsschreiben_X.md
    "log": ("log", "Verlauf"),                       # log_X.md / Verlauf_X.md
    "run": ("jobsearch", "Stellensuche"),            # jobsearch_2026-11-24.md / Stellensuche_2026-11-24.md
    "preview": (".preview", ".vorschau"),            # template previews: profile/templates/.preview/
}
# document categories = folder names under documents/
CATEGORIES = {"cvs": "Lebenslaeufe", "certificates": "Zeugnisse", "diplomas": "Diplome", "letters": "Bewerbungsschreiben", "other": "Sonstiges"}
PROFILE_FILES = {  # default places of the profile texts
    "profile": ("profile/profile.md", "Profil/profil.md"),
    "style": ("profile/style.md", "Profil/stil.md"),
    "search_profile": ("profile/search-profile.md", "Profil/suchprofil.md"),
    "grading": ("profile/grading.md", "Profil/bewertung.md"),
    "cv": ("profile/cv.md", "Profil/lebenslauf.md"),
}

# JSON keys per file: German (stored in old folders) -> English (used in the code). Per file, because a name that is
# German in one file can be English already in another (the feedback log always had "path", the profile "pfad"). Each
# table must stay one to one so writing back gives the old names again.
_PROFILE = {"ort": "town", "telefon": "phone", "adresse": "address", "foto": "photo", "unterschrift": "signature",
            "dokument_ordner": "document_folders", "pfad": "path", "kategorie": "category", "vorlagen": "templates",
            "anschreiben": "cover_letter", "lebenslauf": "cv", "dateiname": "file_names", "regeln": "rules", "ss_statt_sz": "swiss_ss",
            "regelcheck": "rule_check", "woerter_min": "words_min", "woerter_max": "words_max", "zeichen": "chars", "dateien": "files",
            "profil": "profile", "stil": "style", "suchprofil": "search_profile", "bewertung": "grading"}
_SEARCH = {"felder": "fields", "an": "on", "begriffe": "terms", "orte": "places", "zentren": "centers", "umkreis_km": "radius_km",
           "frische_tage": "max_age_days", "ziel_min": "target_min", "ziel_max": "target_max", "quellen": "sources", "ausschluss": "exclude",
           "raster": "scale", "anbieter": "provider", "ausgeblendet": "hidden", "linkedin_orte": "linkedin_places", "firmen": "companies",
           "eigene_quellen": "custom_sources", "seiten": "pages", "remote_regionen": "remote_places",
           "automatisch": "auto", "automatisch_stunde": "auto_hour"}
FILE_KEYS = {
    "profile_file": _PROFILE,
    "state": {"seit": "since", "antwort": "answer", "pruefung": "hints", "finalisiert": "finalized", "dateien": "files",
              "vorlagen": "templates", "sprache": "language", "kurz": "short"},
    "search_config": _SEARCH,
    "history": {"firma": "company", "titel": "title", "gefunden": "found"},
    "postings": {"grund": "reason", "geprueft": "checked"},
    "feedback_log": {"firma": "company", "antwort": "answer", "typ": "kind", "anbieter": "provider", "sitzung": "session",
                     "nachgetragen": "backfilled", "runden": "rounds"},
    "learnings_state": {"bis": "until"},
}
# stored values per file and key
_STATUS = {"offen": "open", "abgeschickt": "sent", "archiv": "archived"}
_PHASE = {"abgeschickt": "sent", "keine_antwort": "no_reply", "antwort": "replied", "gespraech": "interview", "angebot": "offer", "absage": "rejected"}
FILE_VALUES = {
    "state": {"status": _STATUS, "phase": _PHASE},
    "feedback_log": {"kind": {"rueckgaengig": "undo"}},
    "profile_file": {"category": {v: k for k, v in CATEGORIES.items()}},
}
PLACEHOLDERS = (("{firma}", "{company}"),)  # in the file name patterns


def legacy(root):
    """Does the folder use the German layout of the first versions?"""
    root = Path(root)
    return (root / "Bewerbungen").is_dir() or ((root / "bewerbungs-board.json").is_file() and not (root / "application-board.json").is_file())


class Layout:
    def __init__(self, root):
        self.root = Path(root)
        self.legacy = legacy(root)
        i = 1 if self.legacy else 0
        for k, v in PATHS.items():
            setattr(self, k, self.root / v[i])
        self.letter, self.log, self.run, self.preview = (NAMES[k][i] for k in ("letter", "log", "run", "preview"))
        self.categories = {k: (v if self.legacy else k) for k, v in CATEGORIES.items()}  # English id -> folder name
        self.profile_files = {k: v[i] for k, v in PROFILE_FILES.items()}

    def rel(self, key):
        return getattr(self, key).relative_to(self.root).as_posix()

    def is_letter(self, name):
        return name.startswith(self.letter) and name.endswith(".md")

    def letter_name(self, short):
        return f"{self.letter}_{short}.md"

    def log_of(self, letter):
        """Log file next to a letter: letter_X.md -> log_X.md (Bewerbungsschreiben_X.md -> Verlauf_X.md)."""
        return letter.with_name(self.log + letter.name[len(self.letter):])

    def letters(self):
        return sorted(self.apps.glob(f"*/{self.letter}*.md")) if self.apps.is_dir() else []

    # JSON in this folder's format <-> English names; kind is the key in PATHS of the file it comes from
    def to_en(self, obj, kind):
        return translate(obj, FILE_KEYS[kind], FILE_VALUES.get(kind, {})) if self.legacy else obj

    def to_disk(self, obj, kind):
        return translate(obj, _inverse(FILE_KEYS[kind]), {k: _inverse(m) for k, m in FILE_VALUES.get(kind, {}).items()}, back=True) if self.legacy else obj

    def read_json(self, kind, default=None):
        try:
            return self.to_en(json.loads(getattr(self, kind).read_text("utf-8")), kind)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write_json(self, kind, obj):
        p = getattr(self, kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        storage.write_atomic(p, json.dumps(self.to_disk(obj, kind), ensure_ascii=False, indent=2) + "\n")

    def read_jsonl(self, kind):
        p = getattr(self, kind)
        out = []
        for line in p.read_text("utf-8").splitlines() if p.is_file() else []:
            try:
                out.append(self.to_en(json.loads(line), kind))
            except json.JSONDecodeError:
                continue
        return out

    def append_jsonl(self, kind, entries):
        p = getattr(self, kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(self.to_disk(e, kind), ensure_ascii=False) + "\n")


def _inverse(m):
    return {v: k for k, v in m.items()}


def translate(obj, keys, values, back=False):
    """Renames the known keys at every depth and the known values of "status", "phase", ... Unknown keys (for example
    the letter paths that review.json is keyed by) stay as they are."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            new = keys.get(k, k)
            v = translate(v, keys, values, back)
            vkey = k if back else new  # the value tables are keyed by the English name
            if isinstance(v, str):
                v = values.get(vkey, {}).get(v, v)
                for de, en in PLACEHOLDERS:
                    v = v.replace(en, de) if back else v.replace(de, en)
            out[new] = v
        return out
    if isinstance(obj, list):
        return [translate(v, keys, values, back) for v in obj]
    return obj


_CACHE = {}


def get(root):
    """Layout of a data folder; looked up again when the folder changes from empty to German."""
    key = str(Path(root).resolve())
    lay = _CACHE.get(key)
    if lay is None or (not lay.legacy and legacy(root)):
        lay = _CACHE[key] = Layout(root)
    return lay


if __name__ == "__main__":  # self-test
    import tempfile
    for kind, m in FILE_KEYS.items():
        assert len(_inverse(m)) == len(m), f"{kind}: keys must be one to one"
    for kind, vals in FILE_VALUES.items():
        assert all(len(_inverse(v)) == len(v) for v in vals.values()), kind
    new = Path(tempfile.mkdtemp())
    a = get(new)
    assert not a.legacy and a.rel("state") == "applications/_review/state.json" and a.letter_name("X") == "letter_X.md"
    assert a.log_of(new / "applications" / "A" / "letter_X.md").name == "log_X.md" and a.categories["cvs"] == "cvs"
    old = Path(tempfile.mkdtemp())
    (old / "Bewerbungen").mkdir()
    b = get(old)
    assert b.legacy and b.rel("search_config") == "Bewerbungen/_Stellensuche/suche.json" and b.categories["cvs"] == "Lebenslaeufe"
    assert b.log_of(old / "Bewerbungen" / "A" / "Bewerbungsschreiben_X.md").name == "Verlauf_X.md"
    stored = {"Bewerbungen/A/Bewerbungsschreiben_X.md": {"status": "abgeschickt", "phase": "gespraech", "seit": "2026-01-01",
                                                         "dateien": {"cl_pdf": "x.pdf"}, "feedback": "f", "unbekannt": 1}}
    en = b.to_en(stored, "state")
    assert en == {"Bewerbungen/A/Bewerbungsschreiben_X.md": {"status": "sent", "phase": "interview", "since": "2026-01-01",
                                                             "files": {"cl_pdf": "x.pdf"}, "feedback": "f", "unbekannt": 1}}, en
    assert b.to_disk(en, "state") == stored
    prof = {"ort": "Bern", "dateiname": {"anschreiben": "CL_{name}_{firma}_{mmyy}"}, "dokument_ordner": [{"pfad": "Zeugnisse", "kategorie": "Zeugnisse"}]}
    assert b.to_en(prof, "profile_file") == {"town": "Bern", "file_names": {"cover_letter": "CL_{name}_{company}_{mmyy}"},
                                             "document_folders": [{"path": "Zeugnisse", "category": "certificates"}]}, b.to_en(prof, "profile_file")
    assert b.to_disk(b.to_en(prof, "profile_file"), "profile_file") == prof
    rnd = {"ts": "t", "path": "Bewerbungen/A/x.md", "firma": "A", "typ": "rueckgaengig", "antwort": "a"}  # "path" was always English here
    assert b.to_en(rnd, "feedback_log") == {"ts": "t", "path": "Bewerbungen/A/x.md", "company": "A", "kind": "undo", "answer": "a"}
    assert b.to_disk(b.to_en(rnd, "feedback_log"), "feedback_log") == rnd
    posting = {"https://x": {"status": "open", "grund": "", "geprueft": "2026-01-01"}}  # status values were English from the start
    assert b.to_disk(b.to_en(posting, "postings"), "postings") == posting and b.to_en(posting, "postings")["https://x"]["status"] == "open"
    b.write_json("state", en)
    assert json.loads(b.state.read_text("utf-8")) == stored and b.read_json("state") == en
    assert a.to_en(stored, "state") is stored  # English folders are taken as they are
    print("ok")
