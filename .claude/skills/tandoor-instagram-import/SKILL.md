---
name: tandoor-instagram-import
description: Import an Instagram reel into Tandoor via the AI-import endpoint — scrape the post's caption + thumbnail with agent-browser (headless Chromium), POST the caption text to /api/ai-import/ with a user-chosen AI provider, then hand the resulting recipe to the standard tandoor-url-import pipeline (persist, image, map ingredients→steps, audit, cleanup). Use when the user wants to import an instagram.com/reel/ URL that the regular URL importer can't handle, or to batch-import the Instagram subsection of the backlog.
---

# Tandoor Instagram import (AI-powered)

## When to use

The user names an `instagram.com/reel/<id>` URL (or is working the **Instagram**
subsection of `docs/mdimport/backlog.md`) and wants it imported through
Tandoor's **AI** importer. Instagram reels are not structured recipe pages, so
the regular URL scraper can't read them — but the reel's **caption** almost
always contains the full recipe (ingredients, steps, often nutrition).

The human-written annotations on `### Instagram` backlog entries (a guessed title
or note) can be inaccurate or describe a different recipe than the reel's actual
current caption — always go by the freshly scraped caption, not the backlog note.

This skill only owns the **front-end** that's unique to Instagram: scrape the
caption + thumbnail, then convert the caption to a recipe via `/api/ai-import/`.
The `/api/ai-import/` response has the **exact same shape** as
`/api/recipe-from-source/`, so everything downstream (persist, image, mapping,
audit, interactive cleanup, autonomous-mode artifacts, backlog advancement,
failure routing) is **delegated verbatim to `tandoor-url-import` Phases 2–7**.

This mirrors how kitshn (the official mobile app) imports reels: load the page
in a WebView, scrape `og:description`/`og:image`, send the caption text (not the
video) to `/api/ai-import/`, attach the thumbnail afterward. See
`docs/agents/research/2026-06-13-kitshn-ai-import-flow.md`.

## Prerequisites

- **agent-browser** + a Chromium binary. In this sandbox Chromium is at
  `/usr/bin/chromium` (Chrome-for-Testing has no Linux-ARM64 build). Install
  once if missing: `npm i -g agent-browser`. Always pass
  `--executable-path /usr/bin/chromium` to the first `open`.
- The space must have **AI enabled** and at least one **working** AI provider
  (see Provider selection — not all of them work).

## Credentials & network

Same as `tandoor-url-import` (read it):
- `tandoor_token.txt` (Bearer), `tandoor_url.txt` (base URL), project root.
- **Tandoor instance** (private IP) → `curl --noproxy '*'`.
- **Public hosts** (instagram.com, the CDN image) → plain `curl` / agent-browser
  **through the proxy**, no `--noproxy`.

## Pipeline

| # | What | Owner | Mutating? |
|---|---|---|---|
| 0 | Scrape reel (caption + thumbnail) via agent-browser | **this skill** | no |
| 1 | AI import: POST caption to `/api/ai-import/` | **this skill** | no |
| 2 | Persist recipe | `tandoor-url-import` Phase 2 | yes |
| 3 | Attach image (Instagram-specific note below) | `tandoor-url-import` Phase 3 | yes |
| 4 | Map ingredients → steps | `tandoor-recipe-mapping` (via url-import P4) | yes |
| 5 | Verification report | `tandoor-url-import` Phase 5 | no |
| 6 | Interactive cleanup | `tandoor-url-import` Phase 6 | per-item, user-approved |
| 7 | Delete orphans | `tandoor-url-import` Phase 7 | after "yes delete" |

After Phase 1 you hold a `recipe` dict + `images` + `duplicates` — identical in
shape to a `recipe-from-source` response. **From there, do exactly what
`tandoor-url-import` says**, including its `order: null` → int fix and
`import_keyword` keyword pre-filter in Phase 2, the duplicates/error handling,
and its two-mode behaviour.

## Two modes

Same two modes as `tandoor-url-import` (Interactive 1–7 / Autonomous 1–5 + write
artifact + advance backlog, no food/unit mutation). The only differences:

- **Provider selection** (Phase 1): in **Interactive** mode, prompt the user to
  pick a provider per run. In **Autonomous** mode, ask once at the **start of the
  run** (or take it as a parameter) and reuse it for every reel — never stop
  per-recipe.
- **Backlog section**: operate on `## Instagram / Facebook / freeform` →
  `### Instagram`, not the *Standard URL backlog*. Advance/route within that
  list (see Failure routing).

## Phase 0 — Scrape the reel (agent-browser)

Load the reel with a real headless browser (Instagram's caption is JS-rendered;
a plain `curl` of the URL returns an empty shell). One session, then extract the
same meta tags kitshn's script reads.

