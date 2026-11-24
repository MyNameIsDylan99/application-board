#!/usr/bin/env python3
"""Pour a Bewerbungsschreiben_*.md into a cover letter and a CV (docx and pdf).

Takes the finished letter from the section "# Bewerbungsschreiben" (or "# Cover Letter") of a
Bewerbungsschreiben*.md and fills it into the person's existing Word template (photo header, address table,
body text) by reusing an earlier cover letter docx through XML text replacement. No new layout, no new
formatting, only the text is exchanged.

Name, e-mail, town and the file name pattern come from the profile in the data folder
(<data folder>/bewerbungs-board.json, see userprofile.py), not from the code.

Why XML instead of a docx generator: the template contains a photo, a multi-column header table and
paragraph styles that cannot be rebuilt from Markdown. Reusing an existing file guarantees exactly the same
layout as all earlier applications.

The places to fill are not found through table or paragraph indices (those shift slightly between
templates) but through two anchor texts from the profile that are the same in every template:
  - the e-mail address marks the cell with the sender details. The cell right after it in the same row is
    the recipient cell (company, attn., position, address; the number of lines varies per letter).
  - the name as the last paragraph of a cell with more than one paragraph marks the letter cell. Its order
    is: salutation, N body paragraphs, closing line, name line. In every template seen so far the date is
    NOT in this cell but in its own single-cell row directly above (same table, one row earlier). The script
    looks for that row structurally; if there is none, it uses the first paragraph of the letter cell.

Both cells are brought to the number of paragraphs actually needed (paragraphs are cloned or removed) before
the text goes in. That way it works whether the new letter has more or fewer paragraphs than the template.

Also creates the CV: an existing CV docx is copied under the new name, unchanged except for the job title line
in the header (if --cv-title is given). Which projects and skills the CV shows is NOT decided here; that stays
deliberate manual curation.

Usage:
    python finalize.py "<company folder>" --company-short <ShortName> [--mmyy 0926]
        [--cl-template <path>] [--cv-template <path>] [--cv-title "<title>"]
        [--skip-cv] [--out-dir <folder>] [--repo-root <data folder>] [--md <file>]
    (the German options --firma-kurz and --cv-titel still work)

Example:
    python finalize.py "Muster AG" --company-short Muster --cv-title "IT Supporter"

Reads Bewerbungen/<company folder>/Bewerbungsschreiben*.md and writes Bewerbungen/<company folder>/
<cover letter pattern>.docx/.pdf and, unless --skip-cv, <CV pattern>.docx/.pdf (patterns from the profile,
default CL_<name>_<company>_<MMYY>).

ALWAYS look at both PDFs after a run before calling the result done. The script only checks that the text
replacement worked technically, not how it looks.
"""

from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from xml.dom.minidom import parse as parse_xml
import zipfile

WORDML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402
import userprofile  # noqa: E402

# Anchors and town from the profile, set by set_profile() (main() or the calling module)
ANCHOR_EMAIL = ""
ANCHOR_SIGNATURE = ""
TOWN = ""
# Heading of the letter section in a Bewerbungsschreiben*.md; German and English files are both understood
LETTER_MARKERS = ("# Bewerbungsschreiben", "# Cover Letter")


def letter_start(md_text: str) -> int:
    """Index of the letter heading (the last one, if several), or -1."""
    return max(md_text.rfind(m) for m in LETTER_MARKERS)


def set_profile(p: dict) -> None:
    global ANCHOR_EMAIL, ANCHOR_SIGNATURE, TOWN
    if not p.get("name") or not p.get("email"):
        raise ValueError(f"Profile incomplete: enter name and email in {userprofile.FILE} in the data folder "
                         "(both appear exactly like this in the Word template and serve as anchors)")
    ANCHOR_EMAIL, ANCHOR_SIGNATURE, TOWN = p["email"], p["name"], p.get("ort", "")


def w(tag: str) -> str:
    return f"w:{tag}"


def find_elements(root, tag: str) -> list:
    return list(root.getElementsByTagNameNS(WORDML_NS, tag))


