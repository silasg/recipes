# Instagram reel clean-thumbnail research (Task A1)

**Date:** 2026-06-14
**Context:** Reel imports attach a thumbnail with Instagram's play-button overlay (ugly). Spike to confirm where the overlay lives and find a clean cover source reachable anonymously via agent-browser. Gates Task A2 (skill edit) and A3 (backfill).

## Findings (verified on reels 50 `C_azGRMoCGs`, 68 `C9z59JOK5mm`, 4 `DRKkDzLCelg`)

1. **The play button + the creator's title text are baked into the `og:image` pixels.** The `og:image` URL is the `best_image_urlgen` square (`stp=...s640x640`, `efg` tag `{"efg_tag":"CLIPS.best_image_urlgen.C3"}`). Instagram composites the play affordance + caption text server-side. Re-fetching the same URL cannot remove them. Confirmed by eye on RID 50's attached image.

2. **A clean source exists in the DOM.** Each reel page carries exactly one `<img>` whose `efg` query param decodes to a **`vencode_tag` containing `video_additional_cover_frame`** (640×1136, full 9:16). It is a raw video frame — **no play button, no title overlay** — and downloads cleanly through the proxy. Every *other* `<img>` on the page is a related-reel thumbnail (`best_image_urlgen` / `CAROUSEL_ITEM.best_image_urlgen`, all play-buttoned) — do not pick those.

   **Selection rule:** base64-decode each candidate `<img>`'s `efg` param; pick the one whose decoded JSON contains `video_additional_cover_frame`. Robust across all 3 reels (always present, always first, always 640×1136).

3. **Caveat — it's an arbitrary frame, not the composed cover, so quality is hit-or-miss.** A composed cover *without* the play button is **not** exposed anonymously.
   - RID 50: ✅ great (dressing pour)
   - RID 4: ✅ good (action shot)
   - RID 68: ⚠️ weak (bowl half-out of frame, mostly empty cutting board)

## Chosen strategy (user decision, 2026-06-14)

**Default to the clean `video_additional_cover_frame`; when that frame is clearly poor, fall back to sampling the reel video and picking the best frame.** (Do *not* fall back to keeping the play-buttoned `og:image`.)

### Fallback — sample the video, pick the best frame (proven on RID 68)

The real progressive mp4 URL is recoverable from the page's embedded `video_versions` JSON (the `<video>` tag `src` is a useless `blob:` URL). It is JSON-escaped — unescape `\/`→`/` and `%`→`%`.

```bash
# 1. download the mp4 (public CDN → proxy, no --noproxy)
curl -sS -L "$VIDEO_URL" -o /tmp/reel.mp4
# 2. contact sheet: one frame every 2s, timestamped, tiled
ffmpeg -y -i /tmp/reel.mp4 -vf "fps=1/2,scale=240:-1,drawtext=text='%{pts\:hms}':x=4:y=4:fontsize=18:fontcolor=yellow:box=1:boxcolor=black@0.6" /tmp/f_%02d.jpg -hide_banner -loglevel error
ffmpeg -y -pattern_type glob -i '/tmp/f_*.jpg' -filter_complex "tile=5x4:margin=4:padding=4" /tmp/sheet.jpg -hide_banner -loglevel error
# 3. (agent reads sheet.jpg, picks a timestamp) re-extract at full res with DECODE-ACCURATE OUTPUT seeking
ffmpeg -y -i /tmp/reel.mp4 -ss "$TS" -frames:v 1 -q:v 2 /tmp/best.jpg -hide_banner -loglevel error
```

- **Seeking gotcha:** input seeking (`-ss` *before* `-i`) snaps to the nearest keyframe and lands on the wrong (often motion-blurred) frame. Put `-ss` *after* `-i` for the final full-res extract.
- Reels typically open on the plated beauty shot at t≈0–0.5 and repeat it near the end; those are usually the best covers. RID 68's winner was **t=0.5** (clean centered plate) vs the awkward `video_additional_cover_frame`.
- Frames are 720×1280. Save as `.jpg` (never `.img`) and PUT via `PUT /api/recipe/<RID>/image/`.

## Tools / environment

- agent-browser `/usr/bin/chromium`; `ffmpeg`/`ffprobe` at `/usr/bin`; ImageMagick (`montage`/`convert`) also present.
- CDN URLs (`og:image`, the cover-frame `<img>`, the mp4) are signed and **expire** (`oe=`/`oh=`) — scrape→download in one pass, re-scrape if stale.

## Outcome

- **A2:** folded into `tandoor-instagram-import` Phase 3 (clean-frame-first, video-sample fallback). ✅ done.
- **A3:** autonomous re-image backfill of every DB recipe whose `source_url` contains `instagram.com/reel/`. ✅ done — see below.

## A3 backfill results (2026-06-14)

Enumerated **30** reel-based recipes from the DB (the 26-recipe batch RID 49–74 plus 4 older ones: 4, 10, 15, 21). All were re-imaged except the one deleted reel.

- **Clean cover frame kept (12):** RID 4, 50, 51, 53, 54, 57, 58, 62, 66, 69, 73, 74 — the `video_additional_cover_frame` was already a good food shot; attached as-is.
- **Video-sample fallback (17):** RID 10, 21, 49, 52, 55, 56, 59, 60, 61, 63, 64, 65, 67, 68, 70, 71, 72 — the cover frame was awkward (empty background, person/process shot, motion blur, or text-dominated), so the mp4 was sampled and the best plated/finished-dish frame chosen by eye. Chosen timestamps: 10@16s, 21@12s, 49@28s, 52@12s, 55@0s, 56@2s, 59@4s, 60@14s, 61@2s, 63@20s, 64@2s, 65@24s, 67@2s, 68@2s, 70@46s, 71@22s, 72@2s.
- **Skipped (1):** **RID 15** (Veganes Käsefondue, reel `DSDFPVOjElN`) — reel is **deleted** ("Post isn't available"); no clean source exists, so it keeps its existing (play-buttoned) image.

All 29 re-images PUT with HTTP 200; spot-verified live (RID 68, 49) that the server image is now the clean frame.
