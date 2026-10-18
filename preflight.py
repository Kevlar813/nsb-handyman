#!/usr/bin/env python3
"""Preflight: run every live chain of the Knowledge Galaxy end to end, against the running system.

No mocks. Every check talks to the real server over HTTP, or to the real Anthropic API, or reads the
real files on disk. The failures that hurt are the ones where every unit test passes and a real
chain is dead, so that is what this looks for.

    python3 preflight.py                     # check the server at http://127.0.0.1:4700
    python3 preflight.py --url http://127.0.0.1:4701
    python3 preflight.py --only 7,8          # run just some checks (by number)

Exit status is 0 only when nothing failed. Warnings don't fail the run; they are things preflight
could not verify (usually because an earlier check failed) or that are worth a look.

Side effects: the /remember check writes one clearly named note into <notes>/remembered/, asks
/chat about it, then deletes exactly that file and re-indexes. Nothing else is written.
Standard library only.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VIEWER_DIR = os.path.join(ROOT, "viewer")
CONFIG_PATH = os.path.join(ROOT, "config.json")
INDEX_PATH = os.path.join(ROOT, "index.json")
CANARY_JPEG = os.path.join(ROOT, "preflight_canary.jpg")   # a real JPEG reading "PREFLIGHT 4471"
API = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
PLACEHOLDER_KEY = "PUT-YOUR-KEY-HERE"

PASS, FAIL, WARN = "pass", "fail", "warn"
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
MARK = {PASS: ("✓", "32"), FAIL: ("✗", "31"), WARN: ("⚠", "33")}


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


class Result:
    def __init__(self, status, summary, details=()):
        self.status, self.summary, self.details = status, summary, list(details)


# ----------------------------------------------------------------------------------------------
# Plumbing: raw HTTP to the server (exact paths, nothing normalised) and to the Anthropic API.
# ----------------------------------------------------------------------------------------------
class Server:
    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.url, self.host, self.port = url.rstrip("/"), u.hostname, u.port or 80

    def request(self, method, path, body=None, timeout=30):
        """Returns (status, headers dict lower-cased, body bytes). Raises OSError if unreachable."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if data is not None else {}
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def get(self, path, timeout=30):
        return self.request("GET", path, timeout=timeout)

    def post_json(self, path, body, timeout=180):
        status, headers, raw = self.request("POST", path, body, timeout=timeout)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"_unparseable": raw[:200].decode("utf-8", "replace")}


def anthropic(method, path, key, body=None, timeout=120):
    """One real call to the Anthropic API. Returns (status, parsed JSON or {})."""
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-api-key": key, "anthropic-version": API_VERSION,
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {}