def direct_children(node, tag: str) -> list:
    return [
        c
        for c in node.childNodes
        if c.nodeType == c.ELEMENT_NODE and c.localName == tag
    ]


def paragraph_text(p) -> str:
    return "".join(
        t.firstChild.data
        for t in find_elements(p, "t")
        if t.firstChild is not None
    )


def set_paragraph_text(p, text: str) -> None:
    """Replace the text of a paragraph's runs. Assumes the paragraph carries its
    visible text in a single run (true for every template seen so far); if there
    are several, the first keeps the new text and the rest are emptied so no
    formatting is lost and no stray text survives."""
    runs = find_elements(p, "r")
    t_nodes = []
    for r in runs:
        t_nodes.extend(direct_children(r, "t"))
    if not t_nodes:
        raise ValueError("Paragraph without <w:t>, cannot set text: " + p.toxml()[:200])
    first, *rest = t_nodes
    if first.firstChild is not None:
        first.firstChild.data = text
    else:
        first.appendChild(first.ownerDocument.createTextNode(text))
    if text:
        first.setAttribute("xml:space", "preserve")
    for extra in rest:
        if extra.firstChild is not None:
            extra.firstChild.data = ""


def remove_bold(p) -> None:
    """Remove bold from all runs of a paragraph (w:b and w:bCs in rPr)."""
    for rpr in find_elements(p, "rPr"):
        for tag in ("b", "bCs"):
            for el in direct_children(rpr, tag):
                rpr.removeChild(el)


def set_paragraph_list(cell, texts: list[str]) -> None:
    """Bring a table cell's paragraphs to len(texts) by cloning the last
    paragraph (for growth) or dropping trailing ones (for shrink), then set
    the text of each. The first paragraph's own formatting (e.g. bold company
    name) is preserved because it is only ever touched for its text, never
    replaced or removed, as long as at least one line is requested."""
    paras = direct_children(cell, "p")
    if not paras:
        raise ValueError("Cell without paragraphs, cannot insert a list of texts")
    if not texts:
        raise ValueError("Empty list of texts not allowed, at least one line needed")

    while len(paras) < len(texts):
        clone = paras[-1].cloneNode(deep=True)
        paras[-1].parentNode.insertBefore(clone, paras[-1].nextSibling)
        paras.append(clone)
    while len(paras) > len(texts):
        doomed = paras.pop()
        doomed.parentNode.removeChild(doomed)

    for p, text in zip(paras, texts):
        set_paragraph_text(p, text)


def previous_element_sibling(node):
    sib = node.previousSibling
    while sib is not None and sib.nodeType != sib.ELEMENT_NODE:
        sib = sib.previousSibling
    return sib


def find_cells(dom) -> dict:
    """Finds the places that finalizing touches through fixed anchors instead of table or paragraph
    indices (see the module docstring for why).

    date_cell is optional: some templates have the date as its own row directly above the letter cell
    (every template seen so far), others might keep it as the first paragraph of the letter cell. Both are
    supported so that a wrong assumption never again leaves an old date behind, as happened in the first
    version of this script.
    """
    cells = find_elements(dom.documentElement, "tc")
    sender_cell = None
    body_cell = None
    for cell in cells:
        paras = direct_children(cell, "p")
        texts = [paragraph_text(p) for p in paras]
        if any(ANCHOR_EMAIL in t for t in texts):
            sender_cell = cell
        if len(texts) > 1 and texts[-1].strip() == ANCHOR_SIGNATURE:
            body_cell = cell
    if sender_cell is None:
        raise ValueError(f"Sender cell not found (anchor {ANCHOR_EMAIL!r})")
    if body_cell is None:
        raise ValueError(f"Letter cell not found (anchor {ANCHOR_SIGNATURE!r} as last paragraph)")

    row = sender_cell.parentNode
    row_cells = direct_children(row, "tc")
    idx = row_cells.index(sender_cell)
    if idx + 1 >= len(row_cells):
        raise ValueError("No table cell after the sender cell")
    recipient_cell = row_cells[idx + 1]

    date_cell = None
    body_row = body_cell.parentNode
    prev_row = previous_element_sibling(body_row)
    if prev_row is not None and prev_row.localName == "tr":
        prev_cells = direct_children(prev_row, "tc")
        if len(prev_cells) == 1:
            prev_paras = direct_children(prev_cells[0], "p")
            if len(prev_paras) == 1:
                date_cell = prev_cells[0]

    return {
        "recipient_cell": recipient_cell,
        "body_cell": body_cell,
        "date_cell": date_cell,
    }


