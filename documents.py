"""Profile documents: certificates, old CVs, earlier applications, diplomas.

Uploaded files live in the data folder under profile/documents/<category>/ (Profil/Dokumente/<German name> in the
German layout, see layout.py). Existing folders can also be linked (profile field "document_folders") without
copying anything. The text is extracted for the AI (PDF via pdftotext, docx/odt via their XML) and cached in
profile/.textcache/, valid as long as size and modification time stay the same.

Categories are English ids (cvs, certificates, diplomas, letters, other); the folder names come from layout.py.
"""
import csv
import hashlib
import html
import io
import json
import re
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import layout
import storage

CATEGORIES = tuple(layout.CATEGORIES)
TEXT_TYPES = {".pdf", ".docx", ".odt", ".txt", ".md", ".zip", ".json"}  # .zip: LinkedIn export, .json: JSON Resume
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_SIZE = 30 * 1024 * 1024


def folders(root, prof):
    """[(path, category, linked?)]: one upload folder per category plus the linked folders."""
    lay = layout.get(root)
    out = [(lay.documents / lay.categories[k], k, False) for k in CATEGORIES]
    for o in prof.get("document_folders", []):
        if isinstance(o, dict) and o.get("path"):
            out.append((Path(root) / o["path"], o.get("category") or Path(o["path"]).name, True))
    return out


def allowed(root, prof, rel):
    """Absolute path of rel, only inside the document folders (or the profile's photo/signature)."""
    p = (Path(root) / rel).resolve()
    lay = layout.get(root)
    bases = [d.resolve() for d, _, _ in folders(root, prof)] + [lay.documents.resolve(), lay.templates.resolve()]
    extra = {(Path(root) / prof[k]).resolve() for k in ("photo", "signature") if prof.get(k)}
    if not p.is_file() or not (p in extra or any(p.is_relative_to(b) for b in bases)):
        raise ValueError("Path not allowed")
    return p


def listing(root, prof):
    root = Path(root)
    out, seen = [], set()
    for d, category, linked in folders(root, prof):
        for p in sorted(d.rglob("*")) if d.is_dir() else []:
            if not p.is_file() or p.name.startswith((".", "~")) or p in seen or ".textcache" in p.parts:
                continue
            seen.add(p)
            st = p.stat()
            out.append({"rel": p.relative_to(root).as_posix(), "name": p.name, "category": category, "linked": linked,
                        "folder": p.parent.relative_to(root).as_posix(), "size": st.st_size, "type": p.suffix.lower(),
                        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "text": p.suffix.lower() in TEXT_TYPES, "image": p.suffix.lower() in IMAGE_TYPES})
    return out


def _cache(root, p):
    st = p.stat()
    key = hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:20]
    return layout.get(root).textcache / f"{key}.txt"