def api_error(data):
    return (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return str(cfg.get("api_key", "")).strip(), str(cfg.get("model", "")).strip()


def key_hint(key):
    return f"key ending …{key[-4:]}" if len(key) > 8 else "key"


def parse_graph(js_bytes):
    text = js_bytes.decode("utf-8")
    m = re.search(r"const GRAPH = (\{.*\});\s*$", text, flags=re.S)
    if not m:
        raise ValueError("no 'const GRAPH = {...};' in graphdata.js")
    return json.loads(m.group(1))


def notes_dir_from_index():
    with open(INDEX_PATH, encoding="utf-8") as f:
        data = json.load(f)
    nd = "notes" if isinstance(data, list) else (data.get("notes_dir") or "notes")
    return nd if os.path.isabs(nd) else os.path.join(ROOT, nd)


# ----------------------------------------------------------------------------------------------
# The checks. Each takes the shared state dict and returns a Result. A check that cannot run
# because an earlier check already failed returns WARN "not run", so one root cause is reported
# as one failure, not five.
# ----------------------------------------------------------------------------------------------
def check_server_up(s, st):
    try:
        status, headers, body = s.get("/", timeout=10)
    except OSError as e:
        return Result(FAIL, f"nothing answering at {s.url}", [f"{e}", "Start it with: python3 server.py"])
    # Something answered. The served-files and config.json checks run from here on regardless:
    # a server serving the wrong folder is exactly when config.json leaks.
    st["reachable"] = True
    if status != 200:
        return Result(FAIL, f"GET / returned HTTP {status}")
    html = body.decode("utf-8", "replace")
    missing = [m for m in ("<title>Knowledge Galaxy</title>", 'src="graphdata.js"') if m not in html]
    if missing:
        return Result(FAIL, "GET / is not the Knowledge Galaxy viewer", [f"missing: {', '.join(missing)}"])
    st["server_up"] = True
    return Result(PASS, f"{s.url}/ serves the viewer ({len(body) // 1024} KB)")


def check_graph(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    status, _, body = s.get("/graphdata.js")
    if status != 200:
        return Result(FAIL, f"GET /graphdata.js returned HTTP {status}", ["Run: python3 build.py"])
    try:
        graph = parse_graph(body)
    except ValueError as e:
        return Result(FAIL, "graphdata.js does not parse", [str(e)])
    nodes, links = graph.get("nodes", []), graph.get("links", [])
    if not nodes:
        return Result(FAIL, "graph has 0 notes", ["Is the notes folder empty? Run: python3 build.py <notes folder>"])
    bad_ids = [i for i, n in enumerate(nodes) if n.get("id") != i]
    bad_links = [l for l in links if not (0 <= l.get("source", -1) < len(nodes) and 0 <= l.get("target", -1) < len(nodes))]
    if bad_ids or bad_links:
        return Result(FAIL, "graph data is inconsistent",
                      [f"{len(bad_ids)} nodes whose id != index", f"{len(bad_links)} links to missing nodes"])
    st["graph"] = graph
    extra = [] if graph.get("version") else ["graphdata.js has no version stamp (built by an older build.py)"]
    return Result(PASS if not extra else WARN,
                  f"{len(nodes)} notes, {len(links)} links, version {graph.get('version', '?')}", extra)


def check_chat(s, st):
    if not st.get("graph"):
        return Result(WARN, "not run: no graph data")
    nodes = st["graph"]["nodes"]
    hub = max(nodes, key=lambda n: (n.get("degree", 0), -n["id"]))
    question = f"What do my notes say about {hub['label']}?"
    t0 = time.time()
    status, data = s.post_json("/chat", {"question": question, "session": f"preflight-{int(t0)}"})
    took = time.time() - t0
    details = [f"asked: “{question}” ({took:.1f}s)"]
    if status != 200 or "_unparseable" in data:
        return Result(FAIL, f"/chat returned HTTP {status}", details + [str(data)[:200]])
    answer, ids = data.get("answer"), data.get("nodes")
    if not isinstance(answer, str) or not isinstance(ids, list):
        return Result(FAIL, "/chat reply is missing 'answer' or 'nodes'", details + [str(data)[:200]])
    details.append(f"answer: “{answer[:140]}{'…' if len(answer) > 140 else ''}”")
    if data.get("error") == "config":
        return Result(FAIL, "/chat cannot answer: config.json has no usable API key (see check 4)", details)
    if data.get("error"):
        return Result(FAIL, f"/chat answered with error '{data['error']}'", details)
    if not answer.strip():
        return Result(FAIL, "/chat returned an empty answer", details)
    bad = [i for i in ids if not isinstance(i, int) or not 0 <= i < len(nodes)]
    if bad:
        return Result(FAIL, f"/chat cited note indexes that don't exist: {bad}", details)
    details.append("cited: " + (", ".join(nodes[i]["label"] for i in ids) or "nothing"))
    if data.get("graph_version") and data["graph_version"] != st["graph"].get("version"):
        return Result(FAIL, "/chat answered from a different graph version than the browser is served", details)
    return Result(PASS, f"real answer, nodes {ids}", details)


def check_key(s, st):
    try:
        key, model = read_config()
    except (OSError, ValueError) as e:
        return Result(FAIL, "config.json can't be read", [str(e)])
    st["model"] = model
    if not key or key == PLACEHOLDER_KEY:
        return Result(FAIL, "config.json still has the placeholder key",
                      ['Put your Anthropic API key in config.json: {"api_key": "sk-ant-...", ...}'])
    try:
        status, data = anthropic("GET", "/v1/models?limit=1", key, timeout=30)
    except OSError as e:
        return Result(FAIL, f"could not reach {API}", [str(e)])
    if status == 200:
        st["key"] = key
        return Result(PASS, f"Anthropic accepted the {key_hint(key)} (GET /v1/models)")
    reason = {401: "rejected: invalid key", 403: "key not permitted"}.get(status, f"HTTP {status}")
    return Result(FAIL, f"Anthropic {reason}", [api_error(data)] if api_error(data) else [])


def check_model(s, st):
    model = st.get("model")
    if not model:
        return Result(FAIL, "config.json names no model")
    if not st.get("key"):
        return Result(WARN, f"not run: '{model}' can only be checked with a valid key (check 4)")
    t0 = time.time()
    try:
        status, data = anthropic("POST", "/v1/messages", st["key"], {
            "model": model, "max_tokens": 256, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}]})
    except OSError as e:
        return Result(FAIL, f"could not reach {API}", [str(e)])
    if status != 200:
        hint = {404: "the key can't see this model", 403: "the key isn't allowed to use this model"}.get(status, "")
        return Result(FAIL, f"'{model}' returned HTTP {status}" + (f": {hint}" if hint else ""),
                      [api_error(data)] if api_error(data) else [])
    served = data.get("model", "")
    if served and not served.startswith(model):
        return Result(FAIL, f"asked for '{model}' but '{served}' answered")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    return Result(PASS, f"'{model}' answered in {time.time() - t0:.1f}s (stop_reason {data.get('stop_reason')})",
                  [f"reply: “{text[:60]}”"] if text else [])


