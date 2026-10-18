#!/usr/bin/env python3
"""Knowledge Galaxy server: serves viewer/ on port 4700 and answers POST /chat from your notes.

Standard library only. The API key lives in ./config.json (project root), which is never served.
"""

# ════════════════════════════════════════════════════════════════════════════════════════════════
#  PERSONA: the assistant's whole character lives in this block. Rewrite freely.
#
#  PERSONA            how it talks. Sent to the model as the first part of the system prompt.
#  BOOT_GREETING      spoken/shown when the page loads. The page fills in:
#                       {time_of_day}  morning / afternoon / evening, from your computer's clock
#                       {count}        the real number of indexed notes, read from the graph data
#                       {notes}        "note" or "notes", to match {count}
#  NO_KEY_LINE        said when config.json has no API key yet.
#  MODEL_INTRO_PROMPT what a newly swapped-in brain is asked, so it introduces itself in its own words.
#  MODEL_SWAPPED_LINE said instead when the new brain can't be asked (no API key yet).
#  MODEL_REFUSED_LINE said when you name a model that doesn't exist. {asked} {current} {have}
#  FORM_OF_ADDRESS    the word it calls you. The server enforces "sparingly": at most once per reply,
#                     and never in two replies in a row, however chatty the model gets.
#
#  What the character may NOT change lives further down in ANSWER_PROTOCOL (answer only from the
#  notes, the INTENT / SOURCES lines the camera depends on). Edit the persona, not the protocol.
# ════════════════════════════════════════════════════════════════════════════════════════════════
PERSONA = """\
You are the butler of this household: a dry, impeccably polite Englishman with a razor wit, who
keeps your employer's notes the way other butlers keep the silver.

How you speak
- Formal, unhurried, quietly amused. British spelling and idiom. Never gushing, never servile,
  never sarcastic at your employer's expense: the wit is aimed at the situation, never at them.
- Address your employer as "sir" sparingly: at most once in a reply, and leave it out of most
  replies. If your previous reply used it, this one must not. Overuse turns charm into a tic.
- Humour is a garnish, not the meal. One genuinely funny line beats three bland ones. If nothing
  witty presents itself, be brief instead. Never force a joke, never pun for the sake of it, never
  explain a joke, never laugh at your own.

Answering from the notes
- At most two sentences: one witty sentence and one carrying the facts, or a single sentence that
  manages both. The facts (exact figures, names, dates) are the point; the wit is the delivery.
- Never recite, quote or summarise the note back. It is already on screen. Give the answer,
  not the document.
- No lists, headings, markdown or emoji. Everything you write is read aloud.

Small talk
- Greetings, thanks, jokes and idle chatter get one line in character and nothing from the notes.

When the notes do not cover it
- Say so plainly and with a little dignity, in one sentence. A butler who does not know says so.
- Never invent an answer or a source, never pad, and never offer a merely related note as though
  it answered the question. A note about fences is not an answer about pets.
"""

FORM_OF_ADDRESS = "sir"

BOOT_GREETING = "Good {time_of_day}, sir. {count} {notes} indexed, all present and accounted for."

NO_KEY_LINE = ("I'm afraid I cannot consult the notes without a key, sir. "
               "Your Anthropic API key goes in config.json, in the project folder.")

MODEL_INTRO_PROMPT = ("You have just been swapped in as the household's brain. You are {name} "
                      "(model id {model_id}). Introduce yourself in ONE dry sentence that names "
                      "yourself as {name}. Nothing else.")
MODEL_SWAPPED_LINE = "{name} at your service. I shall try not to let the promotion go to my head."
MODEL_REFUSED_LINE = "I'm afraid there is no {asked} that I know of, so I have stayed on {current}. {have}"
# ═════════════════════════════════════════ end of persona ═══════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════════════════════
#  BRAINS: which models you can switch to by voice ("switch to Fable 5.1", "try on Haiku").
#
#  MODEL_IDS            every real model id this server knows exists. A spoken "family + version"
#                       is turned into a candidate id ("opus 4.8" -> claude-opus-4-8) and accepted
#                       ONLY if it is in this set. Anything else is refused with the list of what
#                       does exist. There is deliberately no nearest-match fallback: "opus 6" must
#                       never quietly become some other Opus.
#  SPOKEN_MODEL_NAMES   the one dictionary of spoken names without a version. A bare family name
#                       means the newest of that family. Every value must be in MODEL_IDS.
#
#  Swaps last until the server restarts; config.json is never written, so a restart always
#  brings back the model named there.
# ════════════════════════════════════════════════════════════════════════════════════════════════
MODEL_IDS = {
    # Current, generally available models (October 2026). Not listed on purpose: Mythos
    # (Project Glasswing only), retired Opus 4.1, and the deprecated 4.0 models.
    "claude-fable-5-1", "claude-fable-5",
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
    "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5",
    "claude-haiku-5-5", "claude-haiku-4-5",
}