def _xml_text(xml):
    xml = re.sub(r"</(w:p|text:p|text:h)>|<(w:br|w:tab|text:line-break|text:tab)[^>]*/>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


# Files of a LinkedIn data export ("Get a copy of your data") that describe the person, in this order
LINKEDIN = ("Profile.csv", "Positions.csv", "Education.csv", "Skills.csv", "Languages.csv", "Certifications.csv", "Projects.csv",
            "Honors.csv", "Courses.csv", "Volunteering.csv", "Publications.csv")


def linkedin_text(z):
    """Readable text of a LinkedIn export zip; each file at most 1 MB (no zip bombs)."""
    names = {Path(n).name: n for n in z.namelist()}
    out = []
    for f in LINKEDIN:
        if f not in names or z.getinfo(names[f]).file_size > 1_000_000:
            continue
        rows = list(csv.DictReader(io.StringIO(z.read(names[f]).decode("utf-8-sig", "replace"))))
        if rows:
            out.append(f"## {f[:-4]}")
            out += ["- " + " | ".join(f"{k}: {v.strip()}" for k, v in r.items() if k and v and v.strip()) for r in rows]
            out.append("")
    return "\n".join(out)


def json_text(obj, depth=0):
    """Any JSON (e.g. JSON Resume) as indented lines, keys as labels."""
    pad = "  " * depth
    if isinstance(obj, dict):
        return "\n".join(f"{pad}{k}:" + ("\n" + json_text(v, depth + 1) if isinstance(v, (dict, list)) else f" {v}")
                         for k, v in obj.items() if v not in ("", None, [], {}))
    if isinstance(obj, list):
        return "\n".join(json_text(v, depth) if isinstance(v, (dict, list)) else f"{pad}- {v}" for v in obj)
    return f"{pad}{obj}"


def text(root, p):
    """Text of a file, from the cache or freshly extracted. Empty when nothing is readable."""
    p = Path(p)
    c = _cache(root, p)
    if c.is_file():
        return c.read_text("utf-8")
    t, kind = "", p.suffix.lower()
    try:
        if kind == ".pdf":
            t = subprocess.run([storage.tool("pdftotext") or "pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60).stdout
        elif kind == ".docx":
            t = _xml_text(zipfile.ZipFile(p).read("word/document.xml").decode("utf-8", "ignore"))
        elif kind == ".odt":
            t = _xml_text(zipfile.ZipFile(p).read("content.xml").decode("utf-8", "ignore"))
        elif kind in (".txt", ".md"):
            t = p.read_text("utf-8", "ignore")
        elif kind == ".zip":
            t = linkedin_text(zipfile.ZipFile(p))
        elif kind == ".json":
            t = json_text(json.loads(p.read_text("utf-8", "ignore")))
    except (OSError, zipfile.BadZipFile, KeyError, subprocess.SubprocessError, ValueError):
        t = ""
    t = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", t)).strip()
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(t, "utf-8")
    return t


def store(root, category, name, data):
    """Saves an upload in the folder of the category, never overwriting."""
    if category not in CATEGORIES:
        raise ValueError("Unknown category")
    if len(data) > MAX_SIZE:
        raise ValueError("File too large (30 MB at most)")
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", Path(name).name).strip(" .") or "Document"
    d = layout.get(root).documents / layout.get(root).categories[category]
    d.mkdir(parents=True, exist_ok=True)
    p, stem, n = d / name, Path(name).stem, 2
    while p.exists():
        p, n = d / f"{stem}_{n}{Path(name).suffix}", n + 1
    p.write_bytes(data)
    return p.relative_to(root).as_posix()


def delete(root, rel):
    """Uploads only; linked folders belong to the user and are never touched."""
    p = (Path(root) / rel).resolve()
    if not p.is_file() or not p.is_relative_to(layout.get(root).documents.resolve()):
        raise ValueError("Only uploaded documents can be deleted here")
    p.unlink()


def collect(root, prof, each=6000, total=60000, categories=None):
    """Texts of all readable documents for a prompt, newest first, each shortened."""
    parts, size = [], 0
    for d in sorted(listing(root, prof), key=lambda d: d["mtime"], reverse=True):
        if not d["text"] or (categories and d["category"] not in categories):
            continue
        t = text(root, Path(root) / d["rel"])[:each]
        if not t.strip():
            continue
        parts.append(f"--- {d['category']}: {d['rel']} ---\n{t}")
        size += len(t)
        if size > total:
            break
    return "\n\n".join(parts)


if __name__ == "__main__":  # self-test
    import io
    import tempfile
    r = Path(tempfile.mkdtemp())
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("Positions.csv", "\ufeffCompany Name,Title,Started On\nAcme,Developer,Jan 2024\n")
        z.writestr("Connections.csv", "First Name\nX\n")
    (r / "li.zip").write_bytes(zbuf.getvalue())
    (r / "cv.json").write_text(json.dumps({"basics": {"name": "Zoë", "email": ""}, "skills": [{"name": "C#", "keywords": ["Unity"]}]}), "utf-8")
    assert text(r, r / "li.zip") == "## Positions\n- Company Name: Acme | Title: Developer | Started On: Jan 2024", text(r, r / "li.zip")
    assert text(r, r / "cv.json") == "basics:\n  name: Zoë\nskills:\n  name: C#\n  keywords:\n    - Unity", text(r, r / "cv.json")
    (r / "Cert").mkdir()
    (r / "Cert" / "a.txt").write_text("Grade 5.5 in maths", "utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>First &amp; line</w:t></w:r></w:p><w:p><w:t>Second</w:t></w:p></w:document>")
    prof = {"document_folders": [{"path": "Cert", "category": "certificates"}]}
    rel = store(r, "cvs", "../../evil CV.docx", buf.getvalue())
    assert rel == "profile/documents/cvs/evil CV.docx", rel
    assert store(r, "cvs", "evil CV.docx", b"x").endswith("evil CV_2.docx")
    entries = listing(r, prof)
    assert {d["name"] for d in entries} == {"a.txt", "evil CV.docx", "evil CV_2.docx"} and any(d["linked"] for d in entries)
    assert text(r, r / rel) == "First & line\nSecond", repr(text(r, r / rel))
    assert "maths" in collect(r, prof) and "First" in collect(r, prof) and "maths" not in collect(r, prof, categories={"cvs"})
    for bad in ("../etc/passwd", "profile/.textcache"):
        try:
            allowed(r, prof, bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    try:
        delete(r, "Cert/a.txt")
        raise AssertionError("a linked file must not be deletable")
    except ValueError:
        pass
    delete(r, rel)
    g = Path(tempfile.mkdtemp())  # German layout: the German folder names
    (g / "Bewerbungen").mkdir()
    assert store(g, "cvs", "cv.txt", b"x") == "Profil/Dokumente/Lebenslaeufe/cv.txt"
    assert [d["category"] for d in listing(g, {})] == ["cvs"]
    print("ok")
