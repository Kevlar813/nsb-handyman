"""Generate the content calendar, script book and channel bios from scripts.json + config.json."""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
DATA = json.loads((ROOT / "content/scripts.json").read_text())
START = dt.date.fromisoformat(CFG.get("start_date", "2026-10-24"))
# Short-form reach peaks around lunch and early evening; alternate to test both.
TIMES = ["12:00 PM", "6:30 PM"]


def date_of(day):
    return START + dt.timedelta(days=day - 1)


def words(text):
    return len(text.split())


def calendar():
    rows = ["# 30-Day Content Calendar: Fall Pressure Washing",
            "",
            f"**Offer on every video:** {CFG['offer_title'].title()}: {CFG['offer_house']} per house, "
            f"{CFG['offer_driveway']} per driveway. Call or text {CFG['phone']}.",
            "",
            "**Post each video to:** YouTube Shorts, TikTok, Instagram Reels and Facebook Reels "
            "(same file and caption on all four). Pin the comment: *\"Fall special: "
            f"{CFG['offer_house']} house / {CFG['offer_driveway']} driveway. Call or text {CFG['phone']}\"*.",
            "",
            "| Day | Date | Post time | Pillar | Surface | Title | Hook (first 2 seconds) |",
            "|---:|---|---|---|---|---|---|"]
    for v in DATA["videos"]:
        d = date_of(v["day"])
        rows.append(f"| {v['day']} | {d:%a %b %d} | {TIMES[v['day'] % 2]} | {v['pillar']} | {v['surface']} | "
                    f"{v['title']} | \"{v['hook']}\" |")
    rows += ["",
             "## Weekly rhythm",
             "- **Transformations** (most days): the reliable reach driver. Satisfying reveals get rewatched.",
             "- **Mistakes / myths / honest answers** (about 2 a week): build trust and get saves and shares.",
             "- **Seasonal + offer pushes** (Thursday to Saturday): leaf stains, holiday lights, house + driveway bundle.",
             "- **Day 30 recap**: a montage of the month's reveals with a last call for the fall special.",
             "",
             "## Daily 10-minute routine",
             "1. Upload `output/videos/dayNN.mp4` with the caption and hashtags from `scripts.md`.",
             "2. Set the cover to `output/thumbnails/dayNN_cover.png` (YouTube: `dayNN_yt.png`).",
             "3. Pin the offer comment. Reply to every comment within the first hour.",
             "4. Log calls and texts that came from each video (\"How did you hear about us?\")."]
    return "\n".join(rows) + "\n"


def script_book():
    out = ["# Video Scripts: 30 Faceless Pressure-Washing Shorts", "",
           "Each video runs: **hook → before → process → wipe reveal → split before/after → offer card**.",
           f"Every video ends with the same CTA voiceover: *\"{DATA['cta_vo']}\"*", ""]
    for v in DATA["videos"]:
        d = date_of(v["day"])
        out += [f"## Day {v['day']}: {v['title']}",
                f"*{d:%A, %B %d} · {v['pillar']} · {v['surface']} · ~{words(v['body']) + words(DATA['cta_vo'])} words*",
                "",
                f"**Hook:** {v['hook']}",
                "",
                f"**Voiceover:** {v['body']} *{DATA['cta_vo']}*",
                "",
                f"**On-screen captions:** {v['hook_caption'].replace(chr(10), ' ')}"
                + (f" → {' → '.join(v['steps'])}" if v.get("steps") else "")
                + (" → BEFORE / AFTER → offer card" if not v.get("montage") else " → rapid before/after montage → offer card"),
                "",
                f"**Thumbnail text:** {v['thumb_text'].replace(chr(10), ' ')}",
                "",
                f"**Post caption:** {v['post_caption']} {CFG['phone']}",
                "",
                f"**Hashtags:** {' '.join(v['hashtags'])} #{CFG['service_area'].replace(' ', '').lower()}",
                ""]
    return "\n".join(out)


def bios():
    p, area = CFG["phone"], CFG["service_area"]
    h, dw = CFG["offer_house"], CFG["offer_driveway"]
    return f"""# Channel Bios & Lead Call-to-Action

Use the same name, logo (`brand/logo-1000.png`) and handle everywhere. Suggested handle: **@NSBHandyman**
(fallback: @NSBPressureWashing).

## TikTok (80 characters max)
```
Pressure washing in {area} 💦 Fall: {h}/house {dw}/driveway. Text {p}
```

## Instagram (150 characters max)
```
💦 Pressure & soft washing | {area}
🍂 FALL SPECIAL: {h} house · {dw} driveway
📲 Call/text {p}
👇 Book your spot
```

## YouTube channel description
```
NSB Handyman: pressure washing and soft washing in {area}.

Driveways, house siding, walkways, patios and brick, start to finish, before and after.
No fluff. Just clean.

🍂 FALL SPECIAL
• {h} per house wash
• {dw} per driveway

📲 Call or text {p} to book.
New transformation every day.
```

## Facebook page "About"
```
NSB Handyman: professional pressure washing and soft washing in {area}. Houses, driveways, walkways and patios.
FALL SPECIAL: {h} per house, {dw} per driveway. Call or text {p}. Fast quotes, honest answers, clean results.
```

## Pinned comment (every video)
```
🍂 Fall special: {h} per house, {dw} per driveway. Call or text {p} to book. Spots fill fast.
```

## Link-in-bio button labels
- "Book Fall Special ({h} House / {dw} Driveway)"
- "Text Us for a Quote"
"""


if __name__ == "__main__":
    (ROOT / "content/calendar.md").write_text(calendar())
    (ROOT / "content/scripts.md").write_text(script_book())
    (ROOT / "brand/bios.md").write_text(bios())
    print("wrote content/calendar.md, content/scripts.md, brand/bios.md")