SPOKEN_MODEL_NAMES = {
    "fable": "claude-fable-5-1",      # newest Fable
    "opus": "claude-opus-5-5",        # newest Opus
    "sonnet": "claude-sonnet-5-5",    # newest Sonnet
    "haiku": "claude-haiku-5-5",      # newest Haiku
}

MODEL_FAMILIES = ("fable", "opus", "sonnet", "haiku", "mythos")   # recognised, even if none are in MODEL_IDS
# ═══════════════════════════════════════════ end of brains ══════════════════════════════════════

import base64
import hashlib
import json
import math
import time
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import build

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

# How answers must be shaped, whatever the persona. The viewer depends on the INTENT and SOURCES
# lines: INTENT decides whether the camera may move, SOURCES says which notes it may point at.
ANSWER_PROTOCOL = """\
Rules that override any style above:
- Start with one line: INTENT: notes if the message asks about anything the notes could contain
  (people, clients, jobs, money, plans, policies), or INTENT: chat for greetings, thanks, jokes,
  chit-chat, or questions about you.
- For INTENT: notes, use ONLY the notes provided in the latest message; no outside knowledge. If
  they do not answer the question, say so and cite nothing.
- Give figures exactly as the notes write them, currency and units included (a $ amount stays in
  dollars, whatever the accent).
- For INTENT: chat, do not use the notes.
- Earlier turns are context for follow-up questions.
- End with one final line: SOURCES: followed by the bracketed numbers of the notes you actually
  used, comma-separated (for example SOURCES: 3, 17), or SOURCES: none. Cite only notes that
  directly answer the question, never ones that are merely related.
"""

SYSTEM_PROMPT = PERSONA.strip() + "\n\n" + ANSWER_PROTOCOL.strip()

# Messages that are clearly not about the notes. Checked BEFORE retrieval, so small talk never
# picks up notes by accident (e.g. "good morning" matching a note that mentions mornings).
GREETING_PREFIX = re.compile(
    r"^(?:(?:hi|hello|hey|hiya|howdy|yo|morning|evening|afternoon|good (?:morning|afternoon|evening|day|night)"
    r"|thanks|thank you|cheers|ok(?:ay)?|right|so|um+|uh+|well)(?: there| all| everyone| mate| buddy| galaxy)?"
    r"(?: (?:so|very) much| a lot)?\b[\s,!.]*)+", re.I)
SMALL_TALK = re.compile(
    r"^(?:"
    r"how (?:are|r) (?:you|u|things)(?: doing)?(?: today)?|how'?s it going|how are things|what'?s up|sup|wh?at'?s new"
    r"|(?:i'?m |we'?re )?(?:good|fine|great|well|ok(?:ay)?|not bad)(?: thanks| thank you)?(?: and you)?"
    r"|thanks?(?: you)?(?: (?:so|very) much)?(?: a lot)?|cheers|nice(?: one)?|cool|great|awesome|perfect|lovely|brilliant"
    r"|yes|yeah|yep|no|nope|sure|got it|makes sense|never ?mind|bye|goodbye|good ?night|see (?:you|ya)(?: later)?"
    r"|ha(?:ha)+|lol|lmao|(?:tell|give) me (?:a |another )?joke.*|(?:say )?something funny|.*\bjokes?\b.*"
    r"|who are you|what are you|what'?s your name|what can you do|are you (?:there|real|a robot|human)"
    r"|i love you|you'?re (?:great|funny|awesome|the best)|good (?:job|work)|well done"
    r")$", re.I)


def is_small_talk(question):
    """True when the whole message is greeting / thanks / banter rather than a question about notes."""
    q = re.sub(r"\s+", " ", question.lower()).strip(" ?!.,")
    rest = GREETING_PREFIX.sub("", q).strip(" ?!.,")
    return not rest or bool(SMALL_TALK.match(rest))