SUBJECT_RE = re.compile(r"^(Bewerbung|Application)\b")


def parse_letter_markdown(md_text: str) -> dict:
    """Splits the letter section ("# Bewerbungsschreiben" or "# Cover Letter") into recipient block,
    date, optional bold subject line ("**Bewerbung als ...**"), salutation, body paragraphs and closing."""
    idx = letter_start(md_text)
    if idx == -1:
        raise ValueError(f"Section {' or '.join(repr(m) for m in LETTER_MARKERS)} not found in the Markdown file")
    letter = md_text[md_text.index("\n", idx) if "\n" in md_text[idx:] else len(md_text):].strip()

    blocks = [b.strip() for b in re.split(r"\n\s*\n", letter) if b.strip()]
    if len(blocks) < 6:
        raise ValueError(
            f"Only {len(blocks)} blocks found in the letter; expected at least sender, recipient, date, "
            "salutation, one paragraph and closing. Check the file."
        )

    i = 1  # block 0 = sender block, always identical to the template, left alone
    recipient_lines = blocks[i].split("\n")
    i += 1
    date_line = blocks[i].strip()
    i += 1
    subject = ""
    if blocks[i].startswith("**") and blocks[i].endswith("**"):
        subject = blocks[i].strip("*").strip()  # subject line, set in bold before the salutation
        i += 1
    salutation = blocks[i].strip()
    i += 1

    signoff_block = blocks[-1].split("\n")
    if len(signoff_block) < 2:
        raise ValueError(
            'The last block must contain the closing and the name, '
            f"found: {blocks[-1]!r}"
        )
    signoff_phrase = signoff_block[0].strip()

    body_paragraphs = [b.strip() for b in blocks[i:-1]]
    if not body_paragraphs:
        raise ValueError("No body paragraphs between salutation and closing")

    return {
        "recipient_lines": [l.strip() for l in recipient_lines if l.strip()],
        "date_line": date_line,
        "subject": subject,
        "salutation": salutation,
        "body_paragraphs": body_paragraphs,
        "signoff_phrase": signoff_phrase,
    }


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    for info in zf.infolist():
        target = dest / info.filename
        if not str(target.resolve()).startswith(str(dest.resolve())):
            raise ValueError(f"Unsafe zip entry: {info.filename}")
    zf.extractall(dest)


def rezip(src_dir: Path, out_path: Path) -> None:
    if out_path.exists():
        out_path.unlink()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir))


def latest_template(repo_root: Path, exclude_dir: Path, prefix: str) -> Path:
    candidates = [
        Path(p)
        for p in glob.glob(str(repo_root / "Bewerbungen" / "*" / f"{prefix}*.docx"))
        if Path(p).parent.resolve() != exclude_dir.resolve()
    ]
    if not candidates:
        raise ValueError(f"No existing {prefix}*.docx found as template")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_cv(template: Path, new_title: str | None, work_dir: Path, out_docx: Path) -> None:
    """Copies an existing CV docx unchanged except for the job title line under the name
    ("<title>, <town>") if new_title is given. Everything else (projects, skills, order) stays as in the
    template on purpose; real curation remains a manual step.

    The title line is not found by its content (that is what changes) but structurally: it is always the
    paragraph right after a paragraph containing the name from the profile. The name paragraph can appear
    several times (header repeated per page), so EVERY occurrence is updated.
    """
    unpacked = work_dir / "unpacked"
    unpacked.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(template) as zf:
        safe_extract(zf, unpacked)

    if new_title is not None:
        doc_xml_path = unpacked / "word" / "document.xml"
        dom = parse_xml(str(doc_xml_path))
        paras = find_elements(dom.documentElement, "p")
        name_indices = [i for i, p in enumerate(paras) if paragraph_text(p).strip() == ANCHOR_SIGNATURE]
        if not name_indices:
            raise ValueError(f'No paragraph with the text "{ANCHOR_SIGNATURE}" found in the CV')
        new_title_line = f"{new_title}, {TOWN}" if TOWN else new_title
        for i in name_indices:
            if i + 1 >= len(paras):
                continue
            set_paragraph_text(paras[i + 1], new_title_line)
        doc_xml_path.write_bytes(dom.toxml(encoding="UTF-8"))

    rezip(unpacked, out_docx)


