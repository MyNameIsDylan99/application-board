"""Word templates for cover letters and CVs: generate, check, preview.

Finalizing (finalize.py) fills an existing docx through text replacement. It finds the places through two
anchors from the profile: the e-mail marks the sender cell (the cell to its right is the recipient), the name
as last paragraph marks the letter cell (salutation, text, closing, name); the date sits in a single-cell table
row directly above. The templates generated here are built exactly like that, with the standard library only
(a docx is a ZIP of XML).

Stored in the data folder: profile/templates/, preview PDFs in profile/templates/.preview/ (Profil/Vorlagen/ and
.vorschau/ in the German layout, see layout.py).
Placeholder texts and check messages follow the UI language (de/en).
"""
import io
import re
import struct
import tempfile
import zipfile
from pathlib import Path
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape

import finalize
import layout as data_layout
import userprofile

LAYOUTS = {
    "modern": {"font": "Arial", "accent": "2B5FD9", "photo": True},
    "classic": {"font": "Georgia", "accent": "333333", "photo": False},
}
EMU_CM = 360000

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = (f'xmlns:w="{W}" xmlns:r="{R}" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')

# Placeholders in a generated cover letter (replaced when finalizing) and file names, per UI language
PLACEHOLDERS = {
    "de": {"recipient": ["Firma AG", "z. Hd. Frau Muster", "Musterstrasse 1", "8000 Musterort"], "date": "1. Januar 2026",
           "subject": "Bewerbung als Stellenbezeichnung", "salutation": "Sehr geehrte Damen und Herren", "body": "Brieftext.",
           "closing": "Mit freundlichen Grüßen", "closing_ss": "Freundliche Grüsse", "town": "Ort",
           "file_cl": "Anschreiben", "file_cv": "Lebenslauf"},
    "en": {"recipient": ["Company Ltd", "Attn. Ms Sample", "1 Sample Street", "London AB1 2CD"], "date": "1 January 2026",
           "subject": "Application for the position of Job Title", "salutation": "Dear Sir or Madam", "body": "Letter text.",
           "closing": "Kind regards", "closing_ss": "Kind regards", "town": "Town",
           "file_cl": "CoverLetter", "file_cv": "CV"},
}
MESSAGES = {
    "de": {"sender": "Absender-Zelle mit {email} gefunden, Empfänger-Zelle rechts daneben",
           "body": 'Brief-Zelle mit {n} Absätzen, letzter ist "{name}"', "few": ", zu wenige (Anrede, Text, Gruss, Name)",
           "date_row": "Eigene Datumszeile über dem Brief", "date_first": "Datum als erster Absatz der Brief-Zelle",
           "name_found": 'Absatz mit dem Namen "{name}" gefunden', "name_missing": 'Absatz mit dem Namen "{name}" fehlt (nötig für die Titelzeile)'},
    "en": {"sender": "Sender cell with {email} found, recipient cell to its right",
           "body": 'Letter cell with {n} paragraphs, the last is "{name}"', "few": ", too few (salutation, text, closing, name)",
           "date_row": "Own date row above the letter", "date_first": "Date as first paragraph of the letter cell",
           "name_found": 'Paragraph with the name "{name}" found', "name_missing": 'Paragraph with the name "{name}" missing (needed for the title line)'},
}


def _m(ui, key, **kw):
    return MESSAGES.get(ui, MESSAGES["en"])[key].format(**kw)