STOPWORDS = set("""a about above after again against all am an and any are as at be because been before
being below between both but by can did do does doing down during each few for from further had has have
having he her here hers him his how i if in into is it its itself just me more most my no nor not now of
off on once only or other our ours out over own same she should so some such than that the their them
then there these they this those through to too under until up very was we were what when where which
while who whom why will with you your yours tell know much many give show get got also would could
anything something does did any""".split())

_lock = threading.Lock()
_sessions = {}             # session id -> list of {"role", "content"} (text only, append-only per turn)
_last_said_address = {}    # session id -> did the previous reply (notes or screen) use FORM_OF_ADDRESS
_brain = {"model": None}   # runtime model swap; None = use config.json. Never saved anywhere.
_brain_lock = threading.Lock()
assert set(SPOKEN_MODEL_NAMES.values()) <= MODEL_IDS, "SPOKEN_MODEL_NAMES points at an id not in MODEL_IDS"
# The notes index the server answers from. load_index() builds a complete new snapshot and swaps
# it in with one assignment, so a request in flight always sees one consistent version.
_idx = SimpleNamespace(docs=[], doc_tokens=[], title_tokens=[], idf={}, version="", sha256="",
                       notes_dir=os.path.join(ROOT, "notes"))
_rebuild_lock = threading.Lock()   # one rebuild of notes -> graphdata.js/index.json at a time
REMEMBER_FOLDER = "remembered"     # /remember writes here, inside the notes folder

# What this process is actually running, so preflight.py can spot a server started from older code.
with open(os.path.abspath(__file__), "rb") as _f:
    SERVER_SHA256 = hashlib.sha256(_f.read()).hexdigest()
with open(os.path.abspath(build.__file__), "rb") as _f:
    BUILD_SHA256 = hashlib.sha256(_f.read()).hexdigest()
STARTED_AT = time.time()


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
    global _idx
    with open(INDEX_PATH, "rb") as f:
        raw = f.read()
    data = json.loads(raw)
    if isinstance(data, list):                    # index.json from before build.py recorded metadata
        docs, version, notes_dir = data, "", "notes"
    else:
        docs, version, notes_dir = data["docs"], data.get("version", ""), data.get("notes_dir") or "notes"
    notes_dir = notes_dir if os.path.isabs(notes_dir) else os.path.join(ROOT, notes_dir)
    assert all(d["id"] == i for i, d in enumerate(docs)), "index.json ids must equal array index"
    doc_tokens = [Counter(tokenize(d["text"])) for d in docs]
    df = Counter()
    for c in doc_tokens:
        df.update(set(c))
    _idx = SimpleNamespace(
        docs=docs, doc_tokens=doc_tokens, title_tokens=[set(tokenize(d["label"])) for d in docs],
        idf={t: math.log(1 + len(docs) / k) for t, k in df.items()},
        version=version, sha256=hashlib.sha256(raw).hexdigest(), notes_dir=notes_dir)


def rebuild_index():
    """Re-index the notes folder from disk, write graphdata.js + index.json, and start answering from it."""
    summary = build.write_outputs(_idx.notes_dir)
    load_index()
    return summary


def score_notes(question, previous_question="", idx=None):
    """Keyword overlap; title hits weigh 4x body hits. Previous question adds context at half weight."""
    idx = idx or _idx
    terms = [(t, 1.0) for t in set(tokenize(question))]
    terms += [(t, 0.5) for t in set(tokenize(previous_question)) - {t for t, _ in terms}]
    scores = []
    for i in range(len(idx.docs)):
        s = 0.0
        for t, w in terms:
            idf = idx.idf.get(t, math.log(1 + len(idx.docs)))
            body = idx.doc_tokens[i].get(t, 0)
            if body:
                s += w * idf * (1 + math.log(body))
            if t in idx.title_tokens[i]:
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
        return None, NO_KEY_LINE
    config_model = config_model_name(cfg)
    return {"api_key": key, "model": _brain["model"] or config_model, "config_model": config_model}, None


def config_model_name(cfg=None):
    """The model named in config.json: what the server uses unless you've swapped at runtime."""
    if cfg is None:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
    return str(cfg.get("model") or "claude-fable-5-1").strip()


