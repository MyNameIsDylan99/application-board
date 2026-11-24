"""Read mails directly from the local Thunderbird profile, without password and without export.

Thunderbird stores every folder (IMAP too, when "keep for offline use" is on, the default) as an mbox file in
the profile. New mails are appended at the end, so the tail of each recently changed folder file is enough.
From it an in-memory index (header and text of every mail) is built so the search answers while typing.
Changed folders are re-read on the next call. Read-only; Thunderbird may keep running.
"""
import configparser
import email
import gzip
import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

CHUNK = 64 * 1024 * 1024  # folders are read backwards in chunks until the mails are older than DAYS
CAP = 1024 * 1024 * 1024  # ponytail: at most 1 GB per folder on the first read (attachments make Sent folders huge)
DAYS = 365
TEXT_MAX = 20000  # characters of mail text per mail in the index
SKIP = re.compile(r"(?i)(trash|papierkorb|spam|junk|drafts|entw[uü]rfe|vorlagen|templates|outbox|postausgang|gel[oö]scht|deleted)")
CACHE = None  # Path of a gzip JSON file; set by the server so the index survives a restart (None: memory only)
INDEX = {}  # path -> {"stamp": (mtime, size), "mails": [...], "path": Path}
LOCK = threading.Lock()
_BUILT = threading.Event()  # set once the first index build finished (or there is no profile)
_WARM = threading.Lock()


def candidates():
    home = Path.home()
    c = [home / ".thunderbird", home / ".var/app/org.mozilla.Thunderbird/.thunderbird", home / "snap/thunderbird/common/.thunderbird",
         home / "Library/Thunderbird"]
    if os.environ.get("APPDATA"):
        c.insert(0, Path(os.environ["APPDATA"]) / "Thunderbird")
    return c


def profile():
    """Path of the default profile or None."""
    for base in candidates():
        ini = base / "profiles.ini"
        if not ini.is_file():
            continue
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(ini, encoding="utf-8")
        chosen = [cp[s]["Default"] for s in cp.sections() if s.startswith("Install") and cp[s].get("Default")]
        for s in cp.sections():
            if s.startswith("Profile") and cp[s].get("Path") and (cp[s].get("Path") in chosen or (not chosen and cp[s].get("Default") == "1")):
                p = cp[s]["Path"]
                p = base / p if cp[s].get("IsRelative", "1") == "1" else Path(p)
                if p.is_dir():
                    return p
    return None


def folders(prof, days=DAYS):
    """mbox files from ImapMail/ and Mail/ that changed in the last `days` days."""
    limit = time.time() - days * 86400
    for top in ("ImapMail", "Mail"):
        for f in (prof / top).rglob("*") if (prof / top).is_dir() else []:
            rel = f.relative_to(prof / top)
            if (f.is_file() and not f.suffix and f.stat().st_size and f.stat().st_mtime >= limit
                    and not any(SKIP.search(part) for part in rel.parts)):
                yield f, "/".join(p.removesuffix(".sbd") for p in rel.parts)


def _mails(f, start, end, boundary=False):
    """(separator offset, offset, length, bytes) of every complete mail in [start, end) of the file. Thunderbird
    separates mails with "From - <date>"; bytes.find is ~20x faster than a regex here. Before the first separator
    the piece is incomplete unless start is a known mail boundary (0 or the old end of the file)."""
    with open(f, "rb") as h:
        h.seek(start)
        data = h.read(end - start)
    pos = [0] if (start == 0 or boundary) and data.startswith(b"From - ") else []
    i = data.find(b"\nFrom - ")
    while i >= 0:
        pos.append(i + 1)
        i = data.find(b"\nFrom - ", i + 1)
    out = []
    for a, b in zip(pos, pos[1:] + [len(data)]):
        head_end = data.find(b"\n", a) + 1  # the "From - " line itself is not part of the mail
        out.append((start + a, start + head_end, b - head_end, data[head_end:b]))
    return out


def _fold(s):
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()


HEADER = re.compile(r"^(date|subject|from|to|message-id):[ \t]*(.*(?:\r?\n[ \t].*)*)", re.I | re.M)


def _headers(hdr):
    """Only the few headers the search needs; the full parser would be the bottleneck here."""
    d = {}
    for m in HEADER.finditer(hdr.decode("latin-1")):
        d.setdefault(m.group(1).lower(), re.sub(r"\r?\n[ \t]+", " ", m.group(2)).strip())
    return d


