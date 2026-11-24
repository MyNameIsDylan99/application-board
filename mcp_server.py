#!/usr/bin/env python3
"""MCP server (stdio): lets Claude Desktop, Claude Code or another MCP client read the board and save drafts.

Start:  python3 mcp_server.py [data folder]
        Data folder: the argument, otherwise APPLICATION_BOARD_ROOT, otherwise the folder stored in the board's settings.

Tools: list_applications, read_application, read_profile, list_job_results, save_draft. Nothing is sent and nothing
is deleted; save_draft only creates a new letter file and never overwrites one. JSON-RPC 2.0, one message per line.
"""
import json
import os
import re
import sys
from pathlib import Path

import jobsearch
import layout
import pipeline
import storage
import userprofile

VERSION = "0.6.0"
LETTER = re.compile(r"^(applications/[^/]+/letter|Bewerbungen/[^/]+/Bewerbungsschreiben)[^/]*\.md$")  # English or German layout


def data_root(argv):
    if len(argv) > 1:
        return Path(argv[1]).expanduser().resolve()
    if storage.env("ROOT"):
        return Path(storage.env("ROOT")).expanduser().resolve()
    try:
        return Path(json.loads(storage.config_file("settings.json").read_text("utf-8"))["root"])
    except (OSError, KeyError, json.JSONDecodeError):
        return Path.cwd()


def letter_path(root, rel):
    p = (root / rel).resolve()
    if not LETTER.match(rel) or not p.is_file() or not p.is_relative_to(layout.get(root).apps.resolve()):
        raise ValueError("unknown letter path; take one from list_applications")
    return p


def list_applications(root, args):
    lay = layout.get(root)
    state = lay.read_json("state", {})
    out = []
    for p in lay.letters():
        rel = p.relative_to(root).as_posix()
        e = state.get(rel, {})
        m = re.search(r"^\*\*(?:Titel|Title):\*\*\s*(.+)$", p.read_text("utf-8"), re.M)
        out.append({"path": rel, "company": p.parent.name, "title": m.group(1).strip() if m else "",
                    "status": e.get("status", ""), "phase": e.get("phase", ""), "since": e.get("since", "")})
    return out


def read_application(root, args):
    p = letter_path(root, str(args.get("path", "")))
    d = pipeline.read(p)
    return {"letter": p.read_text("utf-8"), "log": {k: d[k] for k in ("events", "preparation", "notes")}}


def read_profile(root, args):
    prof = userprofile.load(root)
    return {"details": {k: prof.get(k) for k in ("name", "town", "email", "rules")},
            **{k: userprofile.text(root, prof, k) for k in ("profile", "style", "search_profile", "cv")}}


def list_job_results(root, args):
    runs = jobsearch.runs(root)
    if not runs:
        return {"run": "", "jobs": []}
    r = jobsearch.parse_run((root / runs[0]["rel"]).read_text("utf-8"))
    return {"run": runs[0]["name"], "jobs": [{k: j.get(k) for k in ("title", "company", "place", "note", "score", "link", "pros", "gaps", "new")} for j in r["jobs"]]}


def save_draft(root, args):
    company, content = str(args.get("company", "")).strip(), str(args.get("content", ""))
    if not company or not re.search(r"^# (Bewerbungsschreiben|Cover Letter)\s*$", content, re.M):
        raise ValueError('company missing, or the content has no heading "# Bewerbungsschreiben" / "# Cover Letter" before the letter')
    p = jobsearch.new_letter_path(root, company)  # never overwrites
    storage.write_atomic(p, userprofile.ss(content.strip(), userprofile.load(root)) + "\n")
    return {"path": p.relative_to(root).as_posix(), "note": "Saved as a new letter; it shows up in the board under Open."}


S = {"type": "string"}
TOOLS = {
    "list_applications": (list_applications, "All applications of the board: path, company, title, status (open, final, sent, archived) and phase (sent, no_reply, replied, interview, offer, rejected).", {}, []),
    "read_application": (read_application, "One application: the whole Markdown file (job info, mapping, letter) and its log (mails, dates, notes).", {"path": S}, ["path"]),
    "read_profile": (read_profile, "The person's profile: details, profile text (the only source of facts), writing style, search profile, CV content.", {}, []),
    "list_job_results": (list_job_results, "Jobs of the newest job search run with grade, score, pros and gaps.", {}, []),
    "save_draft": (save_draft, "Save a new application file for a company (never overwrites). Use the structure of existing letters: job info, "
                   "'# Requirements & Skills Mapping', then '# Bewerbungsschreiben' (German) or '# Cover Letter' (English) with the letter. "
                   "Invent no facts: only what read_profile backs.", {"company": S, "content": S}, ["company", "content"]),
}


def handle(root, msg):
    """One JSON-RPC message -> answer dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "application-board", "version": VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req}}
                            for n, (_, d, props, req) in TOOLS.items()]}
    elif method == "tools/call":
        tool = TOOLS.get(params.get("name"))
        if not tool:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}}
        try:
            result = {"content": [{"type": "text", "text": json.dumps(tool[0](root, params.get("arguments") or {}), ensure_ascii=False, indent=1)}], "isError": False}
        except (ValueError, OSError, KeyError) as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(argv):
    root = data_root(argv)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            answer = handle(root, json.loads(line))
        except json.JSONDecodeError:
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if answer:
            sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv)