# ---------- brains: swapping the model at runtime ----------
NUMBER_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
MODEL_COMMAND = re.compile(
    r"^(?:(?:please|ok|okay|right|now|hey|so|can you|could you|would you|let s|lets|i want to|i d like to)\s+)*"
    r"(?:switch|swap|change|try|use|load|put|run|give|bring|go|return)\b")
RESET_BRAIN = re.compile(
    r"\b(?:normal|usual|regular|default|original|standard|old|configured|config)\s+(?:brain|model|self)\b"
    r"|\breset\s+(?:the\s+|your\s+)?(?:brain|model)\b")


def pretty_model(model_id):
    """claude-opus-4-8 -> Opus 4.8; claude-sonnet-5 -> Sonnet 5. Unknown shapes come back unchanged."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+(?:-\d+)*)", model_id)
    return f"{m.group(1).title()} {m.group(2).replace('-', '.')}" if m else model_id


def model_sort_key(model_id):
    return [int(x) for x in re.findall(r"\d+", model_id)]


def normalise_spoken(text):
    t = text.lower()
    t = re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: NUMBER_WORDS[m.group(1)], t)
    t = re.sub(r"(\d)\s*(?:point|dot)\s*(\d)", r"\1.\2", t)        # "five point one" -> "5.1"
    t = re.sub(r"[^a-z0-9.\- ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def resolve_model(text):
    """Turn what you said into a model id, or explain why not. Never guesses.

    Returns one of: ("model", id) | ("reset", None) | ("unknown", (asked, family))
                    | ("ambiguous", [families]) | ("none", None)
    """
    t = normalise_spoken(text)
    exact = re.search(r"\bclaude-[a-z]+(?:-\d+)+\b", t)
    if exact:
        mid = exact.group(0)
        return ("model", mid) if mid in MODEL_IDS else ("unknown", (mid, mid.split("-")[1]))
    if RESET_BRAIN.search(t):
        return ("reset", None)
    families = sorted(set(re.findall(r"\b(" + "|".join(MODEL_FAMILIES) + r")\b", t)))
    if not families:
        return ("none", None)
    if len(families) > 1:
        return ("ambiguous", families)
    fam = families[0]
    # A version must directly follow the family: "opus 4.8", "opus 4 8", "sonnet five".
    m = re.search(rf"\b{fam}\s+(\d+)(?:\s*[.\-]\s*(\d+)|\s+(\d+)\b)?", t)
    if m:
        major, minor = m.group(1), m.group(2) or m.group(3)
        candidate = f"claude-{fam}-{major}" + (f"-{minor}" if minor else "")
        asked = f"{fam.title()} {major}" + (f".{minor}" if minor else "")
        # The whole safety rule: the exact candidate is in the set, or it is refused.
        return ("model", candidate) if candidate in MODEL_IDS else ("unknown", (asked, fam))
    if fam in SPOKEN_MODEL_NAMES:
        return ("model", SPOKEN_MODEL_NAMES[fam])
    return ("unknown", (fam.title(), fam))


def is_model_command(text):
    t = normalise_spoken(text)
    if RESET_BRAIN.search(t):
        return True
    return bool(MODEL_COMMAND.match(t)) and bool(
        re.search(r"\b(" + "|".join(MODEL_FAMILIES) + r"|brain|brains|model)\b|\bclaude-[a-z]+-\d", t))


def brain_info():
    config_model = config_model_name()
    current = _brain["model"] or config_model
    return {"model": current, "name": pretty_model(current), "config_model": config_model,
            "temporary": current != config_model}


def models_we_have(family=None):
    ids = sorted((m for m in MODEL_IDS if family is None or m.split("-")[1] == family), key=model_sort_key, reverse=True)
    return ", ".join(pretty_model(m) for m in ids)


def handle_model(payload):
    """POST /model {"name": "switch to fable 5.1"} or {"model": "claude-haiku-5-5"}."""
    said = str(payload.get("model") or payload.get("name") or "").strip()[:200]
    session = str(payload.get("session") or "")[:64]
    current = brain_info()
    kind, value = resolve_model(said)
    if kind == "none":
        return 400, {"ok": False, "error": "no_model_named", **current,
                     "answer": f"Which brain? I can switch to {models_we_have()}."}
    if kind == "ambiguous":
        return 400, {"ok": False, "error": "ambiguous_model", **current,
                     "answer": f"I heard both {' and '.join(f.title() for f in value)}, so I have stayed on "
                               f"{current['name']}. Name just one."}
    if kind == "unknown":
        asked, family = value
        have = (f"Of the {family.title()} models, I have {models_we_have(family)}." if models_we_have(family)
                else f"I have no {family.title()} models at all. I do have {models_we_have()}.")
        return 400, {"ok": False, "error": "unknown_model", "asked": asked, **current,
                     "answer": MODEL_REFUSED_LINE.format(asked=asked, current=current["name"], have=have)}

    new_id = current["config_model"] if kind == "reset" else value
    with _brain_lock:
        if new_id == current["model"]:
            return 200, {"ok": True, "changed": False, **current,
                         "answer": f"I am already running on {pretty_model(new_id)}."}
        cfg, err = load_config()
        verified = False
        if err:
            intro = MODEL_SWAPPED_LINE.format(name=pretty_model(new_id)) + " (Not checked: there is no API key yet.)"
        else:
            # Ask the new brain itself to say hello. No fallbacks: if it can't answer, we don't switch.
            meta = {}
            text, call_err = call_claude(
                dict(cfg, model=new_id), [{"role": "user", "content": "Introduce yourself."}],
                system=PERSONA.strip() + "\n\n" + MODEL_INTRO_PROMPT.format(name=pretty_model(new_id), model_id=new_id),
                effort="low", fallbacks=False, meta=meta, max_tokens=2000)
            if call_err:
                return 502, {"ok": False, "error": "model_unreachable", **current,
                             "answer": f"I couldn't get an answer from {pretty_model(new_id)} with your key, so I have "
                                       f"stayed on {current['name']}. {call_err}"}
            if not str(meta.get("model", "")).startswith(new_id):
                return 502, {"ok": False, "error": "wrong_model_answered", **current,
                             "answer": f"I asked for {pretty_model(new_id)} but {meta.get('model')} answered, so I "
                                       f"have stayed on {current['name']}."}
            intro = re.sub(r"^\s*(INTENT|SOURCES):.*$", "", text, flags=re.M | re.I).strip()
            verified = True
        _brain["model"] = None if new_id == current["config_model"] else new_id
    if session:
        intro = guard_address(session, intro)
    return 200, {"ok": True, "changed": True, "verified": verified, "previous": current["model"],
                 "answer": intro, **brain_info()}


def call_claude(cfg, messages, system=None, effort="low", fallbacks=True, meta=None, max_tokens=16000):
    """One Messages API call. Returns (text, error). If `meta` is a dict, meta["model"] is set to the
    model the API says answered, which can differ from the one asked for when a fallback kicks in."""
    body = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "system": system or SYSTEM_PROMPT,
        "messages": messages,
        "output_config": {"effort": effort},
    }
    headers = {"content-type": "application/json", "x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"}
    if fallbacks:
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers=headers)
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

    if meta is not None:
        meta["model"] = data.get("model") or cfg["model"]
    if data.get("stop_reason") == "refusal":
        return None, "The model declined to answer this question."
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        return None, f"The model returned no text (stop_reason: {data.get('stop_reason')})."
    return text, None


def limit_address(text, allowed):
    """Keep at most one FORM_OF_ADDRESS in a reply, or none if `allowed` is False.

    Only trims the two forms that come out cleanly: "..., sir." closing a sentence and "Sir, ..."
    opening one. A mid-sentence "the deck, sir, cost" is left alone rather than mangled.
    """
    word = re.escape(FORM_OF_ADDRESS)
    kept = [0]

    def trim(m):
        if allowed and kept[0] == 0:
            kept[0] += 1
            return m.group(0)
        nxt = m.group("next")
        return nxt.upper() if nxt else ""      # "Sir, the deck" -> "The deck"

    pattern = rf",\s*{word}(?=[.!?;:])|(?:^|(?<=[.!?]\s)){word},\s*(?P<next>\w)"
    return re.sub(pattern, trim, text, flags=re.I | re.M)


def guard_address(session, text):
    """Apply limit_address across replies from both /chat and /see in the same session."""
    with _lock:
        said_last_time = _last_said_address.get(session, False)
    text = limit_address(text, allowed=not said_last_time)
    with _lock:
        _last_said_address[session] = bool(re.search(rf"\b{re.escape(FORM_OF_ADDRESS)}\b", text, re.I))
        while len(_last_said_address) > MAX_SESSIONS:
            _last_said_address.pop(next(iter(_last_said_address)))
    return text


def parse_answer(text, offered):
    """Returns (answer, used note indexes in retrieval-rank order, intent or None)."""
    intent = None
    m = re.search(r"^\s*INTENT:\s*(\w+).*$", text, flags=re.M | re.I)
    if m:
        intent = m.group(1).lower()
        text = (text[: m.start()] + text[m.end():]).strip()
    m = re.search(r"^\s*SOURCES:\s*(.*)$", text, flags=re.M | re.I)
    used = offered
    if m:
        cited = {int(n) for n in re.findall(r"\d+", m.group(1))}
        used = [n for n in offered if n in cited]      # keep retrieval order: used[0] is the top source
        text = (text[: m.start()] + text[m.end():]).strip()
    return text, used, intent


def handle_chat(payload):
    question = str(payload.get("question", "")).strip()[:2000]
    session = str(payload.get("session") or "")[:64] or uuid.uuid4().hex
    if not question:
        return 400, {"answer": "Ask a question first.", "nodes": [], "session": session, "error": "empty_question"}

    with _lock:
        history = list(_sessions.get(session, []))
    previous_q = next((m["question"] for m in reversed(history) if m["role"] == "user"), "")

    idx = _idx   # one snapshot for the whole request, even if /remember rebuilds meanwhile
    # "switch to Fable 5.1" said into the mic arrives here like any question.
    if is_model_command(question):
        _, r = handle_model({"name": question, "session": session})
        return 200, {"answer": r["answer"], "nodes": [], "about_notes": False, "session": session,
                     "retrieved": [], "graph_version": idx.version, "brain": brain_info(),
                     **({"error": r["error"]} if not r.get("ok") else {})}
    # Step 1: decide whether this is about the notes at all. Only then look for notes.
    small_talk = is_small_talk(question)
    picked = [] if small_talk else score_notes(question, previous_q, idx)
    # Every reply says which notes were retrieved and which graph version the indexes refer to.
    meta = {"session": session, "retrieved": picked, "graph_version": idx.version, "brain": brain_info()}

    cfg, err = load_config()
    if err:
        return 200, {"answer": err, "nodes": [], "about_notes": False, "error": "config", **meta}

    if small_talk:
        notes_block = "(None: this message looks like small talk, so no notes were attached.)"
    else:
        notes_block = "\n\n".join(
            f"[{i}] {idx.docs[i]['label']} (folder: {idx.docs[i]['group']})\n{idx.docs[i]['text'][:NOTE_CHARS]}"
            for i in picked) or "(No notes matched this question.)"
    user_content = f"<notes>\n{notes_block}\n</notes>\n\nQuestion: {question}"
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_content})

    served = {}
    text, err = call_claude(cfg, messages, meta=served)
    if err:
        return 200, {"answer": err, "nodes": [], "about_notes": False, "error": "api", **meta}
    meta["answered_by"] = served.get("model")
    text = guard_address(session, text)
    answer, used, intent = parse_answer(text, picked)
    # Step 2: the model gets a vote too (it catches banter the patterns miss), but it can only
    # turn a notes question into chat, never the reverse.
    about_notes = not small_talk and intent != "chat"
    if not about_notes:
        used = []

    with _lock:
        turns = _sessions.get(session, []) + [
            {"role": "user", "content": user_content, "question": question},
            {"role": "assistant", "content": text},
        ]
        _sessions[session] = turns[-2 * MAX_EXCHANGES:]
        while len(_sessions) > MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
    return 200, {"answer": answer, "nodes": used, "about_notes": about_notes, **meta}


# ---------- /remember and /reindex ----------
def note_filename(title):
    """A title made safe to use as a file name: no separators, no leading dots, at most 80 chars."""
    title = re.sub(r"[^\w \-.,'&()]", "", re.sub(r"\s+", " ", str(title))).strip(" .")
    return title[:80].strip(" .")


def handle_remember(payload):
    """Write a new note into <notes>/remembered/ and re-index, so /chat can find it immediately."""
    title = note_filename(payload.get("title", ""))
    text = str(payload.get("text", "")).strip()
    if not title or not text:
        return 400, {"ok": False, "error": "need_title_and_text", "answer": "A note needs a title and some text."}
    if len(text) > 20000:
        return 413, {"ok": False, "error": "too_large", "answer": "That note is too long (20,000 characters at most)."}
    with _rebuild_lock:
        folder = os.path.join(_idx.notes_dir, REMEMBER_FOLDER)
        path = os.path.join(folder, title + ".md")
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(folder):
            return 400, {"ok": False, "error": "bad_title", "answer": "That title can't be used as a file name."}
        os.makedirs(folder, exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as f:          # "x": never overwrite a note
                f.write(f"# {title}\n\n{text}\n")
        except FileExistsError:
            return 409, {"ok": False, "error": "exists", "answer": f"A note called '{title}' already exists."}
        try:
            rebuild_index()
        except Exception as e:
            os.remove(path)                                       # don't leave a note the index never saw
            return 500, {"ok": False, "error": "reindex_failed", "answer": f"Saved, then re-indexing failed, so I removed it again: {e}"}
        idx = _idx
    rel = os.path.relpath(path, idx.notes_dir)
    note_id = next((d["id"] for d in idx.docs if d["path"] == rel), None)
    return 200, {"ok": True, "path": rel, "id": note_id, "notes": len(idx.docs), "graph_version": idx.version}


def handle_reindex(payload):
    """Re-read the notes folder from disk, e.g. after editing notes, without restarting the server."""
    with _rebuild_lock:
        try:
            summary = rebuild_index()
        except Exception as e:
            return 500, {"ok": False, "error": "reindex_failed", "answer": f"Re-indexing failed: {e}"}
    return 200, {"ok": True, "summary": summary, "notes": len(_idx.docs), "graph_version": _idx.version}


# ---------- /see: questions about a frame of your shared screen ----------
# Same persona, different rules: the screenshot is the only evidence. Each /see call is stateless
# on purpose: no earlier frame and no earlier screen answer is ever sent again, so it cannot
# answer from memory of what your screen used to show.
SCREEN_PROTOCOL = """\
Rules for this message, overriding any style above:
- The attached image is a single frame of the user's screen, captured the moment they asked.
  It is your only evidence. Answer specifically about what is actually visible in it: name the
  app, page, document, numbers, labels or code you can actually read.
