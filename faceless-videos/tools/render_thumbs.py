"""Render YouTube (1280x720) and Shorts/Reels/TikTok cover (1080x1920) thumbnails.

usage: python3 render_thumbs.py <day> [...] | all
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
SCRIPTS = json.loads((ROOT / "content/scripts.json").read_text())
TEMPLATE = (ROOT / "brand/thumbnail_template.html").read_text()
SIZES = {"yt": (1280, 720, "land"), "cover": (1080, 1920, "port")}
NODE_ENV = {**os.environ, "NODE_PATH": subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()}


def pair_for(v):
    if v.get("montage"):
        # recap uses the first finished transformation
        for x in SCRIPTS["videos"]:
            d = ROOT / f"assets/gen/d{x['day']:02d}"
            if (d / "after.jpg").exists() and not x.get("montage"):
                return d / "before.jpg", d / "after.jpg"
        return None
    d = ROOT / f"assets/gen/d{v['day']:02d}"
    return (d / "before.jpg", d / "after.jpg") if (d / "after.jpg").exists() else None


def render(v):
    pair = pair_for(v)
    if not pair:
        print(f"day {v['day']}: no images yet, skipped")
        return
    out_dir = ROOT / "output/thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, (w, h, mode) in SIZES.items():
        html = (TEMPLATE.replace("{{W}}", str(w)).replace("{{H}}", str(h)).replace("{{MODE}}", mode)
                .replace("{{BEFORE}}", pair[0].as_uri()).replace("{{AFTER}}", pair[1].as_uri())
                .replace("{{TEXT}}", v["thumb_text"].replace("\n", "<br>"))
                .replace("{{LOGO}}", (ROOT / "brand/logo-1000.png").as_uri())
                .replace("{{HOUSE}}", CFG["offer_house"]).replace("{{DRIVEWAY}}", CFG["offer_driveway"]))
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write(html)
        out = out_dir / f"day{v['day']:02d}_{key}.png"
        subprocess.run(["node", str(ROOT / "tools/render_html.js"), f.name, str(out), str(w), str(h)],
                       check=True, env=NODE_ENV)
        os.unlink(f.name)
        print("thumb", out)


if __name__ == "__main__":
    args = sys.argv[1:]
    for v in SCRIPTS["videos"]:
        if args == ["all"] or str(v["day"]) in args:
            render(v)