```bash
agent-browser --executable-path /usr/bin/chromium open "$URL"
agent-browser wait --load networkidle
cat <<'EOF' | agent-browser eval --stdin > /tmp/ig_scrape.json
const cand = ["meta[property='og:description']","meta[property='twitter:description']","meta[property='og:title']"]
  .map(s => document.querySelector(s)?.content).filter(Boolean);
const description = cand.sort((a,b)=>b.length-a.length)[0] || null;   // longest = the caption
const imageURL = document.querySelector("meta[property='og:image']")?.content || null;  // play-buttoned; DON'T attach
const imgs = Array.from(document.querySelectorAll('img'))
  .map(i => ({src: i.src, w: i.naturalWidth, h: i.naturalHeight})).filter(i => i.w > 200);  // for the clean cover frame (Phase 3)
const html = document.documentElement.innerHTML;
const grab = (re) => {const s=new Set();let m;while((m=re.exec(html))){s.add(m[1]);}return [...s];};
const videoURLs = grab(/"video_versions".{0,60}?"url":"(https:[^"]+?)"/g);  // for the video-sample fallback (Phase 3)
JSON.stringify({description, imageURL, imgs, videoURLs});
EOF
agent-browser close --all
```

**Parsing gotcha:** `agent-browser eval` JSON-encodes its return value, so the
file holds a *quoted* JSON string. Decode twice:

```python
import json
d = json.loads(json.load(open('/tmp/ig_scrape.json')))   # outer: unwrap; inner: our object
caption, image_url = d['description'], d['imageURL']
```

Checks before continuing:
- `description` empty/None → the page didn't render the caption (login wall,
  age-gate, deleted, or rate-limited). Do **not** call the AI. Route as a
  problem (see Failure routing).
- Save `imgs` + `videoURLs` for Phase 3 (the clean cover frame and the
  video-sample fallback — **not** `image_url`/`og:image`, which is play-buttoned).
  These are **signed CDN URLs that expire** (`oe=` query param) — download
  promptly during this run; don't stash for later.

## Phase 1 — AI import (caption → recipe)

> **Higher-quality alternative — bypass this endpoint.** `/api/ai-import/` runs
> the caption through the space's AI provider (Gemini Flash) and is subject to the
> Claude markdown-fence bug. For Opus-quality extraction, instead hand the scraped
> caption text to **`tandoor-unstructured-import` Phase 1** (the agent extracts the
> caption directly, maps ingredients, pre-resolves foods, and POSTs to
> `/api/recipe/`) — no AI-endpoint call at all. Use that path when you want the
> best extraction or a Claude provider would hit the fence bug; use the
> `/api/ai-import/` path below when you specifically want the server-side
> provider. (Fully migrating this skill onto the direct engine is a separate
> change.)

`POST /api/ai-import/` is **multipart** (`MultiPartParser`; JSON body → 415).
Fields (match kitshn): `recipe_id` (empty), `file` (empty — we send text only),
`text` (the caption), `ai_provider_id` (chosen provider).

Write the caption to a file first (it has newlines, emojis, umlauts — passing it
inline is fragile), then use curl's read-value-from-file form `text=<file`:

```bash
# caption already written to /tmp/desc.txt during Phase 0 parsing
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -F "recipe_id=" \
  -F "file=" \
  -F "text=</tmp/desc.txt" \
  -F "ai_provider_id=$PROVIDER_ID" \
  -w "\nHTTP_CODE=%{http_code}\n" \
  "$BASE/api/ai-import/" -o /tmp/ai_import.json
```

> `-F "text=<file"` reads the field **value** from the file. `-F "text=@file"`
> would upload it as an attachment (wrong) and `<@file` errors (curl 26).

The response is `RecipeFromSourceResponseSerializer`:
`{recipe, recipe_id, images, error, msg, duplicates}` — same as recipe-from-source.

Body-level checks (the endpoint returns 200 with `error:true` for soft failures):
- `error: true` → inspect `msg`. The common one is the **markdown-fence bug**
  (see below). Otherwise report and route as a problem.
- `duplicates` non-empty → stop / record (same rule as url-import; never create
  a second copy).
- A server-appended `✨ AI` keyword is normal — leave it.

**Set `source_url` before persisting.** The `/api/ai-import/` response leaves
`recipe.source_url` empty (it received caption *text*, not a URL), so the
recipe would persist with no provenance link back to the reel. Inject it:

```python
recipe["source_url"] = URL   # the instagram.com/reel/... URL from Phase 0
```

(Verified 2026-06-13: a reel imported without this had `source_url: ''`; patch it
afterwards with `PATCH /api/recipe/$RID/ {"source_url": "<reel URL>"}` if missed.)

