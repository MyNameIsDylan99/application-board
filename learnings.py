"""Feedback history and learnings.

Every feedback round (own feedback, the AI's answer) is appended as a line to
Bewerbungen/_Review/feedback_log.jsonl. On request an AI provider condenses the rounds into lasting learnings;
the user edits the suggestions and has them written as memory files (same format as Claude's memory: front
matter name/description/metadata.type, index in MEMORY.md). Which rounds were already evaluated is stored in
_Review/lernstand.json.

The stored keys of the log (ts, path, firma, feedback, antwort, typ, nachgetragen, ...) are the data format
and stay as they are.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import ai
import userprofile

TYPES = ("feedback", "user", "project", "reference")


def log_path(apps):
    return apps / "_Review" / "feedback_log.jsonl"


def state_path(apps):
    return apps / "_Review" / "lernstand.json"


def append(apps, entry):
    p = log_path(apps)
    p.parent.mkdir(exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")


def read(apps, state=None):
    """All rounds, oldest first. The first time, the old answers from review.json are added as back-filled
    rounds (the feedback itself was not stored back then)."""
    p = log_path(apps)
    if not p.is_file() and state:
        backups = sorted((apps / "_Review" / "backups").glob("*.md")) if (apps / "_Review" / "backups").is_dir() else []
        for rel, e in state.items():
            if e.get("antwort"):
                name = Path(rel).name
                b = [x for x in backups if x.name.endswith(f"_{Path(rel).parent.name}_{name}")]
                m = re.match(r"(\d{8})_(\d{6})", b[-1].name) if b else None
                ts = datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").isoformat() if m else datetime.now().isoformat(timespec="seconds")
                p.parent.mkdir(exist_ok=True)
                with p.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": ts, "path": rel, "firma": Path(rel).parent.name, "feedback": "", "antwort": e["antwort"],
                                        "runden": len(b), "nachgetragen": True}, ensure_ascii=False) + "\n")
    if not p.is_file():
        return []
    out = []
    for line in p.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda e: e.get("ts", ""))


def evaluated_until(apps):
    try:
        return json.loads(state_path(apps).read_text("utf-8")).get("bis", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def set_evaluated_until(apps, until):
    state_path(apps).write_text(json.dumps({"bis": until}) + "\n", "utf-8")


PROMPT = """You evaluate feedback rounds in which {person} revised cover letters with an AI.
Goal: lasting learnings for future applications as memory entries, not one-off corrections.

Rules:
- Only patterns that apply beyond a single letter (style, wording, what to stress or avoid, facts about
  {person} that were corrected). A one-off typo is not a learning.
- Check the existing memories below. If a learning is already there, leave it out. If it extends or corrects an
  existing memory, use the same "file" and write the complete new text.
- Write the entries in {language}.
{rules}- body: the rule in one to three sentences, then a line "**Why:** ..." (which feedback led to it) and a line
  "**How to apply:** ..." (when and how to apply it).
- type is usually "feedback"; "user" for facts about {person}, "project" for the state of single applications.
- At most 8 entries; fewer and solid is better. If there is nothing new, return an empty list.

Answer only with JSON:
{{"learnings": [{{"file": "feedback_short_name.md", "name": "short-kebab-name", "title": "Short title", "description": "one line, when relevant", "type": "feedback", "body": "...", "sources": ["Company", "..."]}}]}}

=== FEEDBACK ROUNDS ===
{rounds}

