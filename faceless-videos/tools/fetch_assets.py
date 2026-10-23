"""Download every generated asset listed in assets/gen/manifest.json into assets/gen/dNN/.

Skips files that are already present, so it is safe to re-run.
usage: python3 tools/fetch_assets.py
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "assets/gen"
MANIFEST = json.loads((GEN / "manifest.json").read_text())


def fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "-sSfL", "--retry", "4", "--retry-delay", "2", "-o", str(dest), url], check=True)
    print("fetched", dest.relative_to(ROOT))


fetch(MANIFEST["cta"]["url"], GEN / "cta.wav")
for key, v in sorted(MANIFEST["videos"].items()):
    d = GEN / f"d{key}"
    for field, name in (("before", "before.jpg"), ("after", "after.jpg"), ("process", "process.mp4")):
        if v.get(field):
            fetch(v[field], d / name)
    if v.get("vo"):
        fetch(v["vo"]["url"], d / "vo.wav")
        (d / "vo.json").write_text(json.dumps({k: v["vo"][k] for k in ("sentenceEnds", "hookEnd") if k in v["vo"]}))
