#!/usr/bin/env python3
"""Knowledge Galaxy server: serves viewer/ on port 4700 and answers POST /chat from your notes.

Standard library only. The API key lives in ./config.json (project root), which is never served.
"""
import json
import math
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
VIEWER_DIR = os.path.join(ROOT, "viewer")
CONFIG_PATH = os.path.join(ROOT, "config.json")
INDEX_PATH = os.path.join(ROOT, "index.json")
PORT = 4700
API_URL = "https://api.anthropic.com/v1/messages"
TOP_K = 6
MAX_EXCHANGES = 6          # question/answer pairs kept per conversation
MAX_SESSIONS = 200
NOTE_CHARS = 2500          # per-note text sent to the model

SYSTEM_PROMPT = (
    "You answer questions about the user's personal notes. Answer ONLY from the notes provided "
    "in the latest message; do not use outside knowledge. Reply in two or three plain sentences. "
    "If the notes do not cover the question, say plainly that your notes don't cover it. "
    "Earlier turns are context for follow-up questions. "
    "After your answer, on its own final line, write SOURCES: followed by the bracketed numbers of "
    "the notes you actually used, comma-separated (for example SOURCES: 3, 17), or SOURCES: none."
)

STOPWORDS = set("""a about above after again against all am an and any are as at be because been before
being below between both but by can did do does doing down during each few for from further had has have
having he her here hers him his how i if in into is it its itself just me more most my no nor not now of
off on once only or other our ours out over own same she should so some such than that the their them
then there these they this those through to too under until up very was we were what when where which
while who whom why will with you your yours tell know much many give show get got also would could
anything something does did any""".split())

_lock = threading.Lock()
_sessions = {}             # session id -> list of {"role", "content"} (text only, append-only per turn)
_docs = []
_doc_tokens = []
_title_tokens = []
_idf = {}


def tokenize(text):
    words = re.findall(r"[a-z0-9$][a-z0-9'$.-]*", text.lower())
    out = []
    for w in words:
        w = w.strip(".'-")
        if len(w) < 3 or w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def load_index():
    global _docs, _doc_tokens, _title_tokens, _idf
    with open(INDEX_PATH, encoding="utf-8") as f:
        docs = json.load(f)
    assert all(d["id"] == i for i, d in enumerate(docs)), "index.json ids must equal array index"
    _docs = docs
    _doc_tokens = [Counter(tokenize(d["text"])) for d in docs]
    _title_tokens = [set(tokenize(d["label"])) for d in docs]
    n = len(docs)
    df = Counter()
    for c in _doc_tokens:
        df.update(set(c))
    _idf = {t: math.log(1 + n / k) for t, k in df.items()}


def score_notes(question, previous_question=""):
    """Keyword overlap; title hits weigh 4x body hits. Previous question adds context at half weight."""
    terms = [(t, 1.0) for t in set(tokenize(question))]
    terms += [(t, 0.5) for t in set(tokenize(previous_question)) - {t for t, _ in terms}]
    scores = []
    for i in range(len(_docs)):
        s = 0.0
        for t, w in terms:
            idf = _idf.get(t, math.log(1 + len(_docs)))
            body = _doc_tokens[i].get(t, 0)
            if body:
                s += w * idf * (1 + math.log(body))
            if t in _title_tokens[i]:
                s += w * idf * 4
        scores.append((s, i))
    scores.sort(key=lambda x: (-x[0], x[1]))
    return [i for s, i in scores[:TOP_K] if s > 0]


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"Could not read config.json: {e}"
    key = str(cfg.get("api_key", "")).strip()
    if not key or key == "PUT-YOUR-KEY-HERE":
        return None, "No API key yet. Put your Anthropic API key in config.json (project root), then ask again."
    return {"api_key": key, "model": cfg.get("model") or "claude-fable-5-1"}, None


def call_claude(cfg, messages):
    body = {
        "model": cfg["model"],
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": messages,
        "output_config": {"effort": "low"},
        "fallbacks": "default",
    }
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json",
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "server-side-fallback-2026-07-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        hint = {401: "The API key in config.json was rejected.",
                403: "The API key does not have access to this model.",
                404: f"Model '{cfg['model']}' was not found for this key.",
                429: "Rate limited by the API; try again in a moment.",
                529: "The API is overloaded; try again in a moment."}.get(e.code, "")
        return None, f"Anthropic API error {e.code}. {hint} {msg}".strip()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"Could not reach the Anthropic API: {getattr(e, 'reason', e)}"

    if data.get("stop_reason") == "refusal":
        return None, "The model declined to answer this question."
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        return None, f"The model returned no text (stop_reason: {data.get('stop_reason')})."
    return text, None


def parse_answer(text, offered):
    m = re.search(r"^\s*SOURCES:\s*(.*)$", text, flags=re.M | re.I)
    used = offered
    if m:
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        used = [n for n in nums if n in offered] if nums else []
        text = (text[: m.start()] + text[m.end():]).strip()
    return text, used


def handle_chat(payload):
    question = str(payload.get("question", "")).strip()[:2000]
    session = str(payload.get("session") or "")[:64] or uuid.uuid4().hex
    if not question:
        return 400, {"answer": "Ask a question first.", "nodes": [], "session": session, "error": "empty_question"}

    with _lock:
        history = list(_sessions.get(session, []))
    previous_q = next((m["question"] for m in reversed(history) if m["role"] == "user"), "")
    picked = score_notes(question, previous_q)

    cfg, err = load_config()
    if err:
        return 200, {"answer": err, "nodes": [], "session": session, "error": "config"}

    notes_block = "\n\n".join(
        f"[{i}] {_docs[i]['label']} (folder: {_docs[i]['group']})\n{_docs[i]['text'][:NOTE_CHARS]}"
        for i in picked) or "(No notes matched this question.)"
    user_content = f"<notes>\n{notes_block}\n</notes>\n\nQuestion: {question}"
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_content})

    text, err = call_claude(cfg, messages)
    if err:
        return 200, {"answer": err, "nodes": [], "session": session, "error": "api"}
    answer, used = parse_answer(text, picked)

    with _lock:
        turns = _sessions.get(session, []) + [
            {"role": "user", "content": user_content, "question": question},
            {"role": "assistant", "content": text},
        ]
        _sessions[session] = turns[-2 * MAX_EXCHANGES:]
        while len(_sessions) > MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
    return 200, {"answer": answer, "nodes": used, "session": session}


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] == "/chat/reset":
            self.do_reset()
            return
        if self.path.split("?")[0] != "/chat":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(min(length, 100_000)) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, UnicodeDecodeError) as e:
            self._json(400, {"answer": f"Bad request: {e}", "nodes": [], "error": "bad_request"})
            return
        try:
            status, obj = handle_chat(payload)
        except Exception as e:  # never crash the server on a chat request
            status, obj = 500, {"answer": f"Server error: {e}", "nodes": [], "error": "server"}
        self._json(status, obj)

    def do_reset(self):
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            payload = {}
        with _lock:
            _sessions.pop(str(payload.get("session", "")), None)
        self._json(200, {"ok": True})


def main():
    if not os.path.exists(INDEX_PATH):
        raise SystemExit("index.json missing — run: python3 build.py")
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump({"api_key": "PUT-YOUR-KEY-HERE", "model": "claude-fable-5-1"}, f, indent=2)
    load_index()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=VIEWER_DIR))
    print(f"Knowledge Galaxy: http://localhost:{PORT}  ({len(_docs)} notes, serving {VIEWER_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
