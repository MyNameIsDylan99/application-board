"""Feedback history and learnings.

Every feedback round (own feedback, the AI's answer) is appended as a line to the feedback log
(applications/_review/feedback_log.jsonl, see layout.py). On request an AI provider condenses the rounds into
lasting learnings; the user edits the suggestions and has them written as memory files (same format as Claude's
memory: front matter name/description/metadata.type, index in MEMORY.md). Which rounds were already evaluated is
stored in learnings_state.json.

Keys of a round: ts, path, company, feedback, answer, kind ("undo" for a rejected round), provider, session,
backfilled, rounds, changed, backup. Folders of the German layout keep their German keys on disk.
"""
import re
from datetime import datetime
from pathlib import Path

import ai
import layout
import userprofile

TYPES = ("feedback", "user", "project", "reference")


def append(root, entry):
    layout.get(root).append_jsonl("feedback_log", [{"ts": datetime.now().isoformat(timespec="seconds"), **entry}])


def read(root, state=None):
    """All rounds, oldest first. The first time, the old answers from the state file are added as back-filled
    rounds (the feedback itself was not stored back then)."""
    lay = layout.get(root)
    if not lay.feedback_log.is_file() and state:
        backups = sorted(lay.backups.glob("*.md")) if lay.backups.is_dir() else []
        old = []
        for rel, e in state.items():
            if e.get("answer"):
                b = [x for x in backups if x.name.endswith(f"_{Path(rel).parent.name}_{Path(rel).name}")]
                m = re.match(r"(\d{8})_(\d{6})", b[-1].name) if b else None
                ts = datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").isoformat() if m else datetime.now().isoformat(timespec="seconds")
                old.append({"ts": ts, "path": rel, "company": Path(rel).parent.name, "feedback": "", "answer": e["answer"],
                            "rounds": len(b), "backfilled": True})
        if old:
            lay.append_jsonl("feedback_log", old)
    return sorted(lay.read_jsonl("feedback_log"), key=lambda e: e.get("ts", ""))


def evaluated_until(root):
    return (layout.get(root).read_json("learnings_state") or {}).get("until", "")


def set_evaluated_until(root, until):
    layout.get(root).write_json("learnings_state", {"until": until})


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
    text = "\n\n".join(f"[{r.get('ts', '')[:10]}] {r.get('company', '')}\nFeedback: {r.get('feedback') or '(not stored)'}\n"
                       f"AI answer: {r.get('answer', '')}" for r in rounds)
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


if __name__ == "__main__":  # self-test in a temp folder, English and German layout
    import json
    import tempfile
    for german in (False, True):
        tmp = Path(tempfile.mkdtemp())
        if german:
            (tmp / "Bewerbungen").mkdir()
        lay = layout.get(tmp)
        lay.backups.mkdir(parents=True)
        letter = lay.rel("apps") + "/Firma AG/" + lay.letter_name("X")
        (lay.backups / f"20261110_101500_000001_Firma AG_{lay.letter_name('X')}").write_text("old", "utf-8")
        old = read(tmp, {letter: {"answer": "I changed X."}})
        assert len(old) == 1 and old[0]["backfilled"] and old[0]["ts"].startswith("2026-11-10T10:15"), old
        append(tmp, {"path": "p", "company": "F", "feedback": "shorter", "answer": "ok", "kind": "undo"})
        assert [r["feedback"] for r in read(tmp)] == ["", "shorter"] and read(tmp)[1]["kind"] == "undo"
        set_evaluated_until(tmp, "2026-11-11")
        assert evaluated_until(tmp) == "2026-11-11"
        last = json.loads(lay.feedback_log.read_text("utf-8").splitlines()[-1])
        assert (last["firma"], last["antwort"], last["typ"]) == ("F", "ok", "rueckgaengig") if german else last["company"] == "F", last
        assert lay.learnings_state.name == ("lernstand.json" if german else "learnings_state.json")
    mem = tmp / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [Old](feedback_old.md) — x\n", "utf-8")
    assert write(mem, [{"file": "feedback_new.md", "name": "New Rule", "title": "New", "description": "when", "body": "Rule.\n\n**Why:** x"},
                       {"file": "feedback_old.md", "title": "Old", "description": "y", "body": "new"}]) == ["feedback_new.md", "feedback_old.md"]
    idx = (mem / "MEMORY.md").read_text("utf-8")
    assert idx.count("feedback_old.md") == 1 and "[New](feedback_new.md) — when" in idx, idx
    assert "name: new-rule" in (mem / "feedback_new.md").read_text("utf-8")
    try:
        write(mem, [{"file": "../x.md", "body": "b"}])
        raise AssertionError("the path should have been rejected")
    except ValueError:
        pass
    print("ok")
