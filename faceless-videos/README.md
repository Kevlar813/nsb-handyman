# NSB Handyman: Faceless Pressure-Washing Videos

10 short vertical videos (about 20–25 s each) promoting the **Fall Special: $99 per house, $79 per driveway,
ending November 30**. They're for YouTube Shorts, TikTok, Instagram Reels and Facebook Reels across Ormond Beach,
Daytona Beach, South Daytona, Daytona Beach Shores, Port Orange, New Smyrna and Edgewater, FL.

| What | Where |
|---|---|
| Posting calendar (10 videos, Oct 24 to Nov 28) | `content/calendar.md` |
| Scripts, captions, hashtags | `content/scripts.md` (source of truth: `content/scripts.json`) |
| Logo | `brand/logo.svg`, `brand/logo-1000.png`, `brand/logo-400.png` |
| Bios, pinned comment, Act Now button setup | `brand/bios.md` |
| Thumbnail template | `brand/thumbnail_template.html` |
| Finished videos | `output/videos/videoNN.mp4` |
| Thumbnails | `output/thumbnails/videoNN_cover.png` (9:16), `videoNN_yt.png` (16:9) |

## How a video is built
`assets/gen/dNN/` holds `before.jpg`, `after.jpg`, an optional `process.mp4` clip, and `vo.wav`.
`assets/gen/cta.wav` holds the shared call-to-action voiceover.

```
python3 tools/make_audio.py assets      # original music + SFX (already generated)
python3 tools/render_video.py all       # videos
python3 tools/render_thumbs.py all      # thumbnails
python3 tools/build_docs.py             # calendar, scripts, bios
```

Phone, email, prices, deadline and service area all come from `config.json`. Change them there and re-run
the three commands above to update every video, thumbnail and bio.

## Swapping in real job photos
Put real before/after photos (same angle) in `assets/gen/dNN/` and re-render. Real results convert better.
Once all the footage is real, you can remove the "AI illustration" tag by setting `"ai_label": ""` in `config.json`.