def check_remember(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    code = f"ZEBRA-{int(time.time()) % 10000:04d}"
    title = f"Preflight canary {stamp}"
    status, data = s.post_json("/remember", {
        "title": title,
        "text": f"The preflight canary code is {code}. This note was written by preflight.py and "
                f"is deleted again as soon as the check finishes."})
    if status == 404:
        return Result(FAIL, "the server has no /remember endpoint (is it running old code? see check 9)")
    if status != 200 or not data.get("ok"):
        return Result(FAIL, f"/remember returned HTTP {status}", [str(data.get("answer") or data)[:200]])
    notes_dir = notes_dir_from_index()
    path = os.path.join(notes_dir, data.get("path", ""))
    details = [f"wrote {os.path.relpath(path, ROOT)} as note #{data.get('id')}"]
    result = None
    try:
        # 1. A real file, with the text we sent.
        try:
            with open(path, encoding="utf-8") as f:
                on_disk = f.read()
        except OSError:
            return Result(FAIL, "/remember said ok but no file is on disk", details)
        if code not in on_disk:
            return Result(FAIL, "the file on disk doesn't contain the note's text", details)
        # 2. Retrievable by /chat straight away: no rebuild, no restart.
        status, chat = s.post_json("/chat", {"question": "What is the preflight canary code?",
                                             "session": f"preflight-remember-{stamp}"})
        retrieved, cited = chat.get("retrieved"), chat.get("nodes") or []
        if retrieved is None:
            return Result(FAIL, "/chat doesn't report what it retrieved (server running old code?)", details)
        if data.get("id") not in retrieved:
            return Result(FAIL, "/chat did not retrieve the new note", details + [f"retrieved: {retrieved}"])
        details.append(f"/chat retrieved it immediately (retrieved {retrieved})")
        if chat.get("error") == "config":
            result = Result(WARN, "file written and retrieved by /chat; answer not checked (no valid key)", details)
        elif chat.get("error"):
            result = Result(FAIL, f"retrieved, but /chat failed: {chat.get('answer', '')[:120]}", details)
        elif code in chat.get("answer", "") and data.get("id") in cited:
            details.append(f"answer: “{chat['answer'][:120]}”")
            result = Result(PASS, f"file written, /chat answered {code} from it", details)
        else:
            result = Result(WARN, "retrieved, but the answer didn't use the new note",
                            details + [f"answer: “{chat.get('answer', '')[:120]}”", f"cited: {cited}"])
        return result
    finally:
        # Delete exactly the file this check created, then re-index so the server forgets it.
        expected_dir = os.path.join(notes_dir, "remembered")
        safe = (os.path.dirname(os.path.abspath(path)) == os.path.abspath(expected_dir)
                and os.path.basename(path) == f"{title}.md")
        if safe and os.path.exists(path):
            os.remove(path)
            try:
                os.rmdir(expected_dir)          # only succeeds if the folder is now empty
            except OSError:
                pass
        rs, rd = s.post_json("/reindex", {})
        if not safe or os.path.exists(path) or rs != 200:
            # Surface a cleanup problem loudly, whatever the check itself concluded.
            st.setdefault("cleanup_problems", []).append(
                f"canary note may be left behind at {path} (reindex HTTP {rs})")


def check_served_files(s, st):
    if not st.get("reachable"):
        return Result(WARN, "not run: nothing is answering at that address")
    stale, missing, checked, details = [], [], 0, []
    for dirpath, dirnames, filenames in os.walk(VIEWER_DIR):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or name.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, VIEWER_DIR).replace(os.sep, "/")
            with open(full, "rb") as f:
                disk = f.read()
            status, headers, served = s.get("/" + urllib.parse.quote(rel))
            checked += 1
            if status != 200:
                missing.append(f"{rel}: HTTP {status}")
            elif sha256(served) != sha256(disk):
                stale.append(f"{rel}: served {len(served):,} bytes, disk has {len(disk):,} (contents differ)")
            if rel in ("index.html", "graphdata.js"):
                cc = headers.get("cache-control", "")
                if "no-store" not in cc and "no-cache" not in cc:
                    details.append(f"{rel} is served without Cache-Control: no-store; a browser may show an old copy")
    if stale or missing:
        return Result(FAIL, f"{len(stale) + len(missing)} of {checked} files the browser gets differ from disk",
                      stale + missing + ["Restart the server, or check it is serving this project's viewer/ folder"])
    return Result(WARN if details else PASS, f"all {checked} files in viewer/ are served byte-identical to disk", details)


