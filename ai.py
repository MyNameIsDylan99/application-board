"""Provider-independent text completion for the board.

Types: claude-code (claude -p), anthropic (Messages API), openai-compatible (chat/completions),
gemini (generateContent), cli (any command). API keys are kept separately in secrets.json (0600) or come
from an environment variable (key_ref "env:NAME"); they never reach the browser.
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import storage

SECRETS = storage.config_file("secrets.json")
USAGE = storage.config_file("usage.jsonl")  # tokens per request, never any content
NO_WEB = ["WebFetch", "WebSearch"]  # completions never need the web; a posting must not be able to send data out
_PROCS = {}  # thread id -> running process (claude or cli), for cancel()
_CANCELLED = set()  # thread ids whose job was cancelled
TYPES = ("claude-code", "anthropic", "openai-compatible", "gemini", "cli")
DEFAULT_BASE = {"anthropic": "https://api.anthropic.com", "gemini": "https://generativelanguage.googleapis.com",
                "openai-compatible": "https://api.openai.com/v1"}
UA = "Bewerbungs-Board/1.0"


class AIError(Exception):
    pass


def clip(text, n, what="text"):
    """Cut text to n characters and say so, instead of cutting silently."""
    text = text or ""
    return text if len(text) <= n else text[:n] + f"\n[... {what} cut after {n} of {len(text)} characters]"


def cancel(thread_id):
    """Cancel the job running in that thread: kills claude/cli at once, an HTTP answer is thrown away when it arrives."""
    _CANCELLED.add(thread_id)
    proc = _PROCS.get(thread_id)
    if proc and proc.poll() is None:
        proc.kill()


def reset():
    """Call at the start of a job thread: thread ids are reused, an old cancel must not hit a new job."""
    _CANCELLED.discard(threading.get_ident())


def cancelled(thread_id=None):
    return (thread_id or threading.get_ident()) in _CANCELLED


def check_cancelled():
    if cancelled():
        raise AIError("Cancelled")


def _run(args, inp, timeout, cwd):
    """subprocess.run with a handle for cancel()."""
    me = threading.get_ident()
    check_cancelled()
    proc = subprocess.Popen(args, stdin=subprocess.PIPE if inp is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=cwd)  # UTF-8 also on Windows
    _PROCS[me] = proc
    try:
        out, err = proc.communicate(inp, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise AIError("Timeout")
    finally:
        _PROCS.pop(me, None)
    check_cancelled()
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


# ---------- Keys ----------

def load_secrets():
    try:
        return json.loads(SECRETS.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_secrets(d):
    fd = os.open(SECRETS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.chmod(SECRETS, 0o600)


def resolve_key(p):
    ref = p.get("key_ref") or ""
    if ref.startswith("env:"):
        v = os.environ.get(ref[4:], "")
        if not v:
            raise AIError(f"Environment variable {ref[4:]} is not set")
        return v
    if ref == "secret":
        v = load_secrets().get(p["id"], "")
        if not v:
            raise AIError("No API key stored")
        return v
    return ""


def masked(p):
    """What the browser may see: never the key itself."""
    ref = p.get("key_ref") or ""
    if ref.startswith("env:"):
        return ref + (" (set)" if os.environ.get(ref[4:]) else " (not set)")
    if ref == "secret":
        k = load_secrets().get(p["id"], "")
        return (k[:3] + "…" + k[-4:]) if len(k) > 8 else ("…" + k[-2:] if k else "")
    return ""


def public(p):
    return {k: v for k, v in p.items() if k != "key"} | {"key_masked": masked(p)}


# ---------- Usage ----------

def record(p, tin=0, tout=0, cost=None):
    """One line per answered request: time, task, provider, model, tokens and (Claude Code only) the cost in USD."""
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": p.get("_task", ""), "provider": p.get("name") or p.get("type", ""),
            "model": p.get("model", ""), "in": int(tin or 0), "out": int(tout or 0)}
    if cost:
        line["cost"] = round(float(cost), 5)
    try:
        with USAGE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass  # usage is a convenience, never a reason to fail the job


def usage(months=6):
    """Totals per month and task: requests, tokens in and out, cost."""
    out = {}
    try:
        text = USAGE.read_text("utf-8")
    except FileNotFoundError:
        return []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = (r.get("ts", "")[:7], r.get("task") or "", r.get("provider") or "")
        a = out.setdefault(k, {"month": k[0], "task": k[1], "provider": k[2], "n": 0, "in": 0, "out": 0, "cost": 0.0})
        a["n"] += 1
        a["in"] += r.get("in", 0)
        a["out"] += r.get("out", 0)
        a["cost"] += r.get("cost", 0)
    keep = sorted({k[0] for k in out}, reverse=True)[:months]
    return sorted((v for k, v in out.items() if k[0] in keep), key=lambda v: (v["month"], v["task"]), reverse=True)


# ---------- HTTP ----------

def _http(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")[:2000]
        try:
            j = json.loads(text)
            err = j.get("error", j)
            msg = err.get("message") if isinstance(err, dict) else err
        except (json.JSONDecodeError, AttributeError):
            msg = text
        raise AIError(f"HTTP {e.code}: {str(msg)[:600]}")
    except urllib.error.URLError as e:
        raise AIError(f"Connection failed: {e.reason}")
    except (OSError, json.JSONDecodeError) as e:
        raise AIError(f"Error: {e}")


def _base(p):
    return (p.get("base_url") or DEFAULT_BASE.get(p["type"], "")).rstrip("/")


# ---------- claude -p ----------

def split_cmd(cmd, nt=os.name == "nt"):
    """Command line into arguments, without a shell. On Windows backslashes in paths stay and quotes only group."""
    if not nt:
        return shlex.split(cmd)
    return [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in shlex.split(cmd, posix=False)]


def run_claude(prompt, cwd, model="", allowed=None, disallowed=None, timeout=900, resume="", info=None, task=""):
    """resume: continue a Claude session; info (dict) receives the session_id of the answer; task: for the usage log."""
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if resume:
        cmd += ["--resume", resume]
    if model:
        cmd += ["--model", model]
    if allowed:
        cmd += ["--allowedTools", *allowed]
    if disallowed:
        cmd += ["--disallowedTools", *disallowed]
    try:
        r = _run(cmd, None, timeout, cwd)
    except FileNotFoundError:
        raise AIError("Command claude not found (is Claude Code installed?)")
    try:
        out = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        out = {}
    if r.returncode or out.get("is_error"):
        raise AIError(out.get("result") or r.stderr.strip()[:1000] or f"Exit {r.returncode}")
    if info is not None:
        info["session_id"] = out.get("session_id", "")
    u = out.get("usage") or {}
    record({"_task": task, "name": "Claude Code", "model": model}, sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
           u.get("output_tokens"), out.get("total_cost_usd"))
    return (out.get("result") or "").strip()


# ---------- Completion ----------

def complete(p, prompt, system="", max_tokens=None, temperature=None, timeout=300, cwd=None, allowed=None, disallowed=None):
    t = p["type"]
    mt = int(max_tokens or p.get("max_tokens") or 8000)
    temp = temperature if temperature is not None else p.get("temperature")
    if t == "claude-code":
        full = f"{system}\n\n{prompt}" if system else prompt
        return run_claude(full, cwd or Path.cwd(), p.get("model", ""), allowed, list(disallowed or []) + NO_WEB, timeout, task=p.get("_task", ""))
    if t == "cli":
        args = split_cmd(p.get("cmd") or "")
        if not args:
            raise AIError("No command configured")
        full = f"{system}\n\n{prompt}" if system else prompt
        if any("{prompt}" in a for a in args):
            args, inp = [a.replace("{prompt}", full) for a in args], None
        else:
            inp = full
        try:
            r = _run(args, inp, timeout, cwd)
        except FileNotFoundError:
            raise AIError(f"Command {args[0]} not found")
        if r.returncode:
            raise AIError(f"Exit {r.returncode}: {r.stderr.strip()[:1000]}")
        record(p)
        return r.stdout.strip()
    key = resolve_key(p) if t != "openai-compatible" else (resolve_key(p) if p.get("key_ref") else "")
    if t == "anthropic":
        body = {"model": p.get("model") or "claude-sonnet-5-5", "max_tokens": mt, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        if temp is not None:
            body["temperature"] = temp
        r = _http(_base(p) + "/v1/messages", body, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        u = r.get("usage") or {}
        record(p, u.get("input_tokens"), u.get("output_tokens"))
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    if t == "openai-compatible":
        if not p.get("model"):
            raise AIError("No model given")
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": p["model"], "messages": msgs, "max_tokens": mt}
        if temp is not None:
            body["temperature"] = temp
        r = _http(_base(p) + "/chat/completions", body, {"Authorization": "Bearer " + key} if key else {}, timeout)
        u = r.get("usage") or {}
        record(p, u.get("prompt_tokens"), u.get("completion_tokens"))
        try:
            return (r["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("Answer without choices[0].message.content")
    if t == "gemini":
        model = p.get("model") or "gemini-2.5-flash"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": mt}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temp is not None:
            body["generationConfig"]["temperature"] = temp
        r = _http(f"{_base(p)}/v1beta/models/{model}:generateContent", body, {"x-goog-api-key": key}, timeout)
        u = r.get("usageMetadata") or {}
        record(p, u.get("promptTokenCount"), u.get("candidatesTokenCount"))
        try:
            return "".join(part.get("text", "") for part in r["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("Answer without candidates[0].content.parts")
    raise AIError(f"Unknown type {t}")


def test(p, cwd=None):
    t0 = time.time()
    text = complete(p, "Answer only with the word OK.", max_tokens=50, timeout=90, cwd=cwd, disallowed=["Bash", "Write", "Edit"])
    return {"ok": True, "text": text[:200], "ms": int((time.time() - t0) * 1000)}


def between(text, start, end):
    """Text between two markers, or "" if they are missing."""
    m = re.search(re.escape(start) + r"\s*\n(.*?)\n\s*" + re.escape(end), text, re.S)
    return m.group(1) if m else ""


def extract_json(text):
    """First JSON object in a model answer, even with text or ``` around it."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


if __name__ == "__main__":  # small self-test without network
    assert split_cmd(r'"C:\Program Files\x.exe" C:\t\mock.py --a', nt=True) == [r"C:\Program Files\x.exe", r"C:\t\mock.py", "--a"]
    assert split_cmd("/usr/bin/x 'a b'", nt=False) == ["/usr/bin/x", "a b"]
    assert between("a\n===X===\nhello\nworld\n===Y===\nb", "===X===", "===Y===") == "hello\nworld"
    assert extract_json('Here: ```json\n{"note": "A"}\n```')["note"] == "A"
    assert masked({"id": "x", "key_ref": "env:NOT_THERE_123"}).endswith("(not set)")
    assert clip("abcdef", 3, "profile").startswith("abc\n[... profile cut after 3 of 6")
    import tempfile
    USAGE = Path(tempfile.mkdtemp()) / "usage.jsonl"
    assert complete({"type": "cli", "cmd": "cat", "name": "Cat", "_task": "rate"}, "hi") == "hi"
    record({"_task": "rate", "name": "Cat"}, 10, 5, 0.01)
    assert usage()[0] | {"month": ""} == {"month": "", "task": "rate", "provider": "Cat", "n": 2, "in": 10, "out": 5, "cost": 0.01}, usage()

    def slow():
        try:
            complete({"type": "cli", "cmd": "sleep 30"}, "", timeout=60)
        except AIError as e:
            result.append(str(e))
    result = []
    t = threading.Thread(target=slow)
    t0 = time.time()
    t.start()
    time.sleep(0.3)
    cancel(t.ident)
    t.join(5)
    assert result == ["Cancelled"] and time.time() - t0 < 5, result
    print("ok")
