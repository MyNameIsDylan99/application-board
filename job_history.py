#!/usr/bin/env python3
"""History of the job search (applications/_jobsearch/history.jsonl; Bewerbungen/_Stellensuche/verlauf.jsonl in the
German layout, see layout.py).

Purpose: normalise URLs the same way every time and make the comparison against earlier findings
deterministic. Done by hand, every run tends to make the same mistake: the same posting once with and once
without "www." or with a tracking parameter, so it counts as new again and the NEW marker loses its value.

Usage
-----
  python job_history.py known <data-root>
      Prints all known normalised URLs, one per line.

  python job_history.py check <data-root> <urls.txt>
      Reads candidate URLs (one per line) and prints "NEW <url>" or "KNOWN <url>" for each.

  python job_history.py append <data-root> <jobs.json> [YYYY-MM-DD]
      jobs.json is a list of objects with url, company, title, note (grade). The date found defaults to today; it
      can be given as third argument, per entry in the field "found" or for all in the dict field "date".
      The German field names (firma, titel, gefunden, stellen, datum) work too.
      Only grades A, B or C are added; rejected postings stay out, otherwise they would count as known and
      never show up again. Known URLs are skipped. Output: number of lines appended.

The German subcommands bekannt / pruefen / anhaengen still work (with German output) for existing scripts.
Stored fields: url, company, title, note, found (German keys in folders of the German layout).
"""

import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also when called from a skill in another folder
import layout  # noqa: E402

GRADES = {"A", "B", "C"}


def normalize(url: str) -> str:
    """https, without www., without query and fragment, without trailing slash."""
    url = url.strip()
    if not url:
        return ""
    if "//" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"https://{host}{parts.path.rstrip('/')}"


def history_path(root: str) -> Path:
    return layout.get(root).history


def known(root: str) -> set:
    return {normalize(e["url"]) for e in layout.get(root).read_jsonl("history") if e.get("url")}


def append(root: str, jobs: list, found: str) -> int:
    seen = known(root)
    new = []
    for s in jobs:
        s = layout.translate(s, layout.FILE_KEYS["history"], {})  # German input fields (firma, titel, gefunden) too
        grade = str(s.get("note", "")).strip().upper()
        if grade not in GRADES:
            print(f"Skipped (grade {grade or 'missing'} instead of A/B/C): {s.get('url')}", file=sys.stderr)
            continue
        url = normalize(s.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        new.append({"url": url, "company": s.get("company", ""), "title": s.get("title", ""),
                    "note": grade, "found": s.get("found") or found})
    if new:
        layout.get(root).append_jsonl("history", new)
    return len(new)


GERMAN = {"bekannt": "known", "pruefen": "check", "anhaengen": "append"}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    command, root = sys.argv[1], sys.argv[2]
    german = command in GERMAN
    command = GERMAN.get(command, command)
    if command == "known":
        for url in sorted(known(root)):
            print(url)
    elif command == "check":
        seen = known(root)
        for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines():
            url = normalize(line)
            if url:
                word = ("BEKANNT" if german else "KNOWN") if url in seen else ("NEU" if german else "NEW")
                print(f"{word} {url}")
    elif command == "append":
        data = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
        jobs = data if isinstance(data, list) else data.get("jobs", data.get("stellen", []))
        found = date.today().isoformat()
        if isinstance(data, dict) and (data.get("date") or data.get("datum")):
            found = data.get("date") or data["datum"]
        if len(sys.argv) > 4:
            found = sys.argv[4]
        n = append(root, jobs, found)
        print(f"{n} Zeilen angehaengt" if german else f"{n} lines appended")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