def image_size(data):
    """(width, height) in pixels for PNG and JPEG, without an image library."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + length
    raise ValueError("The photo must be PNG or JPEG")


def _run(text, size=None, bold=False, color=None, font=None):
    rpr = "".join([f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>' if font else "",
                   "<w:b/><w:bCs/>" if bold else "", f'<w:color w:val="{color}"/>' if color else "",
                   f'<w:sz w:val="{size * 2}"/><w:szCs w:val="{size * 2}"/>' if size else ""])
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _p(text="", after=0, align=None, **run):
    ppr = f'<w:pPr><w:spacing w:before="0" w:after="{after}" w:line="276" w:lineRule="auto"/>' + (f'<w:jc w:val="{align}"/>' if align else "") + "</w:pPr>"
    return f"<w:p>{ppr}{_run(text, **run)}</w:p>"


def _cell(width, content, border_bottom=None, top=False):
    border = f'<w:tcBorders><w:bottom w:val="single" w:sz="8" w:space="0" w:color="{border_bottom}"/></w:tcBorders>' if border_bottom else ""
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{border}'
            f'<w:tcMar><w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tcMar>'
            f'<w:vAlign w:val="{"top" if top else "bottom"}"/></w:tcPr>{content}</w:tc>')


def _table(rows, columns):
    grid = "".join(f'<w:gridCol w:w="{c}"/>' for c in columns)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(columns)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
            f'<w:tblBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/>'
            f'<w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>'
            + "".join(f"<w:tr>{r}</w:tr>" for r in rows) + "</w:tbl>")


def _photo(cx, cy):
    return (f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:after="0"/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="Photo"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="photo"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdPhoto"/>'
            f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


def _load_photo(root, p, layout, height_cm):
    """(drawing XML, image data) or ("", None) when the layout has no photo or there is none."""
    if not (layout["photo"] and p.get("photo")):
        return "", None
    f = Path(root) / p["photo"]
    if not f.is_file() or f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        return "", None
    data = f.read_bytes()
    w, h = image_size(data)
    cy = int(height_cm * EMU_CM)
    return _photo(int(cy * w / h), cy), data


def _docx(root, target, body, photo_data):
    """Writes body (w:body content without sectPr) as an A4 docx. Returns the path relative to the data folder."""
    body = (f'<w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            f'<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr></w:body>')
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}>{body}</w:document>'
    parseString(doc.encode())  # raises on broken XML before a file is written
    target.parent.mkdir(parents=True, exist_ok=True)
    ext = "png" if photo_data and photo_data[:4] == b"\x89PNG" else "jpeg"
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + (f'<Relationship Id="rIdPhoto" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/photo.{ext}"/>' if photo_data else "")
            + "</Relationships>")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="png" ContentType="image/png"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/document.xml", doc)
        if photo_data:
            z.writestr(f"word/media/photo.{ext}", photo_data)
    return target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target)


def _contact(p):
    return [z for z in (p.get("address") or p.get("town") or "").splitlines() if z.strip()] + [x for x in (p.get("phone"), p["email"]) if x]


def cover_letter(root, layout="modern", target=None, ui="de"):
    """Generates a cover letter template from the profile. Returns its path relative to the data folder."""
    p = userprofile.load(root)
    finalize.set_profile(p)  # name and e-mail must be set, they are the anchors
    L, ph = LAYOUTS[layout], PLACEHOLDERS.get(ui, PLACEHOLDERS["en"])
    font, accent = L["font"], L["accent"]
    width = 9638  # A4 with 2 cm margins left and right, in twips
    photo, photo_data = _load_photo(root, p, L, 3.6)

    big_name = _p(p["name"].split(" ")[0], 0, font=font, size=26, color=accent) + _p(" ".join(p["name"].split(" ")[1:]) or " ", 160, font=font, size=26, bold=True, color=accent)
    sender = "".join(_p(z, 0, font=font, size=9) for z in _contact(p))  # the last line is the e-mail: anchor
    recipient = "".join(_p(z, 0, font=font, size=10, bold=(i == 0)) for i, z in enumerate(ph["recipient"]))
    if photo:
        # row 1: name and photo; row 2: sender (with the e-mail anchor) and the recipient to its right
        header = _table([_cell(4819, big_name) + _cell(4819, photo),
                         _cell(4819, sender, border_bottom=accent) + _cell(4819, recipient, border_bottom=accent)], [4819, 4819])
    else:
        header = _table([_cell(4819, big_name + sender, border_bottom=accent) + _cell(4819, recipient, border_bottom=accent)], [4819, 4819])
    date_line = _p(f"{p.get('town') or ph['town']}, {ph['date']}", 360, font=font, size=10)
    letter = "".join([_p(ph["subject"], 200, font=font, size=11, bold=True),
                      _p(ph["salutation"], 200, font=font, size=11),
                      _p(ph["body"], 200, font=font, size=11),
                      _p(ph["closing_ss"] if p.get("swiss_ss") else ph["closing"], 600, font=font, size=11),
                      _p(p["name"], 0, font=font, size=11)])
    gap = '<w:p><w:pPr><w:spacing w:after="360"/></w:pPr></w:p>'
    # date and letter as two rows of the SAME table: finalize.py finds the date as the single-cell row right above the letter
    text = _table([_cell(width, date_line, top=True), _cell(width, letter, top=True)], [width])
    return _docx(root, Path(target) if target else data_layout.get(root).templates / f"{ph['file_cl']}_{layout}.docx", header + gap + text, photo_data)


# ---------- CV from the CV content (profile/cv.md) ----------
# Structure of the Markdown file (the AI creates it in the Profile tab, it can be edited there):
#   # Title under the name           (optional, e.g. "Software Engineer")
#   short profile as free text       (optional)
#   ## Section                       (education, work experience, projects, skills, languages ...)
#   ### Period | Title | Place        (entry; place optional)
#   - Point                          (under an entry or directly in a section)
#   - **Label:** value               (line with a bold label, e.g. skills)

def read_cv(md):
    """lebenslauf.md -> {"title", "short", "sections": [{"title", "entries": [{"period", "title", "place", "points"}], "points"}]}."""
    cv = {"title": "", "short": [], "sections": []}
    section = entry = None
    for line in md.splitlines():
        z = line.strip()
        if not z or z.startswith("<!--"):
            continue
        if z.startswith("### ") and section is not None:
            parts = [t.strip() for t in z[4:].split("|")]
            if len(parts) == 1:
                parts = ["", parts[0]]
            entry = {"period": parts[0], "title": parts[1], "place": " | ".join(parts[2:]), "points": []}
            section["entries"].append(entry)
        elif z.startswith("## "):
            section, entry = {"title": z[3:].strip(), "entries": [], "points": []}, None
            cv["sections"].append(section)
        elif z.startswith("# ") and not cv["sections"]:
            cv["title"] = z[2:].strip()
        elif re.match(r"[-*] ", z):
            owner = entry or section
            (owner["points"] if owner else cv["short"]).append(z[2:].strip())
        elif section is None:
            cv["short"].append(z)
        else:
            (entry or section)["points"].append(z)
    return cv


def _runs(text, **fmt):
    """Splits text with **bold** into several runs."""
    parts = re.split(r"\*\*(.+?)\*\*", text)
    return "".join(_run(t, **{**fmt, "bold": fmt.get("bold") or i % 2 == 1}) for i, t in enumerate(parts) if t)


def _para(content, after=0, before=0, indent=None, border=None, align=None):
    ppr = (f'<w:pPr>' + (f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="2" w:color="{border}"/></w:pBdr>' if border else "")
           + f'<w:spacing w:before="{before}" w:after="{after}" w:line="264" w:lineRule="auto"/>'
           + (f'<w:ind w:left="{indent}" w:hanging="200"/>' if indent else "") + (f'<w:jc w:val="{align}"/>' if align else "") + "</w:pPr>")
    return f"<w:p>{ppr}{content}</w:p>"


def cv(root, layout="modern", target=None, ui="de"):
    """Generates a CV docx from the CV content (profile/cv.md, path in the profile under files.cv)."""
    p = userprofile.load(root)
    finalize.set_profile(p)
    md = userprofile.text(root, p, "cv")
    if not md.strip():
        raise ValueError(f"{p['files']['cv']} is missing or empty: create it first in the Profile tab under Profile text")
    data = read_cv(md)
    L, ph = LAYOUTS[layout], PLACEHOLDERS.get(ui, PLACEHOLDERS["en"])
    font, accent = L["font"], L["accent"]
    photo, photo_data = _load_photo(root, p, L, 3.4)
    # name as ONE paragraph, directly followed by the title line: that is how finalize.py recognises the CV (build_cv)
    title = data["title"] + (f", {p['town']}" if data["title"] and p.get("town") else "")
    head_text = (_para(_run(p["name"], font=font, size=24, bold=True, color=accent), 40)
                 + _para(_run(title or " ", font=font, size=11, color="555555"), 100)
                 + _para(_run("  ·  ".join(_contact(p)), font=font, size=9, color="555555"), 0))
    head = _table([_cell(7238, head_text) + _cell(2400, photo)], [7238, 2400]) if photo else head_text
    parts = [head]
    if data["short"]:
        parts.append(_para(_runs(" ".join(data["short"]), font=font, size=10), 0, before=200))
    for s in data["sections"]:
        parts.append(_para(_run(s["title"].upper(), font=font, size=11, bold=True, color=accent), 100, before=280, border=accent))
        for point in s["points"]:
            parts.append(_para(_runs(point, font=font, size=10), 40))
        rows = []
        for e in s["entries"]:
            right = _para(_runs(e["title"], font=font, size=10, bold=True) + (_run("  " + e["place"], font=font, size=9, color="555555") if e["place"] else ""), 30)
            right += "".join(_para(_run("•", font=font, size=10) + _run("\t", font=font) + _runs(x, font=font, size=10), 20, indent=200) for x in e["points"])
            spacer = '<w:p><w:pPr><w:spacing w:after="0" w:line="140" w:lineRule="exact"/></w:pPr></w:p>'  # small gap to the next entry
            rows.append(_cell(1900, _para(_run(e["period"], font=font, size=9, color="555555"), 0), top=True) + _cell(7738, right + spacer, top=True))
        if rows:
            parts.append(_table(rows, [1900, 7738]))
    return _docx(root, Path(target) if target else data_layout.get(root).templates / f"{ph['file_cv']}_{layout}.docx", "".join(parts), photo_data)


def check(root, rel, kind, ui="de"):
    """Does the docx work as a template? Returns {"ok": bool, "points": [(ok, sentence), ...]}."""
    p = userprofile.load(root)
    points = []
    f = allowed(root, rel)
    try:
        finalize.set_profile(p)
        dom = parseString(zipfile.ZipFile(f).read("word/document.xml"))
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        return {"ok": False, "points": [(False, str(e))]}
    if kind == "cover_letter":
        try:
            c = finalize.find_cells(dom)
            points.append((True, _m(ui, "sender", email=p["email"])))
            n = len(finalize.direct_children(c["body_cell"], "p"))
            ok = n >= (4 if c["date_cell"] is not None else 5)
            points.append((ok, _m(ui, "body", n=n, name=p["name"]) + ("" if ok else _m(ui, "few"))))
            points.append((True, _m(ui, "date_row" if c["date_cell"] is not None else "date_first")))
        except ValueError as e:
            points.append((False, str(e)))
    else:
        texts = [finalize.paragraph_text(x).strip() for x in finalize.find_elements(dom.documentElement, "p")]
        found = p["name"] in texts
        points.append((found, _m(ui, "name_found" if found else "name_missing", name=p["name"])))
    return {"ok": all(ok for ok, _ in points), "points": points}


def allowed(root, rel):
    """Only docx files in the templates folder or directly in a company folder of the applications."""
    f = (Path(root) / rel).resolve()
    lay = data_layout.get(root)
    own, apps = lay.templates.resolve(), lay.apps.resolve()
    if f.suffix.lower() != ".docx" or not f.is_file() or not (f.parent == own or f.parent.parent == apps):
        raise ValueError("Template not allowed")
    return f


def upload(root, kind, name, data, ui="de"):
    """Stores an own docx in the templates folder (never overwriting). Returns its relative path."""
    if kind not in ("cover_letter", "cv"):
        raise ValueError("unknown kind")
    try:
        zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("Not a valid Word file (.docx)")
    ph = PLACEHOLDERS.get(ui, PLACEHOLDERS["en"])
    stem = re.sub(r"[^\w-]+", "_", Path(name).stem).strip("_")[:50] or "Template"
    d = data_layout.get(root).templates
    d.mkdir(parents=True, exist_ok=True)
    base = f"{ph['file_cl'] if kind == 'cover_letter' else ph['file_cv']}_{stem}"
    p, n = d / f"{base}.docx", 2
    while p.exists():
        p, n = d / f"{base}_{n}.docx", n + 1
    p.write_bytes(data)
    return p.relative_to(root).as_posix()


def preview(root, rel):
    """PDF in the preview folder next to the templates (regenerated only when the docx is newer). Returns the relative path."""
    f = allowed(root, rel)
    lay = data_layout.get(root)
    target = lay.templates / lay.preview
    pdf = target / (re.sub(r"[^\w.-]+", "_", rel) + ".pdf")
    if not pdf.is_file() or pdf.stat().st_mtime < f.stat().st_mtime:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            created = finalize.convert_to_pdf(f, Path(tmp))
            pdf.write_bytes(Path(created).read_bytes())
    return pdf.relative_to(root).as_posix()


def listing(root):
    """Template candidates: everything in the templates folder plus earlier files matching the file name pattern."""
    p = userprofile.load(root)
    lay = data_layout.get(root)
    out = []
    for f in sorted(lay.templates.glob("*.docx")) if lay.templates.is_dir() else []:
        out.append({"rel": f.relative_to(root).as_posix(), "kind": "cv" if f.name.lower().startswith(("lebenslauf", "cv")) else "cover_letter", "source": "templates"})
    for kind in ("cover_letter", "cv"):
        for f in sorted(lay.apps.glob(f"*/{userprofile.prefix(p, kind)}*.docx"), key=lambda x: x.stat().st_mtime, reverse=True)[:6]:
            out.append({"rel": f.relative_to(root).as_posix(), "kind": kind, "source": "application"})
    return out


if __name__ == "__main__":  # self-test: generate, check, fill with finalize
    import json
    r = Path(tempfile.mkdtemp())
    (r / "applications" / "Testcompany").mkdir(parents=True)
    (r / "pf.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 300, 400) + b"\x08\x02\x00\x00\x00" + b"\x00" * 40)
    (r / "application-board.json").write_text(json.dumps({"name": "Zoe Muster", "town": "Bern", "email": "zoe@example.com", "phone": "+41 31 000 00 00",
                                                          "address": "Weg 1\n3000 Bern", "photo": "pf.png", "swiss_ss": True}), "utf-8")
    assert image_size((r / "pf.png").read_bytes()) == (300, 400)
    for ui in ("de", "en"):
        for lay in LAYOUTS:
            rel = cover_letter(r, lay, ui=ui)
            res = check(r, rel, "cover_letter", ui)
            assert res["ok"], (lay, res)
            md = r / "applications" / "Testcompany" / "letter_Test.md"
            md.write_text("# Infos zur Jobausschreibung\n**Titel:** Dev\n\n# Bewerbungsschreiben\n\nZoe Muster\nWeg 1\n\nACME AG\nz. Hd. Herr X\nStrasse 2\n8000 Zuerich\n\n"
                          "Bern, 9. November 2026\n\n**Bewerbung als Dev**\n\nSehr geehrter Herr X\n\nAbsatz eins.\n\nAbsatz zwei.\n\nFreundliche Gruesse\nZoe Muster\n", "utf-8")
            letter = finalize.parse_letter_markdown(md.read_text("utf-8"))
            out = r / f"out_{lay}.docx"
            with tempfile.TemporaryDirectory() as tmp:
                finalize.build_docx(r / rel, letter, Path(tmp), out)
            text = re.sub(r"<[^>]+>", " ", zipfile.ZipFile(out).read("word/document.xml").decode())
            for want in ("ACME AG", "Bern, 9. November 2026", "Bewerbung als Dev", "Absatz zwei.", "zoe@example.com"):
                assert want in text, (lay, want)
            ph = PLACEHOLDERS[ui]
            assert ph["body"] not in text and ph["recipient"][0] not in text and ph["date"] not in text, (ui, lay)
            assert any(ok and s == _m(ui, "date_row") for ok, s in res["points"]), (lay, res)
    assert Path(cover_letter(r, "modern", ui="en")).name == "CoverLetter_modern.docx"
    (r / "profile").mkdir(exist_ok=True)
    (r / "profile" / "cv.md").write_text("# Software Engineer\nShort profile with **bold**.\n\n## Experience\n### 2024 to date | Developer | ACME AG, Bern\n- Built **X**\n- Y\n\n"
                                                 "## Education\n### 2020 to 2024 | BSc Computer Science | Uni Bern\n\n## Skills\n- **Languages:** Python, C#\n", "utf-8")
    data = read_cv((r / "profile" / "cv.md").read_text("utf-8"))
    assert data["title"] == "Software Engineer" and len(data["sections"]) == 3 and data["sections"][0]["entries"][0]["place"] == "ACME AG, Bern", data
    assert data["sections"][2]["points"] == ["**Languages:** Python, C#"] and data["short"] == ["Short profile with **bold**."], data
    for lay in LAYOUTS:
        rel = cv(r, lay)
        assert check(r, rel, "cv")["ok"], lay
        out = r / f"cv_{lay}.docx"
        finalize.ANCHOR_SIGNATURE, finalize.TOWN = "Zoe Muster", "Bern"
        with tempfile.TemporaryDirectory() as tmp:
            finalize.build_cv(r / rel, "Data Engineer", Path(tmp), out)
        text = re.sub(r"<[^>]+>", " ", zipfile.ZipFile(out).read("word/document.xml").decode())
        assert "Data Engineer, Bern" in text and "Built" in text and "Uni Bern" in text, lay
    print("ok")
