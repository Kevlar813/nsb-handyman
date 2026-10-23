"""Assemble a finished 1080x1920 faceless before/after short for one calendar day.

Inputs (per day, in assets/gen/dNN/):
  before.jpg, after.jpg   AI or real photos (same framing)
  process.mp4             optional motion clip (first frame=before, last=after)
  vo.wav                  body voiceover; vo.json may hold {"sentenceEnds": [...]}
Shared: assets/gen/cta.wav (CTA voiceover), assets/music/*.wav, assets/sfx/*.wav

Timeline: hook (before, push-in) -> process clip -> wipe reveal -> split
before/after -> CTA end card. Captions are minimal: hook, step labels,
BEFORE/AFTER tags, and the offer card.

usage: python3 render_video.py <day> [<day> ...]   (or "all")
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
SCRIPTS = json.loads((ROOT / "content/scripts.json").read_text())
W, H, FPS = 1080, 1920, 30
MUSIC = ["bright_drive.wav", "clean_bounce.wav", "fresh_start.wav"]


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(out.strip())


def font(size, black=True):
    return ImageFont.truetype(CFG["font_black"] if black else CFG["font_bold"], size)


# ---------- caption / overlay images ----------

def text_block(text, size, fill="#FFFFFF", box=None, pad=(34, 20), stroke=10, max_w=W - 120):
    """Centered multi-line text on a transparent canvas, auto-shrunk to max_w."""
    lines = text.split("\n")
    while True:
        f = font(size)
        widths = [f.getbbox(l, stroke_width=stroke)[2] for l in lines]
        if max(widths) + 2 * pad[0] <= max_w or size < 40:
            break
        size -= 4
    asc, desc = f.getmetrics()
    line_h = asc + desc + 6
    bw = max(widths) + 2 * pad[0]
    bh = line_h * len(lines) + 2 * pad[1]
    img = Image.new("RGBA", (bw + 40, bh + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if box:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([24, 26, 20 + bw, 22 + bh], 28, fill=(0, 0, 0, 120))
        img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(8)))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([20, 20, 20 + bw, 20 + bh], 28, fill=box)
    for i, l in enumerate(lines):
        lw = f.getbbox(l, stroke_width=stroke)[2]
        x = 20 + (bw - lw) // 2
        y = 20 + pad[1] + i * line_h
        d.text((x, y), l, font=f, fill=fill,
               stroke_width=0 if box else stroke, stroke_fill="#000000")
    return img


def place(img, y_center):
    """Put an overlay image onto a full-frame transparent canvas."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((W - img.width) // 2, max(0, int(y_center - img.height / 2))))
    return canvas


def tag(text, color, corner="tl"):
    t = text_block(text, 54, fill=CFG["colors"]["navy"] if color == CFG["colors"]["yellow"] else "#FFFFFF",
                   box=color, pad=(26, 10))
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x = 30 if corner.endswith("l") else W - t.width - 30
    y = 150 if corner.startswith("t") else H - t.height - 360
    canvas.alpha_composite(t, (x, y))
    return canvas


def ai_label():
    label = CFG.get("ai_label")
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if not label:
        return canvas
    f = font(26, black=False)
    d = ImageDraw.Draw(canvas)
    bw = f.getbbox(label)[2] + 28
    d.rounded_rectangle([W - bw - 24, H - 84, W - 24, H - 44], 14, fill=(0, 0, 0, 110))
    d.text((W - bw - 10, H - 80), label, font=f, fill=(255, 255, 255, 210))
    return canvas


