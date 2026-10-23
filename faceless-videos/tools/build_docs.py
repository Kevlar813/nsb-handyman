"""Generate the content calendar, script book and channel bios from scripts.json + config.json."""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
DATA = json.loads((ROOT / "content/scripts.json").read_text())
# Short-form reach peaks around lunch and early evening; alternate to test both.
TIMES = ["12:00 PM", "6:30 PM"]


def date_of(v):
    return dt.date.fromisoformat(v["date"])


def words(text):
    return len(text.split())


def calendar():
    n = len(DATA["videos"])
    towns = ", ".join(CFG["service_area_list"])
    rows = [f"# {n}-Video Content Calendar: Fall Pressure Washing Special",
            "",
            f"**Offer on every video:** {CFG['offer_title'].title()}: {CFG['offer_house']} per house, "
            f"{CFG['offer_driveway']} per driveway. **Ends {CFG['offer_ends_long']}.** "
            f"Call or text {CFG['phone']} · {CFG['email']}",
            "",
            f"**Service area:** {towns}, FL",
            "",
            "**Post each video to:** YouTube Shorts, TikTok, Instagram Reels and Facebook Reels "
            "(same file and caption on all four), plus your Facebook page and local Facebook groups where "
            "business posts are allowed. Pin the comment from `brand/bios.md`.",
            "",
            "| # | Post date | Time | Pillar | Title | Hook (first 2 seconds) | Files |",
            "|---:|---|---|---|---|---|---|"]
    for v in DATA["videos"]:
        rows.append(f"| {v['day']} | {date_of(v):%a %b %d} | {TIMES[v['day'] % 2]} | {v['pillar']} | {v['title']} | "
                    f"\"{v['hook']}\" | `video{v['day']:02d}.mp4` |")
    rows += ["",
             "## Why this order",
             "- **Videos 1–2** open with the strongest transformations (driveway, then house) to set the channel's look.",
             "- **Videos 3–4** are a mistake and an honest answer. They earn trust and get shared.",
             "- **Videos 5–7** are Florida-specific: salt air, humidity-green pavers, and washing before holiday lights.",
             "- **Videos 8–10** push the deadline: the $178 house-and-driveway bundle, a final reveal, "
             f"then a last call two days before the special ends on {CFG['offer_ends_long']}.",
             "",
             "## Between posts (to stretch 10 videos across 5 weeks)",
             "- Re-share each video to your Facebook and Instagram Stories two days after posting, with an **Act Now** link sticker.",
             "- Repost the best performer (most views) in the final week. The offer card already shows the deadline.",
             "",
             "## Per-post routine (10 minutes)",
             "1. Upload `output/videos/videoNN.mp4` with the caption and hashtags from `scripts.md`.",
             "2. Set the cover to `output/thumbnails/videoNN_cover.png` (YouTube: `videoNN_yt.png`).",
             "3. Pin the offer comment. Reply to every comment within the first hour.",
             "4. Log calls and texts that came from each video (\"How did you hear about us?\")."]
    return "\n".join(rows) + "\n"


def script_book():
    out = [f"# Video Scripts: {len(DATA['videos'])} Faceless Pressure-Washing Shorts", "",
           "Each video runs: **hook → before → process → wipe reveal → split before/after → offer card "
           f"with a blinking ACT NOW · ENDS {CFG['offer_ends']} button**.",
           f"Every video ends with the same CTA voiceover: *\"{DATA['cta_vo']}\"*", ""]
    for v in DATA["videos"]:
        out += [f"## Video {v['day']}: {v['title']}",
                f"*Post {date_of(v):%A, %B %d} · {v['pillar']} · {v['surface']} · "
                f"~{words(v['body']) + words(DATA['cta_vo'])} words*",
                "",
                f"**Hook:** {v['hook']}",
                "",
                f"**Voiceover:** {v['body']} *{DATA['cta_vo']}*",
                "",
                f"**On-screen captions:** {v['hook_caption'].replace(chr(10), ' ')}"
                + (f" → {' → '.join(v['steps'])}" if v.get("steps") else "")
                + (" → BEFORE / AFTER → offer card + ACT NOW" if not v.get("montage")
                   else " → rapid before/after montage → offer card + ACT NOW"),
                "",
                f"**Thumbnail text:** {v['thumb_text'].replace(chr(10), ' ')}",
                "",
                f"**Post caption:** {v['post_caption']} Call or text {CFG['phone']} · {CFG['email']}",
                "",
                f"**Hashtags:** {' '.join(v['hashtags'])} #pressurewashingflorida #volusiacounty",
                ""]
    return "\n".join(out)


