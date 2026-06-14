---
name: tandoor-unstructured-import
description: Import a recipe whose page has no usable structured data by having the agent (Opus) extract it directly and build a fully-structured recipe via REST — bypassing Tandoor's /api/ai-import/ (Gemini) endpoint entirely. The agent fetches the page (curl + UA, agent-browser, or a logged-in session for paywalls), reads the HTML/text, extracts ingredients + steps, maps each ingredient to its first-mention step, pre-resolves foods/units to existing ids, then POSTs to /api/recipe/. Image, audit, and cleanup are delegated to tandoor-url-import. Use when the standard scraper fails or returns a hollow recipe (error:true / empty / "No usable data" / JSON-LD Recipe with empty recipeIngredient), or when the content sits behind a bot-block / paywall / login the scraper can't reach.
---

# Tandoor unstructured-page import (agent-extracted, direct REST)

## When to use

Use this skill when Tandoor's own importers **can't read the recipe**, so the
agent does the extraction instead and writes the recipe straight to the REST API.

Pick this skill over the others by what the source looks like:

| Source situation | Skill |
|---|---|
| Page has real structured data — `/api/recipe-from-source/` returns a **populated** recipe | **`tandoor-url-import`** |
| `recipe-from-source` returns `error:true` / empty recipe / "No usable data" | **this skill** |
| `recipe-from-source` returns a JSON-LD `Recipe` but `recipeIngredient` is **empty** (schema present but hollow — verified trap) | **this skill** |
| Content behind a bot-block (403) / paywall / login the scraper can't reach, but the agent can fetch it (UA / agent-browser / logged-in) | **this skill** |
| `instagram.com/reel/<id>` | **`tandoor-instagram-import`** (but see "Relationship to instagram-import") |

### Why bypass `/api/ai-import/`

`/api/ai-import/` runs the extraction through the **space's AI provider** (Gemini
Flash by default — see `.claude/skills/tandoor-instagram-import/SKILL.md` provider
table). This skill instead has **the agent (Opus) extract directly**, which:

1. **Higher extraction quality** — Opus > Gemini Flash on messy German recipe
   prose; correct umlauts/ß, sensible food/amount splitting.
2. **Avoids the markdown-fence bug** — `/api/ai-import/` does `json.loads()` on raw
   model output (`cookbook/views/api.py:2817`) and Claude providers wrap JSON in a
   ```` ```json ```` fence → `error:true` (Memory: ai-import-claude-fence-bug). The
   direct path never touches that endpoint.
3. **Fetch control** — the agent sets its own User-Agent, escalates to
   agent-browser for JS/403, and logs in for paywalled sources (e.g. zeit.de with
   a ZEIT+ session). The server-side scraper can do none of these.
4. **Mapping + dedup folded in** — because the agent builds the payload, it can
   place ingredients on the right step and bind foods to existing ids *before*
   persisting, so two cleanup stages mostly evaporate.

## What this skill owns vs. delegates

This skill owns **Phase 0–2** (fetch → extract → persist) and produces a
**clean, fully-structured** recipe:

- ingredients already distributed to the step where first used → **Phase 4
  mapping is folded in**, not a separate step;
- foods & units pre-resolved to existing ids → **much less Phase 6 cleanup**.

Everything after persist is **delegated to `tandoor-url-import`**: Phase 3
(image), Phase 5 (audit/report), Phase 6 (interactive cleanup), Phase 7 (orphan
delete). Do not duplicate that logic here.

## Credentials & network

Same as `tandoor-url-import` (read its Credentials section):
- `tandoor_token.txt` (Bearer), `tandoor_url.txt` (base URL), project root.
- **Tandoor instance** (private IP) → `curl --noproxy '*'`.
- **Public hosts** (recipe pages, images, archive.org) → plain `curl` /
  agent-browser **through the proxy**, no `--noproxy`.

## Pipeline

| # | What | Owner | Mutating? |
|---|---|---|---|
| 0 | Fetch the page (tiered: UA → agent-browser → login) | **this skill** | no |
| 1 | Extract → structured recipe (map + pre-resolve foods/units) | **this skill (Opus)** | reads API |
| 2 | Persist (`POST /api/recipe/`) | **this skill** | yes |
| 3 | Attach image | `tandoor-url-import` Phase 3 | yes |
| 5 | Verification report | `tandoor-url-import` Phase 5 | no |
| 6 | Interactive cleanup | `tandoor-url-import` Phase 6 | per-item, user-approved |
| 7 | Delete orphans | `tandoor-url-import` Phase 7 | after "yes delete" |

(There is no Phase 4 here — mapping happens in Phase 1.)

## Two modes

Same two modes as `tandoor-url-import` (**Interactive** 1–7 / **Autonomous**
0–5 + write per-recipe cleanup artifact + advance backlog, **no** food/unit
mutation). Differences:

- **Autonomous**: after persist + image, run the Phase 5 audit, write
  `docs/mdimport/cleanup/recipe-<RID>-<slug>.md` (same shape as url-import), and
  advance the backlog entry out of *Probably needs AI import* into *Doing* under
  the furthest stage reached. Record anything needing a human in that file's
  `## Problems` section. Never merge/alias/edit/delete foods or units.