def build_docx(template: Path, letter: dict, work_dir: Path, out_docx: Path) -> None:
    unpacked = work_dir / "unpacked"
    unpacked.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(template) as zf:
        safe_extract(zf, unpacked)

    doc_xml_path = unpacked / "word" / "document.xml"
    dom = parse_xml(str(doc_xml_path))

    cells = find_cells(dom)
    recipient_cell, body_cell, date_cell = cells["recipient_cell"], cells["body_cell"], cells["date_cell"]

    set_paragraph_list(recipient_cell, letter["recipient_lines"])

    body_paras = direct_children(body_cell, "p")
    min_paras = 4 if date_cell is not None else 5
    if len(body_paras) < min_paras:
        raise ValueError(
            f"Letter cell has only {len(body_paras)} paragraphs, expected "
            f"at least {min_paras}"
        )

    if date_cell is not None:
        date_para = direct_children(date_cell, "p")[0]
        set_paragraph_text(date_para, letter["date_line"])
        salutation_idx = 0
    else:
        # No date row found: the date is the first paragraph of the letter cell
        # (not the case in any template seen so far, but supported).
        set_paragraph_text(body_paras[0], letter["date_line"])
        salutation_idx = 1

    # An older template may already have a subject line before the salutation
    subject_para = None
    if SUBJECT_RE.match(paragraph_text(body_paras[salutation_idx])):
        subject_para = body_paras[salutation_idx]
        salutation_idx += 1
        if len(body_paras) < min_paras + 1:
            raise ValueError("Letter cell has too few paragraphs for subject and salutation")

    salutation_para = body_paras[salutation_idx]
    set_paragraph_text(salutation_para, letter["salutation"])

    # Body paragraphs sit between the salutation and the last two paragraphs (closing, name).
    # These paragraph nodes are resized in place so the order stays the same.
    first_body_idx = salutation_idx + 1
    last_body_idx = len(body_paras) - 3  # last body paragraph, inclusive
    anchor_after = body_paras[last_body_idx + 1]  # closing paragraph, the fixed point
    parent = anchor_after.parentNode

    current = body_paras[first_body_idx:last_body_idx + 1]
    target_texts = letter["body_paragraphs"]

    while len(current) < len(target_texts):
        clone = current[-1].cloneNode(deep=True)
        parent.insertBefore(clone, anchor_after)
        current.append(clone)
    while len(current) > len(target_texts):
        doomed = current.pop()
        parent.removeChild(doomed)

    for p, text in zip(current, target_texts):
        set_paragraph_text(p, text)

    set_paragraph_text(anchor_after, letter["signoff_phrase"])

    # Subject: bold like the salutation, so clone that paragraph and insert it before
    if letter.get("subject"):
        if subject_para is None:
            subject_para = salutation_para.cloneNode(deep=True)
            salutation_para.parentNode.insertBefore(subject_para, salutation_para)
        set_paragraph_text(subject_para, letter["subject"])
        remove_bold(salutation_para)  # only the subject is bold, otherwise the salutation looks like a second subject
    elif subject_para is not None:
        subject_para.parentNode.removeChild(subject_para)
    # The name line (last paragraph) stays unchanged; it is always the name from the profile

    doc_xml_path.write_bytes(dom.toxml(encoding="UTF-8"))
    rezip(unpacked, out_docx)