def bios():
    p, e, short = CFG["phone"], CFG["email"], CFG["service_area"]
    h, dw, end, end_long = CFG["offer_house"], CFG["offer_driveway"], CFG["offer_ends"], CFG["offer_ends_long"]
    towns = ", ".join(CFG["service_area_list"])
    tel = "".join(ch for ch in p if ch.isdigit())
    return f"""# Channel Bios, Act Now Button & Lead Call-to-Action

Use the same name, logo (`brand/logo-1000.png`) and handle everywhere. Suggested handle: **@NSBHandyman**
(fallback: @NSBPressureWashing).

**Service area:** {towns}, Florida.

## TikTok (80 characters max)
```
Ormond Bch–Edgewater FL 💦 {h} house {dw} drive · Ends {end} · {p}
```

## Instagram (150 characters max)
```
💦 Pressure & soft washing · Ormond Beach to Edgewater FL
🍂 {h} house · {dw} driveway. Ends {end}!
📲 {p}
👇 ACT NOW
```

## YouTube channel description
```
NSB Handyman: pressure washing and soft washing across {towns}, Florida.

Driveways, houses, pavers and walkways, start to finish, before and after.
No fluff. Just clean.

🍂 FALL SPECIAL: ENDS {end_long.upper()}
• {h} per house wash
• {dw} per driveway

👉 ACT NOW: call or text {p}
✉️ {e}
New transformation every few days.
```

## Facebook page "About"
```
NSB Handyman: professional pressure washing and soft washing in {towns}, FL.
FALL SPECIAL: {h} per house, {dw} per driveway. Ends {end_long}. Act now: call or text {p} or email {e}.
```

## Pinned comment (every video)
```
🍂 Fall special ends {end_long}: {h} per house, {dw} per driveway. ACT NOW: call or text {p} · {e}
Serving {towns}.
```

## "Act Now" button setup (do this on every platform)
| Platform | Where | Button label | Action |
|---|---|---|---|
| Facebook Page | Edit **Add Button** | **Call Now** (or **Send Message**) | `{p}` |
| Instagram (business profile) | Edit profile → **Contact options / Action buttons** | **Call** + **Email** | `{p}`, `{e}` |
| Instagram & Facebook Stories | **Link sticker** | `ACT NOW: ENDS {end}` | `tel:+1{tel}` or your booking link |
| TikTok (business account) | Edit profile → **Contact** / website link | `ACT NOW: {h} House / {dw} Driveway` | phone, email or booking link |
| YouTube | Channel → Customization → **Links** | `ACT NOW: Fall Special (Ends {end})` | booking link or `mailto:{e}` |
| Google Business Profile | **Offer post** | **Call now** | `{h} house / {dw} driveway, ends {end_long}` |

Link labels for a link-in-bio page:
- **ACT NOW: {h} House / {dw} Driveway (Ends {end})** → `tel:+1{tel}`
- **Text Us for a Quote** → `sms:+1{tel}`
- **Email Us** → `mailto:{e}`
"""


if __name__ == "__main__":
    (ROOT / "content/calendar.md").write_text(calendar())
    (ROOT / "content/scripts.md").write_text(script_book())
    (ROOT / "brand/bios.md").write_text(bios())
    print("wrote content/calendar.md, content/scripts.md, brand/bios.md")