- **Fetch tier** is chosen per source (see Phase 0); in autonomous bulk runs,
  decide the tier from the backlog note and don't stop to ask.

**Concurrency:** sequential (or very low). Persisting creates/looks-up shared
foods & units; parallel runs race into duplicates — same caveat as url-import.

## Phase 0 — Fetch the page

Escalate only as far as needed; most pages need only tier 1.

**Tier 1 — `curl` with a browser User-Agent** (pages that load but lack schema:
WPRM cards without JSON-LD, hollow-schema pages, free-text blog posts):
```bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
curl -sL -A "$UA" -o /tmp/page.html "$URL"     # public host → proxy, no --noproxy
```

> **Markup-class hits don't guarantee structured data.** A page can carry dozens
> of `wprm-recipe*` classes (or a `wprm_recipes` JS reference) from CSS/templates
> while the actual recipe is just **free-text prose in the article body** (e.g.
> older madamecuisine posts). Don't assume a parseable card from class counts —
> extract from the readable body text, and let the "verify real content" check
> below (ingredient words / quantity lines) decide whether content is present.

**Tier 2 — agent-browser (headless)** for JS-rendered content or a hard 403
bot-block (e.g. edeka returns a 453-byte block page to curl). See
`tandoor-instagram-import` Phase 0 for the agent-browser invocation pattern
(`--executable-path /usr/bin/chromium`, `open` → `wait --load networkidle` →
`eval` → `close --all`); extract `document.documentElement.innerHTML` or the
visible recipe text instead of og: meta tags.

**Tier 3 — agent-browser + login** for paywalled sources (e.g. zeit.de). Use a
logged-in session (the user has the subscription) so the full article body
renders, then extract as in tier 2.

**Dead-host fallback — Wayback.** If the live host is down (HTTP 500 /
connection refused) check archive.org and fetch the snapshot:
```bash
curl -s "https://archive.org/wayback/available?url=$URL" \
  | python3 -c "import sys,json; d=json.load(sys.stdin).get('archived_snapshots',{}).get('closest',{}); print(d.get('url'))"
```

### Verify real content before extracting

Do **not** extract from a paywall teaser or an empty shell. Confirm the body
carries an actual ingredient list — grep for quantity+unit lines and known
ingredient words:
```bash
python3 - <<'PY'
import re
t=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',open('/tmp/page.html',encoding='utf-8',errors='replace').read()))
print("quantity lines:", len(re.findall(r'\d+\s?(?:g|ml|EL|TL|Dose|Zehe|Stück|Bund|Prise)\b', t)))
print("teaser marker:", bool(re.search(r'weiterlesen mit|kostenlos testen|Zum Abo|entsperren', t, re.I)))
PY
```
A few real quantity lines and no teaser marker → content is present. Zero
quantity lines or a teaser marker → escalate the fetch tier (or, if paywalled,
log in) before extracting.