def _h(k, name):
    v = k.get(name, "") or ""
    try:
        if "=?" not in v:  # raw header read as latin-1, turn back into utf-8
            return v.encode("latin-1").decode("utf-8", "replace")
        return str(make_header(decode_header(v)))
    except (HeaderParseError, LookupError, UnicodeError):
        return v


def _text(raw):
    """All text parts except attachments, via compat32 (policy.default is ten times slower here)."""
    out = []
    for part in email.message_from_bytes(raw).walk():
        if part.get_content_maintype() != "text" or "attachment" in str(part.get("content-disposition", "")).lower():
            continue
        data = part.get_payload(decode=True) or b""
        try:
            out.append(data.decode(part.get_content_charset() or "utf-8", "replace"))
        except LookupError:
            out.append(data.decode("utf-8", "replace"))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", "\n".join(out)))


def _parse(raw, off, length, name, limit):
    """Index entry of one mail, or None when it is older than limit or has no date."""
    end = raw.find(b"\n\n")
    k = _headers(raw[:end if end >= 0 else 20000])
    try:
        dt = parsedate_to_datetime(k.get("date", ""))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if dt < limit:
        return None
    subject, sender, to = _h(k, "subject"), _h(k, "from"), _h(k, "to")
    local = dt.astimezone()
    return {"token": f"{off}:{length}:{name}", "ts": dt.timestamp(), "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
            "folder": name, "from": sender, "to": to, "subject": subject, "mid": (k.get("message-id") or "").strip(),
            "search": _fold(f"{subject} {sender} {to} {_text(raw)[:TEXT_MAX]}")}


def _read_folder(f, name, limit, start=0):
    """Mails newer than limit after offset start, read backwards in chunks from the end of the file until a
    whole chunk is older than limit (or CAP bytes were read)."""
    size = end = f.stat().st_size
    mails, chunk = [], CHUNK
    while end > start and size - end < CAP:
        lo = max(start, end - chunk)
        parts = _mails(f, lo, end, boundary=lo == start)
        if not parts:
            if lo == start:
                break
            chunk *= 2  # one mail bigger than the chunk (attachments)
            continue
        found = [m for m in (_parse(raw, off, n, name, limit) for _, off, n, raw in parts) if m]
        mails = found + mails
        if not found:
            break
        end, chunk = parts[0][0], CHUNK
    return mails


def _boundary(f, off):
    with open(f, "rb") as h:
        h.seek(off)
        return h.read(7) == b"From - "


def _load():
    """Index from CACHE (once, when the memory index is still empty)."""
    if INDEX or not CACHE:
        return
    try:
        with gzip.open(CACHE, "rt", encoding="utf-8") as h:
            data = json.load(h)
    except (OSError, ValueError, EOFError):
        return
    if data.get("days") == DAYS:
        INDEX.update({k: {**v, "stamp": tuple(v["stamp"]), "path": Path(k)} for k, v in data.get("folders", {}).items()})


def _save():
    if not CACHE:
        return
    tmp = CACHE.with_name(CACHE.name + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=1) as h:
            json.dump({"days": DAYS, "folders": {k: {"stamp": v["stamp"], "mails": v["mails"]} for k, v in INDEX.items()}}, h, ensure_ascii=False)
        os.chmod(tmp, 0o600)  # mail texts
        os.replace(tmp, CACHE)
    except OSError:
        pass