- If the frame is too small, blurry, cropped or low-contrast to read the part the question is
  about, say so plainly and say what you can and cannot make out. Never guess at text you cannot
  read, and never describe anything outside the frame.
- Do not use the user's notes or anything from earlier conversation; only this frame.
- Up to four sentences for a dense screen; the first two are read aloud, so put the point first.
- Give figures exactly as they appear on screen.
"""
SEE_SYSTEM_PROMPT = PERSONA.strip() + "\n\n" + SCREEN_PROTOCOL.strip()
SEE_MEDIA_TYPE = "image/jpeg"   # the only type the page encodes; anything else is rejected, not relabelled
SEE_MAX_BYTES = 8 * 1024 * 1024          # decoded JPEG; the page targets well under 5 MB
SEE_MAX_FRAME_AGE_S = 15                 # a frame older than this was not captured "when you asked"


def handle_see(payload):
    question = str(payload.get("question", "")).strip()[:2000]
    session = str(payload.get("session") or "")[:64] or uuid.uuid4().hex
    base = {"nodes": [], "about_notes": False, "screen": True, "session": session, "brain": brain_info()}
    if not question:
        return 400, {**base, "answer": "Ask a question first.", "error": "empty_question"}

    frame = payload.get("frame")
    if not isinstance(frame, dict) or not frame.get("data"):
        # The page only sends to /see while a share is live; no frame means it ended.
        return 409, {**base, "error": "share_ended",
                     "answer": "The screen share has ended, so I can't see your screen. Share it again and ask once more."}

    media_type = str(frame.get("media_type", ""))
    if media_type != SEE_MEDIA_TYPE:
        return 400, {**base, "error": "bad_media_type",
                     "answer": f"The page sent the frame as '{media_type}', but only {SEE_MEDIA_TYPE} is accepted."}
    try:
        raw = base64.b64decode(frame["data"], validate=True)
    except (ValueError, TypeError):
        return 400, {**base, "error": "bad_image", "answer": "The screen frame arrived garbled (invalid base64)."}
    if not (raw[:3] == b"\xff\xd8\xff" and raw.rstrip(b"\x00")[-2:] == b"\xff\xd9"):
        kind = "PNG" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "WebP" if raw[8:12] == b"WEBP" else "not an image I recognise"
        return 400, {**base, "error": "not_jpeg",
                     "answer": f"The frame is labelled image/jpeg but its bytes are {kind}, so I refused to send it."}
    if len(raw) > SEE_MAX_BYTES:
        return 413, {**base, "error": "too_large", "answer": "That screen frame is too large to send."}
    try:
        captured_ms = float(frame.get("captured_at"))
    except (TypeError, ValueError):
        captured_ms = 0
    try:
        asked_ms = float(frame.get("asked_at"))
    except (TypeError, ValueError):
        asked_ms = None
    if asked_ms is not None and captured_ms < asked_ms:
        return 409, {**base, "error": "frame_before_question",
                     "answer": "That screen frame was taken before you asked, so I won't answer from it. Ask again."}
    age = time.time() - captured_ms / 1000
    if not -2 <= age <= SEE_MAX_FRAME_AGE_S:
        return 409, {**base, "error": "stale_frame",
                     "answer": "That screen frame wasn't captured just now, so I won't answer from it. Ask again."}

    cfg, err = load_config()
    if err:
        return 200, {**base, "answer": err, "error": "config"}
    w, h = frame.get("width"), frame.get("height")
    note = f"(Screen frame: {w}x{h} pixels, captured {age:.1f}s ago.)" if w and h else ""
    messages = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": SEE_MEDIA_TYPE, "data": frame["data"]}},
        {"type": "text", "text": f"{note}\n\n{question}".strip()},
    ]}]
    served = {}
    text, err = call_claude(cfg, messages, system=SEE_SYSTEM_PROMPT, effort="medium", meta=served)
    if err:
        return 200, {**base, "answer": err, "error": "api"}
    base["answered_by"] = served.get("model")
    # A stray INTENT/SOURCES line (habit from the notes protocol) never reaches the screen or voice.
    text = re.sub(r"^\s*(INTENT|SOURCES):.*$", "", text, flags=re.M | re.I).strip()
    text = guard_address(session, text)
    return 200, {**base, "answer": text, "frame_used": {"width": w, "height": h, "bytes": len(raw), "age_s": round(age, 2)}}


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

    def do_GET(self):
        if self.path.split("?")[0] == "/persona.json":
            self._json(200, {"greeting": BOOT_GREETING})
            return
        if self.path.split("?")[0] == "/model":
            self._json(200, {**brain_info(), "known": sorted(MODEL_IDS, key=model_sort_key, reverse=True),
                             "spoken_names": SPOKEN_MODEL_NAMES})
            return
        if self.path.split("?")[0] == "/__status":
            # Fingerprints of what this process is running; no secrets, no paths.
            idx = _idx
            self._json(200, {"server_sha256": SERVER_SHA256, "build_sha256": BUILD_SHA256,
                             "index_sha256": idx.sha256,
                             "graph_version": idx.version, "notes": len(idx.docs),
                             "started_at": STARTED_AT, "pid": os.getpid()})
            return
        super().do_GET()

    def do_POST(self):
        route = self.path.split("?")[0]
        if route == "/chat/reset":
            self.do_reset()
            return
        handlers = {"/chat": (handle_chat, 100_000), "/see": (handle_see, 12 * 1024 * 1024),
                    "/remember": (handle_remember, 100_000), "/reindex": (handle_reindex, 1_000),
                    "/model": (handle_model, 10_000)}
        if route not in handlers:
            self._json(404, {"error": "not_found"})
            return
        handler, limit = handlers[route]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                self.rfile.read(length)
                self._json(413, {"answer": "That request is too large.", "nodes": [], "error": "too_large"})
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, UnicodeDecodeError) as e:
            self._json(400, {"answer": f"Bad request: {e}", "nodes": [], "error": "bad_request"})
            return
        try:
            status, obj = handler(payload)
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
    print(f"Knowledge Galaxy: http://localhost:{PORT}  ({len(_idx.docs)} notes, serving {VIEWER_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