Then hand `recipe` / `images` / `duplicates` to **`tandoor-url-import` Phase 2**
and continue through its pipeline.

### Provider selection (fetch from backend, ask the user)

List providers live and present them to the user (the space currently has **no
default**, so a provider must be chosen):

```bash
curl -sS --noproxy '*' -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/ai-provider/?page_size=100" \
  | python3 -c "import json,sys; [print(p['id'],'|',p['name'],'|',p.get('description','')[:70]) for p in json.load(sys.stdin)['results']]"
```

Show the list, let the user pick the `id`, pass it as `ai_provider_id`.

### ⚠ Provider gotcha — not all providers work (verified 2026-06-13)

The endpoint does `json.loads()` on the model's raw output
(`cookbook/views/api.py:2817`). It requests `response_format=json_object`, but
some OpenRouter models **wrap their JSON in a ```json … ``` markdown fence**,
which makes `json.loads` fail → `error:true`, `msg` starts with
`Error parsing AI results. Response Text:\n\n` + a fenced block. The recipe was
extracted fine; only the fence breaks it.

Empirical results on a German reel caption (`/reel/DP33g0hjCdc/`):

| Provider | id | Result |
|---|---|---|
| **OR Gemini Flash 3.5** | 1 | ✅ **Best** — clean JSON, correct umlauts/ß, 12 ingredients parsed cleanly |
| OR DeepSeek V3 | 4 | ✅ Works, correct German, tends to split into more steps |
| OR GPT-4o | 3 | ⚠ Works but **mangles non-ASCII** (ö/ß corrupted) and sometimes stuffs a whole line into the food name — poor for German |
| OR Claude Sonnet 4.5 | 2 | ❌ Markdown-fence bug → `error:true` |
| OR Claude Haiku 4.5 | 8 | ❌ Markdown-fence bug → `error:true` |

Guidance:
- **Recommend Gemini Flash 3.5 (id 1)** for German recipes; DeepSeek (4) is a
  fine alternative. Avoid GPT-4o for non-English.
- **Warn the user that Claude providers currently fail** this endpoint. Detect
  it: `error:true` AND `msg` contains ```` ```json ````. On that failure,
  suggest re-running with a non-Claude provider.
- Provider ids are space-specific and can change — match by the failure
  *signature*, not hard-coded ids.
- **Autonomous mode:** default to a known-good provider (Gemini Flash) chosen
  once; if a reel returns the fence error, record it as a problem and continue.

## Phase 3 note — image (Instagram-specific): get a CLEAN thumbnail

**Do not attach `og:image`.** On reels the `og:image` is the `best_image_urlgen`
square thumbnail with Instagram's **play-button overlay + the creator's title
text baked into the pixels** — re-fetching it can't remove them (verified
2026-06-14, see `docs/agents/research/2026-06-14-instagram-reel-clean-thumbnail.md`).
Use a clean source instead, then PUT via download-then-upload (Instagram's CDN
serves short-lived signed URLs and may block Tandoor's server-side fetcher;
bytes download cleanly through the proxy).

**Primary — the clean cover frame from the DOM.** Each reel page carries exactly
one `<img>` whose `efg` query param decodes to a `vencode_tag` containing
`video_additional_cover_frame` (640×1136, 9:16, no overlay). Every other `<img>`
is a related-reel thumbnail (all play-buttoned) — don't pick those. Harvest the
`<img>` srcs in Phase 0 and select with:

```python
import json, base64, urllib.parse as up
def efg_tag(u):
    q = up.parse_qs(up.urlparse(u).query); raw = q.get('efg', [''])[0]
    raw += '=' * (-len(raw) % 4)
    return base64.b64decode(raw).decode('utf8', 'ignore') if raw else ''
clean = next((i['src'] for i in imgs if 'video_additional_cover_frame' in efg_tag(i['src'])), None)
```

```bash
curl -sS -L "$CLEAN_URL" -o /tmp/ig_hero.jpg          # public CDN → proxy, no --noproxy
curl -sS --noproxy '*' -X PUT -H "Authorization: Bearer $TOKEN" \
  -F "image=@/tmp/ig_hero.jpg" "$BASE/api/recipe/$RID/image/"