## Phase 1 — Extract (the agent does this)

Read the fetched HTML/text and build a `recipe` dict whose shape matches a
`recipe-from-source` response's `recipe` (so Phase 2 persist is identical to
url-import's). Do the following **in the payload you build**, not afterwards:

1. **Steps & instructions.** One step per real method paragraph. Keep
   `instruction` as clean prose. Set `order` 0,1,2,… per step.
   - **Duration ranges MUST use a plain ASCII hyphen `-`, never a Unicode dash**
     (`–` en-dash, `—` em-dash, `−` minus): write `3-5 Minuten`, `10-12 Min.`,
     not `3–5 Minuten`. The kitshn iOS app parses durations to drive its in-prep
     auto-timer, and a Unicode dash between the numbers breaks that parse. This
     applies to any numeric range tied to a time unit (Minuten/Min./Std./Stunden/
     Sekunden). Prose en-dashes elsewhere in the sentence are fine — only the
     number-dash-number-time pattern matters. When extracting from a source that
     used `–`/`—` in a duration, normalize it to `-` as you build the step.
2. **Map ingredients to first-mention step.** Apply the first-mention algorithm
   from `.claude/skills/tandoor-recipe-mapping/SKILL.md` (and its synonym table)
   while you build — each ingredient goes on the step where it's first used, not
   all on `steps[0]`. This replaces the separate Phase 4.
3. **Pre-resolve foods & units to existing ids.** For each ingredient, query the
   space and decide the binding (this is judgment, not just dedup):
   ```bash
   curl -s --noproxy '*' -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/food/?query=<name>&page_size=10"   # {count, results:[{id,name}]}
   curl -s --noproxy '*' -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/unit/?query=<name>&page_size=10"
   ```
   - `query` is a **substring** match — `Zwiebel` returns `Zwiebel`,
     `Zwiebelpulver`, `gelbe Zwiebel`, `Frühlingszwiebel`. **Pick the `iexact`
     match**, not `results[0]`. If only near-matches exist, prefer an existing
     clean food that carries nutrition (e.g. bind `rote Zwiebel` → existing
     `Zwiebel` and move "rote" to the ingredient `note`) per the cleanup
     preferences (Memory: tandoor-cleanup-preferences). Pass `{"id":N,"name":"…"}`
     for a match; `{"name":"…"}` only for a genuinely new food.
   - The serializer also get-or-creates by name (`serializer.py:916-963` food,
     `:406-421` unit) and matches `plural_name`, so a missed exact match isn't
     fatal — but resolving up front is what keeps the cleanup queue short.
4. **Per ingredient**: set `amount` (number), `unit`, `food`, `order` (int within
   the step), `original_text` (the raw line — keep it), `note` for qualifiers,
   `no_amount: true` for "Salz nach Geschmack" style.
5. **`source_url`** = the page URL (or the live URL even if you fetched Wayback).
6. **`image_url`** — capture the hero image URL into the payload (and prep notes)
   so the delegated Phase 3 has it. Source it from `og:image`, the WPRM/recipe
   card image, or the largest in-article `<img>`. `image` itself is read-only on
   create — Phase 3 attaches it via PUT; `image_url` is just the breadcrumb.
   (Observed gap: a built payload with `image_url:""` forced a manual og:image
   lookup at Phase 3 — set it here.)
7. **Keywords**: optional; pass `[]` or resolved `{id,name}` keywords. Don't
   invent the server's `✨ AI` keyword.

Skim the result for sanity (ingredient count matches the source list; no whole
lines stuffed into a food name) before persisting.

## Phase 2 — Persist

> **Use a per-recipe temp filename — never the bare `/tmp/recipe.json`.** This
> skill builds payloads by hand (unlike url-import, which overwrites a fresh
> scrape each run), so a fixed path risks **persisting a stale leftover from an
> earlier run** (observed: a previous recipe's `/tmp/recipe.json` survived and was
> nearly POSTed). And in the plan's parallel-prepare stage, N prep subagents would
> clobber each other on a shared path. Derive a slug and use it throughout:
> `SLUG=<kebab-of-recipe-name>` → `/tmp/recipe_$SLUG.json`, `/tmp/created_$SLUG.json`.

