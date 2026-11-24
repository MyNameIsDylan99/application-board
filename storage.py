"""Small file helpers shared by the modules: config folder, atomic writes, backups and versions of a file.

Settings and keys live outside the app folder so the app can be installed read only:
BEWERBUNGS_BOARD_CONFIG, otherwise %APPDATA%\\bewerbungs-board (Windows), ~/Library/Application Support/bewerbungs-board
(macOS) or $XDG_CONFIG_HOME/bewerbungs-board (default ~/.config/bewerbungs-board). Files from the old place next to
the code are moved there once.
"""
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BACKUP_NAME = re.compile(r"^(\d{8})_(\d{6})_\d+_(.+)$")


def config_dir():
    if os.environ.get("BEWERBUNGS_BOARD_CONFIG"):
        d = Path(os.environ["BEWERBUNGS_BOARD_CONFIG"]).expanduser()
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "bewerbungs-board"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "bewerbungs-board"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "bewerbungs-board"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file(name):
    """Path of a config file; an old copy next to the code is moved over once (keeps its 0600 mode). Only into the
    default folder: with BEWERBUNGS_BOARD_CONFIG (tests, a second setup) the files next to the code stay untouched."""
    p, old = config_dir() / name, APP_DIR / name
    if not os.environ.get("BEWERBUNGS_BOARD_CONFIG") and old.is_file() and not p.exists() and old.resolve() != p.resolve():
        shutil.move(str(old), str(p))
        print(f"{name} moved to {p}")
    return p


TOOL_PLACES = {  # where installers put the tools when they are not on PATH (Windows and macOS)
    "soffice": ["C:/Program Files/LibreOffice/program/soffice.exe", "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
                "/Applications/LibreOffice.app/Contents/MacOS/soffice"],
    "pdftotext": ["C:/Program Files/poppler/Library/bin/pdftotext.exe", "/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext"],
    "pdfinfo": ["C:/Program Files/poppler/Library/bin/pdfinfo.exe", "/opt/homebrew/bin/pdfinfo", "/usr/local/bin/pdfinfo"],
}


def tool(name):
    """Full path of a helper program (soffice, pdftotext, pdfinfo) or None. The env variable BEWERBUNGS_<NAME> wins."""
    own = os.environ.get("BEWERBUNGS_" + name.upper())
    if own:
        return own
    found = shutil.which(name) or (shutil.which("libreoffice") if name == "soffice" else None)
    return found or next((p for p in TOOL_PLACES.get(name, []) if Path(p).is_file()), None)


def write_atomic(p, text):
    """Write text so that a crash leaves either the old or the new file, never a half one."""
    p = Path(p)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, p)


def backup(apps, p, label=None):
    """Copy p to Bewerbungen/_Review/backups/<time>_<company>_<file>; returns the backup path."""
    d = Path(apps) / "_Review" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    b = d / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{label or Path(p).parent.name + '_' + Path(p).name}"
    shutil.copy2(p, b)
    return b


def versions(apps, p):
    """Backups of one letter, newest first: [{"name", "ts", "size"}]."""
    d = Path(apps) / "_Review" / "backups"
    suffix = f"{Path(p).parent.name}_{Path(p).name}"
    out = []
    for f in d.glob("*.md") if d.is_dir() else []:
        m = BACKUP_NAME.match(f.name)
        if m and m.group(3) == suffix:
            ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat()
            out.append({"name": f.name, "ts": ts, "size": f.stat().st_size})
    return sorted(out, key=lambda v: v["name"], reverse=True)


def snapshot(root):
    """mtime of every file in the data folder (without .git and _Review), to see what an agent changed."""
    root = Path(root)
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "_Review", "__pycache__", ".textcache")]
        for f in files:
            p = Path(dirpath) / f
            try:
                out[p.relative_to(root).as_posix()] = p.stat().st_mtime_ns
            except OSError:
                pass
    return out


def changed_since(root, before, allowed=()):
    """Files that were added, changed or deleted since snapshot(), except the allowed ones."""
    after = snapshot(root)
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k) and k not in allowed)


def version_text(apps, p, name):
    if name not in {v["name"] for v in versions(apps, p)}:
        raise ValueError("Unknown version")
    return (Path(apps) / "_Review" / "backups" / name).read_text("utf-8")


if __name__ == "__main__":  # self-test in a temp folder
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    os.environ["BEWERBUNGS_BOARD_CONFIG"] = str(tmp / "cfg")
    assert config_dir() == tmp / "cfg" and config_dir().is_dir()
    letter = tmp / "Bewerbungen" / "Firma AG" / "Bewerbungsschreiben_X.md"
    letter.parent.mkdir(parents=True)
    write_atomic(letter, "one")
    b = backup(tmp / "Bewerbungen", letter)
    write_atomic(letter, "two")
    assert letter.read_text() == "two" and b.read_text() == "one" and not list(letter.parent.glob(".*tmp"))
    v = versions(tmp / "Bewerbungen", letter)
    assert len(v) == 1 and version_text(tmp / "Bewerbungen", letter, v[0]["name"]) == "one"
    try:
        version_text(tmp / "Bewerbungen", letter, "../../x.md")
        raise AssertionError("the name should have been rejected")
    except ValueError:
        pass
    snap = snapshot(tmp)
    (tmp / "Bewerbungen" / "other.md").write_text("x")
    write_atomic(letter, "three")
    assert changed_since(tmp, snap, {"Bewerbungen/Firma AG/Bewerbungsschreiben_X.md"}) == ["Bewerbungen/other.md"]
    print("ok")