```

**Fallback — sample the video when the clean frame is poor.** The cover frame is
an *arbitrary* video frame; sometimes it's awkward (subject half-out of frame,
mostly-empty background). When it is, download the reel's mp4 and pick the best
frame by eye. The real progressive mp4 URL is in the page's embedded
`video_versions` JSON (the `<video src>` is a useless `blob:` URL); it's
JSON-escaped — unescape `\/`→`/` and `%`→`%`.

```bash
curl -sS -L "$VIDEO_URL" -o /tmp/reel.mp4             # public CDN → proxy
# contact sheet: one timestamped frame every 2s, tiled
ffmpeg -y -i /tmp/reel.mp4 -vf "fps=1/2,scale=240:-1,drawtext=text='%{pts\:hms}':x=4:y=4:fontsize=18:fontcolor=yellow:box=1:boxcolor=black@0.6" /tmp/f_%02d.jpg -hide_banner -loglevel error
ffmpeg -y -pattern_type glob -i '/tmp/f_*.jpg' -filter_complex "tile=5x4:margin=4:padding=4" /tmp/sheet.jpg -hide_banner -loglevel error
# read sheet.jpg, choose a timestamp, then re-extract full-res with -ss AFTER -i (decode-accurate)
ffmpeg -y -i /tmp/reel.mp4 -ss "$TS" -frames:v 1 -q:v 2 /tmp/ig_hero.jpg -hide_banner -loglevel error
```

- Reels usually open on the plated beauty shot at t≈0–0.5 and repeat it near the
  end — those are the best covers.
- **Seeking gotcha:** input seek (`-ss` *before* `-i`) snaps to a keyframe and
  lands on the wrong/blurred frame. For the final full-res extract put `-ss`
  *after* `-i`.

The temp file must carry a real image extension (`.jpg`/`.png`/`.webp`), not
`.img`, or the PUT 400s with "File extension 'img' is not allowed."

If any CDN URL (cover frame or mp4) has expired between Phase 0 and here,
re-scrape Phase 0 to refresh it.

## Failure routing (Instagram-specific)

Add these to `tandoor-url-import`'s routing rules:

- **Caption didn't render** (Phase 0 empty `description`): login wall / private /
  age-gated / deleted / rate-limited. Don't call the AI. Leave the URL in the
  `### Instagram` list with an inline reason (e.g. "caption not rendered —
  login wall"). In autonomous mode, record it and move on.
- **AI fence error** (Claude provider): record "provider X returned fenced JSON"
  and either retry with a non-Claude provider (interactive) or note + continue
  (autonomous).
- **Caption has no real recipe** (just marketing / "recipe in comments"): the AI
  returns a thin/empty recipe — treat like a soft scrape error; don't persist.
- **Caption has ingredients but no method** → persist anyway, but record a problem
  note "no cooking method in caption — steps must be added manually" (autonomous)
  or flag to the user (interactive).
- **Facebook URLs** in the backlog are out of scope: both `story.php` and
  private-group `permalink` forms sit behind a login/members wall and don't
  expose the caption anonymously (verified 2026-06-13). Don't attempt them with
  this skill; they need a logged-in agent-browser session or manual paste.

## Don't

- Don't send the reel **URL** to `/api/ai-import/` — it takes `text`/`file`
  only, not a URL. The URL goes to the browser in Phase 0.
- Don't send the **video** or expect video understanding — only the caption text
  is used (same as kitshn).
- Don't `curl` the instagram.com URL directly expecting the caption — it's
  JS-rendered; use agent-browser.
- Don't duplicate `tandoor-url-import`'s persist/map/audit/cleanup logic here —
  delegate. Keep this skill to Phases 0–1.
- Don't stop an autonomous run to pick a provider per recipe — choose once.
- Don't reuse a stale `og:image` URL across runs — re-scrape if expired.

## Empirical notes (2026-06-13)

- Anonymous reel scrape succeeded for two different creators
  (`/reel/DP33g0hjCdc/`, `/reel/C_azGRMoCGs/`): full caption in `og:description`
  (ingredients + numbered steps + nutrition) and a usable `og:image` thumbnail.
- `og:title` carries the same caption text as `og:description` on reels — the
  "longest candidate" rule picks whichever is fuller.
- AI output comes back with **all ingredients on `steps[0]`** and `order: null`
  throughout → the url-import Phase 2 order-fix and Phase 4 mapping are both
  required, exactly as for URL scrapes.
- **Method-poor captions are common:** a reel's caption frequently lists the full
  ingredient set but **no written method** (the steps are only spoken/shown in the
  video). `/api/ai-import/` returns `error:false` and parses ingredients fine, but
  the recipe has a single step with an empty `instruction` (or no real method
  steps). This is not a scrape failure — persist it — but it's ingredient-rich and
  method-poor and needs a human to add the method (see Failure routing).

## Related

- `docs/agents/research/2026-06-13-kitshn-ai-import-flow.md` — how kitshn/the
  server implement AI import; the contract this skill targets.
- `.claude/skills/tandoor-url-import/SKILL.md` — Phases 2–7 (the delegated pipeline).
- `.claude/skills/tandoor-recipe-mapping/SKILL.md` — ingredient→step mapping.
- `.claude/skills/tandoor-ai-providers/SKILL.md` — provider lineup & model notes.