=== EXISTING MEMORIES ===
{memory}
"""
LANGUAGE_NAMES = {"de": "German", "en": "English"}


def summarize(provider, root, rounds, memory, lang="de"):
    text = "\n\n".join(f"[{r.get('ts', '')[:10]}] {r.get('firma', '')}\nFeedback: {r.get('feedback') or '(not stored)'}\n"
                       f"AI answer: {r.get('antwort', '')}" for r in rounds)
    mem = "\n\n".join(f"--- {m['file']} ---\n{m['content'][:2500]}" for m in memory)
    prof = userprofile.load(root)
    r = userprofile.rules(prof)
    out = ai.complete(provider, PROMPT.format(person=userprofile.person(prof), rules=f"- Writing rules:\n{r}\n" if r else "",
                                              language=LANGUAGE_NAMES.get(lang, "German"), rounds=ai.clip(text, 60000, "rounds"), memory=ai.clip(mem, 40000, "memories") or "(none)"),
                      max_tokens=6000, timeout=600, cwd=root, disallowed=["Bash", "Write", "Edit"])
    try:
        raw = ai.extract_json(out).get("learnings", [])
    except ValueError:
        raise RuntimeError("Answer without readable JSON: " + out[:300])
    suggestions = []
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict) or not str(v.get("body", "")).strip():
            continue
        kind = v.get("type") if v.get("type") in TYPES else "feedback"
        name = slug(v.get("name") or v.get("title") or "learning")
        file = v.get("file") if re.fullmatch(r"[a-z0-9_]+\.md", str(v.get("file", ""))) else f"{kind}_{name.replace('-', '_')}.md"
        suggestions.append({"file": file, "name": name, "title": str(v.get("title") or name).strip(), "type": kind,
                            "description": str(v.get("description", "")).strip(), "body": userprofile.ss(str(v["body"]).strip(), prof),
                            "sources": [str(q) for q in v.get("sources", []) if q][:8]})
    return suggestions


def slug(s):
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "learning"


def write(memdir, entries):
    """Writes memory files and adds missing lines to MEMORY.md. Returns the names of the written files."""
    memdir.mkdir(parents=True, exist_ok=True)
    index = memdir / "MEMORY.md"
    idx = index.read_text("utf-8") if index.is_file() else ""
    new_lines, done = [], []
    for e in entries:
        file = str(e.get("file", ""))
        if not re.fullmatch(r"[a-z0-9_]+\.md", file) or file == "MEMORY.md":
            raise ValueError(f"File name not allowed: {file!r}")
        kind = e.get("type") if e.get("type") in TYPES else "feedback"
        desc = " ".join(str(e.get("description", "")).split()).replace('"', "'")
        text = (f"---\nname: {slug(e.get('name') or file[:-3])}\ndescription: \"{desc}\"\nmetadata:\n  type: {kind}\n"
                f"  modified: {datetime.now().isoformat(timespec='seconds')}\n---\n\n{str(e.get('body', '')).strip()}\n")
        (memdir / file).write_text(text, "utf-8")
        done.append(file)
        if f"({file})" not in idx:
            title = " ".join(str(e.get("title") or file).split())
            new_lines.append(f"- [{title}]({file}) — {desc}")
    if new_lines:
        index.write_text(idx.rstrip("\n") + ("\n" if idx else "") + "\n".join(new_lines) + "\n", "utf-8")
    return done


if __name__ == "__main__":  # self-test in a temp folder
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    apps = tmp / "Bewerbungen"
    (apps / "_Review" / "backups").mkdir(parents=True)
    (apps / "_Review" / "backups" / "20261110_101500_000001_Firma AG_Bewerbungsschreiben_X.md").write_text("old")
    old = read(apps, {"Bewerbungen/Firma AG/Bewerbungsschreiben_X.md": {"antwort": "I changed X."}})
    assert len(old) == 1 and old[0]["nachgetragen"] and old[0]["ts"].startswith("2026-11-10T10:15"), old
    append(apps, {"path": "p", "firma": "F", "feedback": "shorter", "antwort": "ok"})
    assert [r["feedback"] for r in read(apps)] == ["", "shorter"]
    mem = tmp / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [Old](feedback_old.md) — x\n")
    assert write(mem, [{"file": "feedback_new.md", "name": "New Rule", "title": "New", "description": "when", "body": "Rule.\n\n**Why:** x"},
                       {"file": "feedback_old.md", "title": "Old", "description": "y", "body": "new"}]) == ["feedback_new.md", "feedback_old.md"]
    idx = (mem / "MEMORY.md").read_text()
    assert idx.count("feedback_old.md") == 1 and "[New](feedback_new.md) — when" in idx, idx
    assert "name: new-rule" in (mem / "feedback_new.md").read_text()
    try:
        write(mem, [{"file": "../x.md", "body": "b"}])
        raise AssertionError("the path should have been rejected")
    except ValueError:
        pass
    print("ok")