def split_overlay():
    c = CFG["colors"]
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, H // 2 - 7, W, H // 2 + 7], fill=c["white"])
    canvas.alpha_composite(tag("BEFORE", c["navy"]), (0, 0))
    after = text_block("AFTER", 54, fill=c["navy"], box=c["yellow"], pad=(26, 10))
    canvas.alpha_composite(after, (30, H // 2 + 40))
    return canvas


def act_now_geometry():
    btn = f"{CFG['act_now']}  ·  ENDS {CFG['offer_ends']}"
    f_btn = font(64)
    bw = f_btn.getbbox(btn)[2] + 110
    return btn, f_btn, ((W - bw) // 2, 985, bw, 140)


def act_now_pulse():
    """Bright ring around the ACT NOW button; blinked on the end card."""
    _, _, (bx, by, bw, bh) = act_now_geometry()
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle([bx - 18, by - 18, bx + bw + 18, by + bh + 18], 88,
                                           outline=(255, 255, 255, 255), width=10)
    glow = ring.filter(ImageFilter.GaussianBlur(10))
    return Image.alpha_composite(glow, ring)


def end_card(bg_path, out_path):
    """Offer card over a blurred, darkened copy of the AFTER image."""
    c = CFG["colors"]
    bg = Image.open(bg_path).convert("RGB")
    bg = bg.resize((W, int(bg.height * W / bg.width))).crop((0, 0, W, H)) if bg.height * W / bg.width >= H \
        else bg.resize((int(bg.width * H / bg.height), H)).crop((0, 0, W, H))
    bg = bg.filter(ImageFilter.GaussianBlur(18))
    card = Image.alpha_composite(bg.convert("RGBA"), Image.new("RGBA", (W, H), (10, 35, 66, 205)))
    # Layout keeps everything above ~1580px: TikTok/Reels UI covers the bottom of the frame.
    logo = Image.open(ROOT / "brand/logo-1000.png").convert("RGBA").resize((300, 300), Image.LANCZOS)
    card.alpha_composite(logo, ((W - 300) // 2, 100))
    d = ImageDraw.Draw(card)

    def center(text, y, size, fill, black=True):
        f = font(size, black)
        tw = f.getbbox(text)[2]
        d.text(((W - tw) // 2, y), text, font=f, fill=fill)

    center(CFG["offer_title"], 420, 92, c["yellow"])
    for i, (price, label) in enumerate(((CFG["offer_house"], "PER HOUSE"), (CFG["offer_driveway"], "PER DRIVEWAY"))):
        y = 550 + i * 210
        d.rounded_rectangle([110, y, W - 110, y + 185], 34, fill=c["white"])
        f_price, f_label = font(130), font(50, black=False)
        pw = f_price.getbbox(price)[2]
        lw = f_label.getbbox(label)[2]
        x0 = (W - (pw + 30 + lw)) // 2
        d.text((x0, y + 12), price, font=f_price, fill=c["navy"])
        d.text((x0 + pw + 30, y + 74), label, font=f_label, fill=c["navy"])

    # ACT NOW button
    btn, f_btn, (bx, by, bw, bh) = act_now_geometry()
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([bx - 14, by - 14, bx + bw + 14, by + bh + 14], 84, fill=(255, 199, 44, 150))
    card = Image.alpha_composite(card, glow.filter(ImageFilter.GaussianBlur(16)))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([bx, by, bx + bw, by + bh], 70, fill=c["yellow"], outline=c["white"], width=5)
    d.text((bx + 55, by + 32), btn, font=f_btn, fill=c["navy"])

    center("CALL OR TEXT", 1170, 54, c["aqua"], black=False)
    center(CFG["phone"], 1235, 118, c["white"])
    center(CFG["email"], 1385, 48, c["white"], black=False)
    # wrap the town list to the frame width
    f_town, lines = font(34, black=False), [[]]
    for town in [t.upper() for t in CFG["service_area_list"][:-1]] + [CFG["service_area_list"][-1].upper() + ", FL"]:
        if lines[-1] and f_town.getbbox("  ·  ".join(lines[-1] + [town]))[2] > W - 140:
            lines.append([])
        lines[-1].append(town)
    for i, line in enumerate(lines):
        center("  ·  ".join(line), 1462 + i * 46, 34, c["aqua"], black=False)
    card.convert("RGB").save(out_path, quality=95)


# ---------- video segments ----------

def still_clip(img, out, seconds, zoom_from=1.0, zoom_to=1.08):
    frames = max(1, round(seconds * FPS))
    rate = (zoom_to - zoom_from) / frames
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-vf",
         f"scale={W * 3}:{H * 3}:force_original_aspect_ratio=increase,crop={W * 3}:{H * 3},"
         f"zoompan=z='{zoom_from}+{rate}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
         "format=yuv420p",
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def process_clip(src, out, seconds):
    """Fit the motion clip to `seconds` (speed-adjust within limits, then trim/pad)."""
    d = duration(src)
    speed = max(0.6, min(1.8, d / seconds))
    run(["ffmpeg", "-y", "-i", str(src), "-an", "-vf",
         f"setpts=PTS/{speed},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
         f"tpad=stop_mode=clone:stop_duration={seconds},format=yuv420p",
         "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def reveal_clip(before, after, out, seconds, wipe=1.1):
    """Hold BEFORE, wipe to AFTER with a bright spray line, then a slow push on AFTER."""
    tmp = Path(out).parent
    hold = 0.5
    still_clip(before, tmp / "rv_b.mp4", hold + wipe, 1.04, 1.04)
    still_clip(after, tmp / "rv_a.mp4", seconds - hold, 1.04, 1.10)
    line_x = f"if(between(t,{hold},{hold + wipe}),(t-{hold})/{wipe}*{W}-6,-100)"
    run(["ffmpeg", "-y", "-i", str(tmp / "rv_b.mp4"), "-i", str(tmp / "rv_a.mp4"), "-filter_complex",
         f"[0][1]xfade=transition=wiperight:duration={wipe}:offset={hold},"
         f"drawbox=x='{line_x}':y=0:w=12:h={H}:color=white@0.9:t=fill,format=yuv420p",
         "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def split_clip(before, after, out, seconds):
    frames = max(1, round(seconds * FPS))
    half = H // 2
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(before), "-loop", "1", "-i", str(after), "-filter_complex",
         f"[0]scale={W}:-2,crop={W}:{half}[b];[1]scale={W}:-2,crop={W}:{half}[a];[b][a]vstack,"
         f"zoompan=z='1+0.0006*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
         "format=yuv420p",
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def concat(clips, out):
    lst = Path(out).with_suffix(".txt")
    lst.write_text("".join(f"file '{c}'\n" for c in clips))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])


# ---------- per-day render ----------

def day_dir(day):
    return ROOT / f"assets/gen/d{day:02d}"


def sentence_end(gen, fallback):
    meta = gen / "vo.json"
    if meta.exists():
        data = json.loads(meta.read_text())
        if data.get("hookEnd"):
            return float(data["hookEnd"])
        ends = data.get("sentenceEnds") or []
        if ends:
            return float(ends[0])
    return fallback


def render(day):
    v = next(x for x in SCRIPTS["videos"] if x["day"] == day)
    gen = day_dir(day)
    out = ROOT / f"output/videos/video{day:02d}.mp4"
    tmp = Path(tempfile.mkdtemp(prefix=f"d{day:02d}_"))
    try:
        vo, cta = gen / "vo.wav", ROOT / "assets/gen/cta.wav"
        body_t, cta_t = duration(vo), duration(cta)
        gap = 0.25
        total = body_t + gap + cta_t + 0.9
        overlays = []  # (png, start, end)

        def overlay(img, start, end, name):
            p = tmp / f"ov_{name}.png"
            img.save(p)
            overlays.append((p, start, end))

        if v.get("montage"):
            clips, t = build_montage(day, tmp, body_t)
            hook_end = min(2.6, body_t)
            last_after = day_dir(t) / "after.jpg"
            overlay(ai_label(), 0, body_t + gap, "ailabel")
        else:
            before, after = gen / "before.jpg", gen / "after.jpg"
            hook_end = min(max(2.2, sentence_end(gen, 2.5) + 0.15), body_t * 0.3)
            middle = body_t - hook_end
            reveal_t = 2.6
            split_t = max(2.4, middle * 0.22)
            process_t = middle - reveal_t - split_t
            clips = [tmp / "s1.mp4", tmp / "s2.mp4", tmp / "s3.mp4", tmp / "s4.mp4"]
            still_clip(before, clips[0], hook_end, 1.0, 1.12)
            if (gen / "process.mp4").exists():
                process_clip(gen / "process.mp4", clips[1], process_t)
            else:
                still_clip(before, clips[1], process_t, 1.12, 1.2)
            reveal_clip(before, after, clips[2], reveal_t)
            split_clip(before, after, clips[3], split_t)
            last_after = after

            c = CFG["colors"]
            overlay(tag("BEFORE", c["navy"]), 0, hook_end + process_t * 0.35, "before")
            steps = v.get("steps") or []
            for i, s in enumerate(steps):
                a = hook_end + process_t * i / len(steps)
                b = hook_end + process_t * (i + 1) / len(steps)
                overlay(place(text_block(s, 76, fill=c["navy"], box=c["white"]), H * 0.80), a + 0.1, b - 0.05, f"step{i}")
            r0 = hook_end + process_t
            overlay(tag("AFTER", c["yellow"]), r0 + 1.4, r0 + reveal_t, "after")
            overlay(split_overlay(), r0 + reveal_t, body_t + gap, "split")
            overlay(ai_label(), 0, body_t + gap, "ailabel")

        overlay(place(text_block(v["hook_caption"], 104), H * 0.30), 0.05, hook_end, "hook")

        pulse = tmp / "ov_pulse.png"
        act_now_pulse().save(pulse)
        overlays.append((pulse, body_t + 0.6, total, "lt(mod(t,0.8),0.4)"))

        end_png = tmp / "end.jpg"
        end_card(last_after, end_png)
        clips.append(tmp / "s5.mp4")
        still_clip(end_png, clips[-1], total - body_t, 1.0, 1.0)  # static so the button pulse stays aligned
        concat(clips, tmp / "base.mp4")

        # overlays
        inputs, chain, last = ["-i", str(tmp / "base.mp4")], [], "0:v"
        for i, (png, a, b, *blink) in enumerate(overlays, start=1):
            inputs += ["-i", str(png)]
            cond = f"between(t,{a:.3f},{b:.3f})" + (f"*{blink[0]}" if blink else "")
            chain.append(f"[{last}][{i}:v]overlay=0:0:enable='{cond}'[v{i}]")
            last = f"v{i}"
        # audio: VO body, CTA, music bed, sfx
        music = ROOT / "assets/music" / MUSIC[day % len(MUSIC)]
        sfx = ROOT / "assets/sfx"
        n = len(overlays) + 1
        inputs += ["-i", str(vo), "-i", str(cta), "-stream_loop", "-1", "-i", str(music),
                   "-i", str(sfx / "whoosh.wav"), "-i", str(sfx / "ding.wav"), "-i", str(sfx / "spray.wav")]
        cta_ms = int((body_t + gap) * 1000)
        if v.get("montage"):
            whoosh_ms, spray_a, spray_len = int(hook_end * 1000), 0, 0.0
        else:
            whoosh_ms = int((hook_end + process_t + 0.4) * 1000)
            spray_a, spray_len = int(hook_end * 1000), max(0.1, process_t)
        chain += [
            f"[{n}:a]aresample=44100,volume=1.0[vo]",
            f"[{n + 1}:a]aresample=44100,adelay={cta_ms}|{cta_ms},volume=1.0[cta]",
            f"[{n + 2}:a]aresample=44100,atrim=0:{total:.3f},volume=0.16,afade=t=out:st={total - 1.2:.3f}:d=1.2[mus]",
            f"[{n + 3}:a]aresample=44100,adelay={whoosh_ms}|{whoosh_ms},volume=0.45[wh]",
            f"[{n + 4}:a]aresample=44100,adelay={cta_ms}|{cta_ms},volume=0.30[dg]",
            f"[{n + 5}:a]aresample=44100,atrim=0:{spray_len:.3f},afade=t=out:st={max(0, spray_len - 0.4):.3f}:d=0.4,"
            f"adelay={spray_a}|{spray_a},volume=0.10[sp]",
            "[vo][cta][mus][wh][dg][sp]amix=inputs=6:normalize=0:duration=longest,"
            f"atrim=0:{total:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[aout]",
        ]
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain),
             "-map", f"[{last}]", "-map", "[aout]", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", str(out)])
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_montage(day, tmp, seconds):
    """Rapid-fire wipes through the month's before/afters."""
    days = [x["day"] for x in SCRIPTS["videos"]
            if not x.get("montage") and (day_dir(x["day"]) / "after.jpg").exists()]
    each = 1.5
    count = max(1, min(len(days), int(seconds // each)))
    pick = [days[round(i * (len(days) - 1) / max(1, count - 1))] for i in range(count)]
    clips = []
    for i, d in enumerate(pick):
        c = tmp / f"m{i:02d}.mp4"
        reveal_clip(day_dir(d) / "before.jpg", day_dir(d) / "after.jpg", c, seconds / count, wipe=0.6)
        clips.append(c)
    return clips, pick[-1]


if __name__ == "__main__":
    args = sys.argv[1:]
    days = [x["day"] for x in SCRIPTS["videos"]] if args == ["all"] else [int(a) for a in args]
    for d in days:
        if not (day_dir(d) / "vo.wav").exists():
            print(f"day {d}: missing vo.wav, skipped")
            continue
        try:
            print("rendered", render(d))
        except subprocess.CalledProcessError as e:
            print(f"day {d}: ffmpeg failed\n{e.stderr.decode()[-1500:]}")
            raise