CONFIG_PROBES = ["/config.json", "/../config.json", "/%2e%2e/config.json", "/%2E%2E%2Fconfig.json",
                 "/..%2fconfig.json", "/..%5cconfig.json", "/./../config.json", "//config.json",
                 "/viewer/../config.json", "/graphdata.js/../../config.json", "/%2e%2e%2f%2e%2e%2fconfig.json"]


def check_config_hidden(s, st):
    if not st.get("reachable"):
        return Result(WARN, "not run: nothing is answering at that address")
    try:
        key, _ = read_config()
    except (OSError, ValueError):
        key = ""
    leaks, refused = [], []
    for path in CONFIG_PROBES:
        try:
            status, _, body = s.get(path, timeout=10)
        except OSError:
            continue
        if status == 200 and (b'"api_key"' in body or (key and key.encode() in body)):
            leaks.append(path)
        else:
            refused.append(status)
    if leaks:
        st["leak"] = [f"{s.url}{p}" for p in leaks]
        return Result(FAIL, f"config.json IS READABLE FROM THE BROWSER via {len(leaks)} path(s)", st["leak"])
    return Result(PASS, f"config.json not reachable ({len(CONFIG_PROBES)} paths tried, all refused: "
                        f"HTTP {', '.join(sorted({str(r) for r in refused}))})")