def index(prof=None):
    """All mails of the last DAYS, newest first, without duplicates. Changed folders are read again; when a
    folder only grew (new mails appended), only the new part is read. Saved to CACHE when something changed."""
    prof = prof or profile()
    if not prof:
        raise FileNotFoundError("No Thunderbird profile found")
    limit = datetime.now(timezone.utc) - timedelta(days=DAYS)
    with LOCK:
        _load()
        current, changed = {}, False
        for f, name in folders(prof):
            st = f.stat()
            old = INDEX.get(str(f))
            if not old or old["stamp"] != (st.st_mtime, st.st_size):
                grew = old and st.st_size > old["stamp"][1] and _boundary(f, old["stamp"][1])
                mails = ([m for m in old["mails"] if m["ts"] >= limit.timestamp()] + _read_folder(f, name, limit, old["stamp"][1])
                         if grew else _read_folder(f, name, limit))
                old, changed = {"stamp": (st.st_mtime, st.st_size), "mails": mails, "path": f}, True
            current[str(f)] = old
        changed = changed or current.keys() != INDEX.keys()
        INDEX.clear()
        INDEX.update(current)
        if changed:
            _save()
        everything, seen = [], set()
        for entry in INDEX.values():
            everything += entry["mails"]
    out = []
    for m in sorted(everything, key=lambda m: m["ts"], reverse=True):
        key = m["mid"] or f"{m['ts']}{m['subject']}"
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def search(q="", limit=50, prof=None):
    """Without a query the newest mails, otherwise all mails containing every word (accents and case ignored)
    in subject, sender, recipient or text. Returns (hits, total)."""
    words = _fold(q).split()
    hits = [m for m in index(prof) if all(w in m["search"] for w in words)]
    return [{k: m[k] for k in ("token", "date", "time", "folder", "from", "subject")} for m in hits[:limit]], len(hits)


def _find(token):
    return next(((e["path"], m) for e in INDEX.values() for m in e["mails"] if m["token"] == token), (None, None))


def _read(path, token):
    off, length, _ = token.split(":", 2)
    with open(path, "rb") as h:
        h.seek(int(off))
        return h.read(int(length))


def raw(token, prof=None):
    """Raw mail for a token from the search, read freshly from disk. Thunderbird may have compacted the folder
    since the search (the offsets moved): then the Message-ID no longer matches and the mail is looked up again."""
    path, m = _find(token)
    if not path:
        raise KeyError("Mail no longer in the index, please search again")
    data = _read(path, token)
    if not m["mid"] or m["mid"].encode() in data[:20000]:
        return data
    index(prof)  # re-reads changed folders
    fresh = next((x for e in INDEX.values() for x in e["mails"] if x["mid"] == m["mid"]), None)
    path2, _ = _find(fresh["token"]) if fresh else (None, None)
    if not path2:
        raise KeyError("Mail no longer in the index, please search again")
    return _read(path2, fresh["token"])


def warm_up():
    """Prepares the index in the background so the first search does not wait."""
    def run():
        with _WARM:
            try:
                if profile():
                    index()
            except Exception:  # an unreadable folder must not kill the thread silently forever; the search reports it
                pass
            finally:
                _BUILT.set()
    if not _WARM.locked():
        threading.Thread(target=run, daemon=True).start()


def ready():
    """True once the index was built at least once; otherwise starts building it and returns False (never blocks)."""
    if not _BUILT.is_set():
        warm_up()
    return _BUILT.is_set()


def program():
    """Command that starts Thunderbird, or None."""
    import shutil
    for c in ("thunderbird", "thunderbird.exe"):
        if shutil.which(c):
            return [shutil.which(c)]
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base and (Path(base) / "Mozilla Thunderbird/thunderbird.exe").is_file():
            return [str(Path(base) / "Mozilla Thunderbird/thunderbird.exe")]
    if Path("/Applications/Thunderbird.app/Contents/MacOS/thunderbird").is_file():
        return ["/Applications/Thunderbird.app/Contents/MacOS/thunderbird"]
    if shutil.which("flatpak") and (Path.home() / ".var/app/org.mozilla.Thunderbird").is_dir():
        return ["flatpak", "run", "org.mozilla.Thunderbird"]
    return None


def compose_arg(to, subject, body_file, files):
    """The -compose argument. Values sit in single quotes (commas inside are fine), so a quote in them becomes ’;
    the text goes through a file (message=) because newlines do not survive the command line."""
    q = lambda v: str(v).replace("'", "’").replace("\n", " ")
    parts = [f"to='{q(to)}'", f"subject='{q(subject)}'", f"message='{q(body_file)}'"]
    if files:
        parts.append("attachment='" + ",".join(Path(f).resolve().as_uri() for f in files) + "'")  # as_uri encodes commas
    return ",".join(parts)


