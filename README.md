# nsb-handyman
Professional handyman service website

## Knowledge Galaxy

A 3D map of the markdown notes in `notes/`, with a chat bar that answers questions from those notes only.

```bash
python3 build.py            # or: python3 build.py /path/to/your/notes
python3 server.py           # then open http://localhost:4700
```

- `build.py` writes `viewer/graphdata.js` (browser) and `index.json` (server-only full text). Re-run it whenever notes change, then restart the server.
- `server.py` serves only `viewer/` on port 4700 and answers `POST /chat` → `{"answer": "...", "nodes": [note indexes]}`.
- Put your Anthropic API key in `config.json` in the project root (git-ignored, never served to the browser).

### Preflight

`python3 preflight.py` runs every live chain against the running server and the real Anthropic API
(server up, graph data, `/chat`, API key, model, `/remember` → `/chat`, served files vs disk,
config.json not exposed, running code vs disk, `/see`, boot greeting, CDN libraries) and ends with
`N pass, N fail, N warn`. It exits non-zero on any failure. Run it after every change.

- `POST /remember {"title": "...", "text": "..."}` saves a new note to `notes/remembered/` and re-indexes.
- `POST /reindex` re-reads the notes folder after you edit notes, without restarting the server.