def check_server_fresh(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    status, _, body = s.get("/__status")
    if status != 200:
        return Result(FAIL, "server has no /__status: it was started from older code", ["Restart: python3 server.py"])
    info = json.loads(body)
    problems = []
    for name, field in (("server.py", "server_sha256"), ("build.py", "build_sha256")):
        with open(os.path.join(ROOT, name), "rb") as f:
            if info.get(field) != sha256(f.read()):
                problems.append(f"{name} changed since the server started: it is running old code. Restart it.")
    with open(INDEX_PATH, "rb") as f:
        if info.get("index_sha256") != sha256(f.read()):
            problems.append("index.json changed since the server loaded it: /chat is answering from old notes. "
                            "POST /reindex or restart.")
    graph = st.get("graph") or {}
    if graph.get("version") and info.get("graph_version") != graph.get("version"):
        problems.append(f"server answers from graph {info.get('graph_version')} but the browser is served "
                        f"{graph.get('version')}: answers would point at the wrong stars")
    up = time.time() - info.get("started_at", time.time())
    if problems:
        return Result(FAIL, "the running server is out of date with the files on disk", problems)
    return Result(PASS, f"running server.py, build.py and notes index all match disk (pid {info.get('pid')}, "
                        f"up {up / 60:.0f} min)")


def check_graph_current(s, st):
    try:
        sys.path.insert(0, ROOT)
        import build
        _, index, summary = build.build(notes_dir_from_index())
    except Exception as e:
        return Result(FAIL, "could not index the notes folder", [f"{type(e).__name__}: {e}"])
    graph = st.get("graph") or {}
    if not graph:
        return Result(WARN, "not compared: no graph data was served")
    if index["version"] != graph.get("version"):
        return Result(WARN, "the notes folder has changed since the graph was last built",
                      [f"served {graph.get('version')}, notes now index to {index['version']}",
                       "Run python3 build.py (then restart), or POST /reindex"])
    return Result(PASS, f"graph matches the notes on disk ({summary.split(',')[0].replace('Indexed ', '')})")


def check_see(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    try:
        with open(CANARY_JPEG, "rb") as f:
            jpeg = f.read()
    except OSError:
        return Result(WARN, "not run: preflight_canary.jpg is missing")
    now = time.time() * 1000
    frame = {"media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode(), "width": 440, "height": 120,
             "asked_at": now, "captured_at": now}
    # The guard first: a PNG labelled as JPEG must be refused before it reaches the API.
    png_as_jpeg = dict(frame, data=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode())
    gs, gd = s.post_json("/see", {"question": "preflight guard check", "frame": png_as_jpeg})
    if gs == 404:
        return Result(FAIL, "the server has no /see endpoint (running old code?)")
    details = []
    if gd.get("error") != "not_jpeg":
        return Result(FAIL, "/see accepted PNG bytes labelled image/jpeg", [str(gd)[:200]])
    details.append("refuses PNG bytes labelled image/jpeg ✓")
    status, data = s.post_json("/see", {"question": "What text is in this image? Reply with the text only.",
                                        "session": "preflight-see", "frame": frame})
    if status != 200:
        return Result(FAIL, f"/see returned HTTP {status}: {data.get('error')}", details + [str(data.get("answer"))[:160]])
    if data.get("error") == "config":
        return Result(WARN, "real JPEG accepted by /see (type, bytes, freshness checked); answer not checked "
                            "(no valid key)", details)
    if data.get("error"):
        return Result(FAIL, f"/see failed: {data.get('answer', '')[:120]}", details)
    details.append(f"answer: “{data.get('answer', '')[:120]}”")
    if "4471" in data.get("answer", ""):
        return Result(PASS, "/see read the canary image (“PREFLIGHT 4471”)", details)
    return Result(WARN, "/see answered but didn't read the canary text", details)


def check_greeting(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    status, _, body = s.get("/persona.json")
    if status != 200:
        return Result(FAIL, f"GET /persona.json returned HTTP {status}: the boot greeting will fall back")
    greeting = json.loads(body).get("greeting", "")
    if "{count}" not in greeting:
        return Result(WARN, "BOOT_GREETING has no {count}: it won't say how many notes are indexed", [greeting])
    h = time.localtime().tm_hour   # same rule as the page: 5-11 morning, 12-16 afternoon, else evening
    part = "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening"
    sample = greeting.replace("{time_of_day}", part).replace("{count}", str(len((st.get("graph") or {}).get("nodes", [])))).replace("{notes}", "notes")
    return Result(PASS, f"boot greeting: “{sample}”")


def check_cdn(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    _, _, body = s.get("/")
    html = body.decode("utf-8", "replace")
    urls = set(re.findall(r'src="(https://[^"]+)"', html))
    imports = re.search(r'<script type="importmap">\s*(\{.*?\})\s*</script>', html, flags=re.S)
    if imports:
        table = json.loads(imports.group(1)).get("imports", {})
        for spec in re.findall(r"from '([^']+)'", html):
            for prefix, target in table.items():
                if spec == prefix or (prefix.endswith("/") and spec.startswith(prefix)):
                    urls.add(target + spec[len(prefix):] if prefix.endswith("/") else target)
    broken, unreachable = [], []
    for url in sorted(urls):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15) as r:
                if r.status != 200:
                    broken.append(f"{url}: HTTP {r.status}")
        except urllib.error.HTTPError as e:
            # A proxy refusing the tunnel is "can't tell from here"; the CDN itself saying 404 is broken.
            (unreachable if e.code in (403, 407) and "jsdelivr" not in str(e.headers.get("server", "")) else broken
             ).append(f"{url}: HTTP {e.code}")
        except OSError as e:
            unreachable.append(f"{url}: {getattr(e, 'reason', e)}")
    if broken:
        return Result(FAIL, f"{len(broken)} library URL(s) the page loads are broken", broken)
    if unreachable:
        return Result(WARN, f"could not reach the CDN from this machine to check {len(unreachable)} library URL(s)",
                      unreachable[:3] + (["…"] if len(unreachable) > 3 else []))
    return Result(PASS, f"all {len(urls)} library URLs the page loads respond 200")


def check_brain(s, st):
    if not st.get("server_up"):
        return Result(WARN, "not run: server is down")
    status, _, body = s.get("/model")
    if status != 200:
        return Result(FAIL, f"GET /model returned HTTP {status} (server running old code?)")
    info = json.loads(body)
    start, config, known, spoken = info["model"], info["config_model"], info["known"], info["spoken_names"]
    current = lambda: json.loads(s.get("/model")[2])["model"]
    details, problems = [f"running {info['name']} ({start}); config.json names {config}"], []
    # 1. Models that don't exist must be refused, and the brain must not move. "haiku 5" sits between
    #    two real Haikus (5.5 and 4.5): a loose matcher would pick one; this must refuse.
    for said in ("switch to opus 6", "switch to haiku 5"):
        _, d = s.post_json("/model", {"name": said})
        now = current()
        if d.get("ok") or d.get("error") != "unknown_model" or now != start:
            problems.append(f"'{said}' was not refused: ok={d.get('ok')}, error={d.get('error')}, now on {now}")
        else:
            details.append(f"refused '{said}': \u201c{d['answer'][:120]}\u201d")
    # 2. A real swap by spoken name, then put back exactly what was running before.
    said, expected = ("try on haiku", spoken["haiku"]) if spoken["haiku"] != start else ("try on sonnet", spoken["sonnet"])
    _, d = s.post_json("/model", {"name": said})
    verified = d.get("verified")
    try:
        if not d.get("ok") or current() != expected:
            problems.append(f"'{said}' did not switch to {expected}: {d.get('answer', '')[:120]}")
        else:
            details.append(f"'{said}' -> {expected} ({'introduced itself' if verified else 'not verified: no API key'}): "
                           f"\u201c{d.get('answer', '')[:100]}\u201d")
    finally:
        back = {"name": "go back to your normal brain"} if start == config else {"model": start}
        s.post_json("/model", back)
        if current() != start:
            problems.append(f"could not restore the brain to {start}; it is on {current()}")
        else:
            details.append(f"restored to {start}")
    # 3. With a valid key: does the server's list of real models match what the key can see?
    if st.get("key"):
        code, models = anthropic("GET", "/v1/models?limit=1000", st["key"], timeout=30)
        if code == 200:
            offered = {m.get("id") for m in models.get("data", [])}
            unseen = [m for m in known if m not in offered]
            if unseen:
                details.append("in MODEL_IDS but not offered to this key: " + ", ".join(unseen))
                if not problems:
                    return Result(WARN, "swaps and refusals work, but some listed models aren't available to your key", details)
    if problems:
        return Result(FAIL, "model switching is not safe", problems + details)
    if not verified:
        return Result(WARN, "refusals and swap-back work; the swap itself wasn't verified (no valid key)", details)
    return Result(PASS, "refuses models that don't exist, swaps for real, and restores the brain", details)


CHECKS = [
    # The eight required checks, in the required order.
    ("The server is up and serving the viewer", check_server_up),
    ("The graph data loads and has notes in it", check_graph),
    ("/chat answers a real question with a nodes array", check_chat),
    ("The API key in config.json is valid (real call)", check_key),
    ("The model in config.json is reachable with that key", check_model),
    ("/remember writes a file that /chat finds immediately", check_remember),
    ("The files the browser is served match the files on disk", check_served_files),
    ("config.json is NOT reachable from the browser", check_config_hidden),
    # Further live chains.
    ("The running server matches the code and notes on disk", check_server_fresh),
    ("The graph is up to date with the notes folder", check_graph_current),
    ("/see reads a real JPEG", check_see),
    ("The boot greeting is served", check_greeting),
    ("The libraries the page loads from the CDN exist", check_cdn),
    ("Swapping brains refuses models that don't exist, and goes back", check_brain),
]


def loud_leak_banner(urls):
    lines = ["SECURITY FAILURE: YOUR API KEY FILE IS READABLE FROM THE BROWSER", ""] + urls + [
        "", "Anyone who can open this page can read config.json.",
        "Stop the server now and fix what it serves before running it again."]
    width = max(len(l) for l in lines) + 4
    bar = "!" * (width + 4)
    out = [bar] + [f"!! {l.ljust(width - 2)} !!" for l in lines] + [bar]
    print("\n" + "\n".join(paint(l, "1;31") for l in out) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:4700", help="where the server is running")
    ap.add_argument("--only", help="comma-separated check numbers to run, e.g. 7,8")
    args = ap.parse_args()
    only = {int(x) for x in args.only.split(",")} if args.only else None

    s, st = Server(args.url), {}
    counts, skipped = {PASS: 0, FAIL: 0, WARN: 0}, 0
    print(f"Knowledge Galaxy preflight — {s.url} — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    for n, (title, fn) in enumerate(CHECKS, 1):
        # Checks 1-2 establish whether the server and graph exist; everything else depends on them,
        # so with --only they still run, and are reported whenever they fail. A run can never come
        # back clean having checked nothing.
        prerequisite = n in (1, 2)
        if only and n not in only and not prerequisite:
            continue
        if n == 9 and not only:
            print(paint("   ── further live chains ──", "2"))
        try:
            r = fn(s, st)
        except Exception as e:   # a crashing check is a failing check, never a silent one
            r = Result(FAIL, f"check crashed: {type(e).__name__}: {e}")
        if only and n not in only and r.status == PASS:
            continue                                  # an unselected prerequisite that passed: stay quiet
        counts[r.status] += 1
        skipped += r.status == WARN and r.summary.startswith(("not run", "not compared"))
        mark, code = MARK[r.status]
        print(f"{n:>2} {paint(mark, code)} {title}")
        print(f"     {paint(r.summary, code) if r.status != PASS else r.summary}")
        for d in r.details:
            print(f"       · {d}")
        if fn is check_config_hidden and st.get("leak"):
            loud_leak_banner(st["leak"])
    for problem in st.get("cleanup_problems", []):
        counts[FAIL] += 1
        print(paint(f" ✗ CLEANUP: {problem}", "31"))

    if counts[PASS] == 0 and counts[FAIL] == 0 and skipped == counts[WARN]:
        counts[FAIL] += 1
        print(paint(" ✗ nothing was verified: every check that ran was skipped", "31"))
    line = f"{counts[PASS]} pass, {counts[FAIL]} fail, {counts[WARN]} warn"
    print("\n" + paint(line, "31" if counts[FAIL] else "33" if counts[WARN] else "32"))
    sys.exit(1 if counts[FAIL] else 0)


if __name__ == "__main__":
    main()