The recipe is already clean, so the only transform that still matters is the
**`order` int** requirement (persist rejects null):

```python
for si, step in enumerate(recipe["steps"]):
    step.setdefault("order", si)
    for ii, ing in enumerate(step.get("ingredients") or []):
        ing.setdefault("order", ii)
```

**Duplicate check first** (this skill gets no `duplicates` field — the scraper
isn't involved). Before POST:
```bash
curl -s --noproxy '*' -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/recipe/?query=<recipe name>&page_size=5"   # count>0 → inspect, ask/skip
```
If a plausible match exists, stop (interactive) or record a duplicate problem and
skip (autonomous) — never create a second copy.

Then POST and capture the id (note the per-recipe filenames):
```bash
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data-binary @/tmp/recipe_$SLUG.json "$BASE/api/recipe/" > /tmp/created_$SLUG.json
RID=$(python3 -c "import json; print(json.load(open('/tmp/created_$SLUG.json'))['id'])")
```

From here, **do exactly what `tandoor-url-import` says** for Phase 3 (image),
Phase 5 (audit), and Phase 6–7 (cleanup). The audit will still surface any new
foods/units that pre-resolution didn't catch.

## Relationship to instagram-import

`tandoor-instagram-import` currently sends the scraped caption to
`/api/ai-import/` (Gemini) — the very endpoint this skill exists to bypass. Its
**Phase 0 caption scrape** is still the right front-end for reels (the caption is
JS-rendered; only agent-browser gets it). The higher-quality path is:
**instagram Phase 0 (scrape caption) → this skill's Phase 1 (Opus extracts the
caption text instead of POSTing it to `/api/ai-import/`) → Phase 2 persist**.
When importing reels and you want Opus-quality extraction (or a Claude provider
would hit the fence bug), use that combination rather than the AI endpoint.
(Fully migrating instagram-import onto this engine is a separate, larger change —
not done here.)

## Don't

- **Don't call `/api/ai-import/`** — that's the endpoint we're bypassing. The
  agent extracts; it does not delegate extraction to the backend provider.
- **Don't extract from a paywall teaser or empty shell** — verify real content
  first (Phase 0), escalate the fetch tier / log in if it's missing.
- **Don't bind a food to `results[0]`** from a `?query=` — it's a substring
  match; pick the `iexact` entry or make a deliberate near-match binding.
- **Don't create near-duplicate foods** when a clean existing one carries
  nutrition — resolve ids up front; move qualifiers to the ingredient `note`.
- **Don't skip the `order`-int fix** — persist 400s on null order.
- **Don't dump all ingredients on `steps[0]`** — map at build time (that's the
  whole point of owning the payload).
- **Don't duplicate url-import's persist-onward logic** — delegate Phase 3/5/6/7.

## Related

- `.claude/skills/tandoor-url-import/SKILL.md` — Phases 3/5/6/7 (delegated), API
  quick reference, the recipe-from-source path this skill is the fallback for.
- `.claude/skills/tandoor-recipe-mapping/SKILL.md` — first-mention mapping +
  synonym table, applied during Phase 1.
- `.claude/skills/tandoor-instagram-import/SKILL.md` — reel caption scrape; the
  AI-endpoint path this skill supersedes for extraction.
- `cookbook/serializer.py:916-963` (Food), `:406-421` (Unit), `:1193-1234`
  (Recipe) — the get-or-create + nested-write behaviour the direct POST relies on.
- `contrib/api_export_import/import.py`, `contrib/migrate_space.py` — existing
  code that builds and POSTs the same recipe payload shape.
- `docs/agents/plans/2026-06-14-ai-import-cluster-remediation.md` — the backlog
  clusters this skill is the remediation method for.