def compose(to, subject, body, files=()):
    """Opens a Thunderbird compose window with the mail and the attachments. Nothing is sent: that stays a click
    in Thunderbird."""
    import subprocess
    import tempfile
    cmd = program()
    if not cmd:
        raise FileNotFoundError("Thunderbird not found")
    # ponytail: the text file stays in the temp folder (Thunderbird reads it after this returns)
    f = Path(tempfile.mkdtemp(prefix="bb-mail-")) / "mail.txt"
    f.write_text(body, "utf-8")
    subprocess.Popen([*cmd, "-compose", compose_arg(to, subject, f, files)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _self_test():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    (tmp / "ImapMail/x").mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    mails = [b"From: Service jobs.ch <a@jobs.ch>\nSubject: Your application\nDate: " + now.encode() +
             b"\nMessage-ID: <1@x>\nContent-Transfer-Encoding: quoted-printable\n\nApplication at =C3=89CLAIR AG\n",
             b"From: Anna <a@b.ch>\nSubject: =?utf-8?q?Gr=C3=BCsse?=\nDate: " + now.encode() + b"\nMessage-ID: <2@x>\n"
             b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: base64\n\nSGFsbG8gV2VsdA==\n"]
    (tmp / "ImapMail/x/INBOX").write_bytes(b"".join(b"From - Thu Nov 12 10:00:00 2026\n" + m + b"\n" for m in mails))
    assert len(search("", prof=tmp)[0]) == 2
    r, n = search("eclair", prof=tmp)
    assert n == 1 and r[0]["subject"] == "Your application", r
    assert search("hallo welt", prof=tmp)[1] == 1 and search("grusse", prof=tmp)[1] == 1
    assert b"=C3=89CLAIR" in raw(r[0]["token"]) and raw(r[0]["token"]).startswith(b"From: Service")
    box = tmp / "ImapMail/x/INBOX"  # compaction: the first mail is gone, the offsets of the others move
    token = search("grusse", prof=tmp)[0][0]["token"]
    box.write_bytes(b"From - Thu Nov 12 10:00:00 2026\n" + mails[1] + b"\n")
    assert b"Message-ID: <2@x>" in raw(token, prof=tmp)
    global CHUNK, CACHE  # backwards in small chunks, a mail bigger than a chunk, appending, cache
    CHUNK, CACHE = 300, tmp / "cache.json.gz"
    old = (datetime.now(timezone.utc) - timedelta(days=DAYS + 5)).strftime("%a, %d %b %Y %H:%M:%S +0000").encode()
    mail = lambda i, d=now.encode(), body=b"text": (b"From - Thu Nov 12 10:00:00 2026\nFrom: a@b.ch\nSubject: m" + str(i).encode() +
                                                     b"\nDate: " + d + b"\nMessage-ID: <m" + str(i).encode() + b"@x>\n\n" + body + b"\n")
    box.write_bytes(b"".join(mail(i, old) for i in range(20)) + mail(20, body=b"x" * 2000) + b"".join(mail(i) for i in range(21, 30)))
    INDEX.clear()
    assert sorted(m["subject"] for m in index(tmp)) == sorted(f"m{i}" for i in range(20, 30)), index(tmp)
    with open(box, "ab") as h:
        h.write(mail(30))
    assert search("m30", prof=tmp)[1] == 1 and search("", prof=tmp)[1] == 11
    INDEX.clear()  # a restart: everything comes from the cache, nothing changed on disk
    _load()
    assert sum(len(e["mails"]) for e in INDEX.values()) == 11 and b"Subject: m30" in raw(search("m30", prof=tmp)[0][0]["token"])
    arg = compose_arg("a@b.ch", "Bewerbung, Zürich's", "/t/m.txt", ["/x/A, B.pdf", "/x/c.pdf"])
    uri = lambda f: Path(f).resolve().as_uri()  # file:///C:/x/... on Windows
    assert arg == f"to='a@b.ch',subject='Bewerbung, Zürich’s',message='/t/m.txt',attachment='{uri('/x/A, B.pdf')},{uri('/x/c.pdf')}'", arg
    assert "A%2C%20B.pdf" in arg
    print("ok")


if __name__ == "__main__":  # without argument: self-test; otherwise python3 thunderbird.py "search term"
    if len(sys.argv) == 1:
        sys.exit(_self_test())
    t0 = time.time()
    r, n = search(sys.argv[1])
    t1 = time.time()
    for m in r[:15]:
        print(m["date"], m["time"], m["folder"], "|", m["from"][:40], "|", m["subject"][:70])
    search(sys.argv[1] + " x")
    print(f"{n} hits, first call {t1 - t0:.1f}s, second {time.time() - t1:.2f}s")