def convert_to_pdf(docx_path: Path, out_dir: Path) -> Path:
    # own profile, otherwise soffice does nothing while LibreOffice is already open
    soffice = storage.tool("soffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) not found; install it or set APPLICATION_BOARD_SOFFICE to its path")
    with tempfile.TemporaryDirectory(prefix="finalize_lo_") as profile:
        subprocess.run(
            [soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
            check=True,
            capture_output=True,
        )
    pdf_path = out_dir / (docx_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"PDF export failed, expected: {pdf_path}")
    return pdf_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company_folder", help='folder name under Bewerbungen/, e.g. "Muster AG"')
    ap.add_argument("--company-short", "--firma-kurz", dest="company_short", required=True, help="short company name for the file name, e.g. Muster")
    ap.add_argument("--mmyy", default=date.today().strftime("%m%y"), help="MMYY for the file name, default today")
    ap.add_argument("--cl-template", help="path to an existing cover letter docx as template, default: the most recently changed one in the data folder")
    ap.add_argument("--cv-template", help="path to an existing CV docx as template, default: the most recently changed one in the data folder")
    ap.add_argument("--cv-title", "--cv-titel", dest="cv_title", help='new job title line for the CV header, e.g. "IT Supporter"; without it the title line of the template stays')
    ap.add_argument("--skip-cv", action="store_true", help="only create the cover letter, no CV")
    ap.add_argument("--repo-root", default=".", help="path to the data folder, default the current directory")
    ap.add_argument("--out-dir", help="target folder, default Bewerbungen/<company_folder>/")
    ap.add_argument("--md", help="a specific Bewerbungsschreiben*.md in the company folder (file name), needed when there are several")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    prof = userprofile.load(root)
    try:
        set_profile(prof)
    except ValueError as e:
        sys.exit(str(e))
    company_dir = root / "Bewerbungen" / args.company_folder
    md_candidates = sorted(company_dir.glob("Bewerbungsschreiben*.md"))
    if not md_candidates:
        sys.exit(f"No Bewerbungsschreiben*.md found in {company_dir}")
    md_path = company_dir / args.md if args.md else md_candidates[0]
    if md_path not in md_candidates:
        sys.exit(f"{args.md} is not a Bewerbungsschreiben*.md in {company_dir}")

    letter = parse_letter_markdown(md_path.read_text(encoding="utf-8"))
    cl_template = Path(args.cl_template).resolve() if args.cl_template else latest_template(root, company_dir, userprofile.prefix(prof, "anschreiben"))
    out_dir = Path(args.out_dir).resolve() if args.out_dir else company_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_cl_docx = out_dir / (userprofile.file_name(prof, "anschreiben", args.company_short, args.mmyy) + ".docx")

    with tempfile.TemporaryDirectory(prefix="finalize_cl_") as tmp:
        build_docx(cl_template, letter, Path(tmp), out_cl_docx)
    cl_pdf = convert_to_pdf(out_cl_docx, out_dir)
    print(f"CL template: {cl_template}")
    print(f"CL docx:     {out_cl_docx}")
    print(f"CL pdf:      {cl_pdf}")

    if not args.skip_cv:
        cv_template = Path(args.cv_template).resolve() if args.cv_template else latest_template(root, company_dir, userprofile.prefix(prof, "lebenslauf"))
        out_cv_docx = out_dir / (userprofile.file_name(prof, "lebenslauf", args.company_short, args.mmyy) + ".docx")
        with tempfile.TemporaryDirectory(prefix="finalize_cv_") as tmp:
            build_cv(cv_template, args.cv_title, Path(tmp), out_cv_docx)
        cv_pdf = convert_to_pdf(out_cv_docx, out_dir)
        print(f"CV template: {cv_template}")
        print(f"CV docx:     {out_cv_docx}")
        print(f"CV pdf:      {cv_pdf}")
        if args.cv_title is None:
            print("Note: --cv-title not given, the title line in the CV header was taken over from the template unchanged.")

    print("Now render both PDFs and check them visually before calling this done.")


if __name__ == "__main__":
    main()
