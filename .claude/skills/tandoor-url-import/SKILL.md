---
name: tandoor-url-import
description: End-to-end Tandoor recipe import from a URL plus the post-import revision workflow — scrape, persist, attach image, distribute ingredients across steps, audit nutrition and food/unit cleanliness, then interactively merge or rewire artifacts and create alias automations. Use when the user wants to import a recipe URL through Tandoor's standard importer and walk through the cleanup before moving on to the next one in the backlog.
---

# Tandoor URL import + post-import revision

## When to use

The user is working through `docs/mdimport/backlog.md` (or naming a single URL) and wants to import it through Tandoor's standard URL importer, then sit with you while you walk through the standard cleanup: distribute ingredients across cooking steps, merge import-artifact foods/units into clean canonical versions, and create FOOD_ALIAS / UNIT_ALIAS automations so the next import skips that work.

This skill is the orchestrator. It delegates Phase 4 (ingredient↔step mapping) to the existing `tandoor-recipe-mapping` skill.

## Credentials

From project root (gitignored):
- `tandoor_token.txt` — Bearer token
- `tandoor_url.txt` — Base URL

Network: route by **destination**, and treat this as environment-dependent, not gospel.
- **Tandoor instance** (private IP): reach it with `curl --noproxy '*'`. Python's `urllib` is blocked against private IPs in some sandboxes, so default to `curl` for HTTP and `python3` only for JSON munging.
- **Public hosts** (recipe pages, remote images): go through the proxy — i.e. plain `curl` **without** `--noproxy`. Verified 2026-06-13: in this sandbox the firewall forces all public egress through the proxy; `--noproxy` against a public host fails to connect. Do **not** reflexively add `--noproxy` to external fetches (this once caused a false "image host unreachable" conclusion).
- A public fetch returning a non-2xx (e.g. `403` bot block, `302` redirect) is a **per-host, expected outcome** — record the specific status as a recipe problem (see Autonomous mode) and move on; it is not a proxy misconfiguration to retry forever.

## Pipeline

| # | What | Mutating? |
|---|---|---|
| 1 | Scrape | no |
| 2 | Persist | yes |
| 3 | Attach image | yes |
| 4 | Map ingredients → steps (delegate to `tandoor-recipe-mapping`) | yes |
| 5 | Build verification report | no |
| 6 | Interactive cleanup | per-item, with explicit user approval each time |
| 7 | Delete orphan foods/units | only after user "yes delete" |

Stop after Phase 5 and present the report. Phase 6 is human-in-the-loop — never bundle cleanup decisions.

## Two modes

This skill runs in one of two modes; the caller (usually `tandoor-backlog-run`, or the user) says which.

| Mode | Phases | Mutates foods/units? | Backlog/artifacts | Human in loop? |
|---|---|---|---|---|
| **Interactive** (default) | 1–7 | yes (Phase 6/7) | not touched by this skill | yes — every cleanup item |
| **Autonomous** | 1–5 + write artifacts + advance backlog | **no** — never merge/alias/edit/delete | **writes** per-recipe cleanup file + advances backlog stage | no |

### Autonomous mode (stages 1–3 + audit, no mutation of foods/units)

Used when importing the backlog in bulk so a later interactive session can do all Phase 6/7 cleanup at once (with global cruft dedup). Per recipe:

1. Run **Phase 1–3** (scrape → persist → image) and **Phase 5** (audit). Skip Phase 4's *delegation style* note below — mapping still happens (it's stage 3), but via the contract in `tandoor-recipe-mapping` ("Mapping as a delegable step").
2. **Never** run Phase 6 or 7. Do not merge, alias, edit, or delete any food/unit. The audit only *records* what's new.
3. **Write the per-recipe cleanup artifact** to `docs/mdimport/cleanup/recipe-<RID>-<slug>.md` — same shape as the worked example `docs/mdimport/cleanup/recipe-49-one-pot-pasta-rote-bete.md` (recipe id + source + new foods/units table with suggested action + target id + nutrition coverage, plus a `## Problems` section). This is the input the batch cleanup session consumes.
4. **Advance the backlog** (`docs/mdimport/backlog.md`): move the URL out of *Standard URL backlog* into *Doing* under the **furthest stage it reached** (3 Mapped normally; 1 Imported if image failed; etc.), with a one-line "next:" pointer to the cleanup file.
5. **Record problems** for the joint pass. Anything needing a human — see Failure routing — goes into a `## Problems` section of that recipe's cleanup file (specific and actionable, e.g. "image 403 from host", "2 ingredients unmapped: X, Y", "duplicate of recipe 14"). Do not stop the run for these; note and continue.

**Failure routing (autonomous):**
- Soft scrape error (`error:true` / `msg` / empty recipe) → do **not** persist. Move the URL from *Standard URL backlog* to *Probably needs AI import* with the reason inline. Continue.
- `duplicates` non-empty → do **not** persist (never create a second copy). Leave the URL in place, record the duplicate id in a problem note for the user. Continue.
- Image both-paths fail → record `image: pending`, advance recipe to Stage 1, continue.
- Mapping leaves ingredients unmapped → leave them on the dump step, list them in the problem note. Continue.

**Resumability:** the backlog file is the checkpoint. Before processing a URL, skip it if it is already in *Doing*/*Done* or already moved to *Probably needs AI import*. A run interrupted by context compaction resumes by re-reading the backlog.

**Concurrency:** run recipes **sequentially** (or very low concurrency). Imports create/look-up shared foods & units; parallel runs can race into duplicate foods.

## Phase 1 — Scrape

```bash
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"url\":\"$URL\"}" \
  "$BASE/api/recipe-from-source/" > /tmp/scrape.json
```

Check the body, not just the status (the endpoint returns 200 even on soft errors):
- `duplicates` non-empty → stop and ask. Tandoor will happily create a second copy.
- `error: true` or `msg` non-empty → report and bail.

Capture: `recipe` dict, `images` list, `recipe.image_url`.

## Phase 2 — Persist

Two transformations on the scrape payload before POST:

1. **Pre-filter keywords by `import_keyword`.** The scraper returns every scraped keyword; the frontend (`vue3/src/pages/RecipeImportPage.vue:810`) drops the ones without an iexact match in the space:
   ```python
   recipe["keywords"] = [k for k in recipe["keywords"] if k.get("import_keyword")]
   ```

2. **Replace `order: null` with sequential ints.** Every scraped ingredient has `order: null`; persist rejects null with `{"order": ["This field may not be null."]}`:
   ```python
   for step_idx, step in enumerate(recipe["steps"]):
       step.setdefault("order", step_idx)
       for ing_idx, ing in enumerate(step.get("ingredients") or []):
           if ing.get("order") is None:
               ing["order"] = ing_idx
   ```

Then POST and capture the new id:
```bash
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data-binary @/tmp/recipe.json "$BASE/api/recipe/" > /tmp/created.json
RID=$(python3 -c "import json; print(json.load(open('/tmp/created.json'))['id'])")
```

## Phase 3 — Attach image

The action sets `parser_classes=[MultiPartParser]` (`cookbook/views/api.py:1840`), so JSON returns 415. Send multipart.

**Two ways to attach, in order of preference:**

1. **`image_url=` (server-side fetch).** Tandoor downloads the URL itself:
   ```bash
   curl -sS --noproxy '*' -X PUT -H "Authorization: Bearer $TOKEN" \
     -F "image_url=$IMAGE_URL" "$BASE/api/recipe/$RID/image/"
   ```
   This can return **HTTP 500** (generic Django error page) when the Tandoor server can't fetch the URL — e.g. the source host blocks its fetcher, or the URL has non-ASCII chars. The 500 is opaque; don't burn cycles URL-encoding. Fall back to (2).

2. **`image=@file` (we supply the bytes).** Download the image **through the proxy** (plain `curl`, NO `--noproxy` — see Credentials), then upload the file to the instance with `--noproxy`:
   ```bash
   curl -sS -L "$IMAGE_URL" -o /tmp/hero.jpg          # public host → proxy, no --noproxy
   curl -sS --noproxy '*' -X PUT -H "Authorization: Bearer $TOKEN" \
     -F "image=@/tmp/hero.jpg" "$BASE/api/recipe/$RID/image/"
   ```
   The temp file must carry a real image extension (`.jpg`/`.png`/`.webp`), not `.img`, or the PUT 400s with "File extension 'img' is not allowed."
   `curl` handles non-ASCII paths fine on the download leg. Verify success: response `image` field is a non-null `/media/recipes/...` path.

Skip if `recipe.image_url` is empty. In **Autonomous mode**, if both paths fail (e.g. source 403s the download too), do **not** block — record `image: pending (<reason>)` as a recipe problem and continue; the recipe still advances (it just sits at Stage 1 instead of Stage 2).

## Phase 4 — Distribute ingredients across steps

Delegate: invoke `tandoor-recipe-mapping` with `recipe_id=$RID`. After persist, all ingredients sit on `steps[0]`; that skill redistributes them by first-mention. Consult its synonym table — the section "Synonyms learned" below records additions worth folding back in.

## Phase 5 — Verification report

Fetch (reads only):
- `GET /api/recipe/$RID/`
- `GET /api/property-type/` — the nutritional dimensions defined in the space
- For each distinct food id: `GET /api/food/$FID/?extended=1` — yields `numrecipe` + `properties`
- For each distinct unit id: `GET /api/unit/$UID/?extended=1`
- For each food/unit with `numrecipe == 1`: `GET /api/recipe/?foods=$FID` (or `?units=`) to confirm the lone reference is `$RID` — this is the proxy for "created during this import" because Food/Unit have no `created_at`.

Then present **two tables** to the user.

### Table A — Ingredients

```
#  step  food (id)                amount  unit (id)     note         original_text       origin           nutrition
1   1    Risottoreis (358)        100     g (12)        ''           '100 g Risottoreis'  new            0/4
…
```

- **origin** = "**new**" iff `numrecipe == 1 AND only $RID uses it`; else `pre-existing (N recipes)`.
- Apply the same logic to the **unit** column independently — units like `Zehe/n` are commonly import artifacts too.
- **nutrition** = `<distinct property_type.id covered> / <total property types in space>`. Dedupe by id; raw `len(food.properties)` may be higher because a food can have multiple records covering the same type.

### Table B — Steps

```
step  pk  ingredients  instruction excerpt
1     75  8            "Zwiebel, Knoblauch, Paprika und Zucchini klein würfeln…"
…
```

### Summary line

> N new foods, M new units, K foods with zero nutrition, L pre-existing foods missing nutrition.

Then stop. Wait for the user before Phase 6.

## Phase 6 — Interactive cleanup

Classify each candidate, propose one action verb, ask. Don't bundle; the user has revealed they want per-item control over what becomes an alias vs. a per-ingredient edit.

### Classification table

| Pattern | Example we've seen | Proposed action |
|---|---|---|
| Cruft name + clean version exists in space | `Zwiebel(n)` (new) + `Zwiebel` (existing, full nutrition) | **merge + FOOD_ALIAS** |
| Form variant + clean version exists | `Zimtpulver` + `Zimt` | **merge + FOOD_ALIAS** |
| Unit cruft + clean version exists | `Zehe/n` + `Zehe` (plural `Zehen`) | **merge + UNIT_ALIAS** |
| Adjective prefix where the qualifier matters | `kleine Zucchini`, `etwas Butter` | **edit ingredient in-place** (rebind food to clean target, set `note` to qualifier), then **delete orphan**. **No alias** — would silently drop the qualifier on future scrapes. |
| Note duplicates food name | food `Dattel getrocknet` + note `getrocknete` | **clear the note** on the ingredient |
| Stray dirty pre-existing entry surfaced by the audit | `Zwiebel, frisch` (unused, found while searching for Zwiebel) | offer merge (no alias) to consolidate, only if it's been there a while and the user agrees |
| Genuinely new ingredient | `Safran`, `Risottoreis` | leave; offer to backfill nutrition properties (separate workflow) |
| Cruft name with no clean target | — | rename in place via Food editor (out of scope here) |

### How to present each decision

For each candidate, give the user:
- source identity: `(id) name`, the affected ingredient row from recipe ($RID), and `original_text`
- target identity (if any): `(id) name`, target's `numrecipe`, target's nutrition coverage
- proposed action verb: `merge + alias` / `merge` / `edit + delete orphan` / `clear note` / `skip`

Then ask. Wait for "yes" / "go" / "do it" before executing.

### Wire calls

**Merge** (Food/Unit/Keyword; SupermarketCategory has merge but no alias type):
```bash
curl -sS --noproxy '*' -X PUT \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' \
  "$BASE/api/{food|unit|keyword}/$SRC/merge/$DST/"
# response: {"msg":"<src> was merged successfully with <dst>"}
```
All reverse-FK and M2M relations are reassigned to target, then source is deleted. The endpoint does NOT accept an automation flag — alias creation is a separate call.

**Create alias automation:**
```bash
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"type\":\"FOOD_ALIAS\",\"name\":\"Merge $SRC -> $DST\",\"param_1\":\"$SRC\",\"param_2\":\"$DST\"}" \
  "$BASE/api/automation/"
```
- `type` = `FOOD_ALIAS` | `UNIT_ALIAS` | `KEYWORD_ALIAS`
- **Matching is name-based, not id-based** (verified 2026-06-13 via source `cookbook/helper/automation_helper.py` + a real `{data}` scrape): `param_1` is the source **name** string matched `iexact` against the parser's emitted name; `param_2` is the replacement **name** string. Applied at parse time. So `Gramm`→`g`, `Esslöffel`→`EL`, etc. work as plain name normalizations; never pass ids. (Empirical proof: a `100 Gramm Mehl` line scraped to unit `g` with the alias present, while `2 Liter Wasser` stayed `Liter` until its own alias was added.)
- Wire format is **snake_case** (`param_1`, not `param1`). The TS client camelCases at the boundary; raw HTTP uses snake_case.
- `name` ≤ 128 chars. The UI uses localized "Zusammenführen X -> Y"; English "Merge X -> Y" is fine, functionally identical.
- `created_by` and `space` auto-inject from the bearer token — don't send them.

**Edit ingredient in-place** (qualifier-as-note pattern):
```bash
curl -sS --noproxy '*' -X PATCH \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"food\":{\"id\":$DST_FID,\"name\":\"$DST_NAME\"},\"note\":\"$QUALIFIER\"}" \
  "$BASE/api/ingredient/$ING_ID/"
```
`IngredientSerializer` is a writable nested serializer; `{id, name}` is enough to rebind to an existing food.

**Clear redundant note:**
```bash
curl -sS --noproxy '*' -X PATCH \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"note":""}' "$BASE/api/ingredient/$ING_ID/"
```

## Phase 7 — Orphan cleanup

After any in-place edits, the original cruft foods (e.g. `kleine Zucchini`, `etwas Butter`) have `numrecipe == 0`. Confirm with the user, then:
```bash
curl -sS --noproxy '*' -X DELETE -H "Authorization: Bearer $TOKEN" "$BASE/api/food/$FID/"   # 204
curl -sS --noproxy '*' -X DELETE -H "Authorization: Bearer $TOKEN" "$BASE/api/unit/$UID/"   # 204
```

## Why automations can't do everything

The five automation types that mutate ingredient data during scrape:

- `FOOD_ALIAS` / `UNIT_ALIAS` / `KEYWORD_ALIAS` — case-insensitive iexact rename.
- `NEVER_UNIT` — token reshuffle when a known food appears at the unit position; can also inject a unit.
- `TRANSPOSE_WORDS` — swap two adjacent words.
- `*_REPLACE` family — regex sub gated on source URL.

None can **split** a food name into food + note. The `note` field is populated by the parser only when the raw ingredient string contains parens or a comma (e.g. `Spitzpaprika, rote` → food `Spitzpaprika`, note `rote`). For input like `kleine Zucchini` or `etwas Butter` no automation can produce note `klein` / `etwas` — only post-import editing does. That's why the cleanup table treats them differently from the FOOD_ALIAS candidates.

## API quick reference

| Endpoint | Method | Notes |
|---|---|---|
| `/api/recipe-from-source/` | POST | `{url}` / `{data}` / `{bookmarklet}`. Body-level `error`/`duplicates` even on HTTP 200. |
| `/api/recipe/` | POST | Nested write. `order` must be int per ingredient and per step. |
| `/api/recipe/{id}/image/` | PUT | **multipart**: `image_url=<url>` or `image=<file>`. JSON returns 415. |
| `/api/recipe/{id}/` | GET | Full nested recipe. |
| `/api/recipe/?foods={id}` | GET | Recipes that reference a food. Same param for `?units=`, `?keywords=`. |
| `/api/step/{pk}/` | PATCH | `{ingredients:[…]}` sets that step's M2M; doesn't unlink from other steps. |
| `/api/ingredient/{id}/` | PATCH | Per-ingredient writable nested. |
| `/api/food/{id}/?extended=1` | GET | Adds `numrecipe`. Same for `unit`/`keyword`. |
| `/api/food/{src}/merge/{dst}/` | PUT | Body `{}`. Same shape for `unit`, `keyword`, `supermarket-category`. |
| `/api/automation/` | POST | Snake_case `param_1/param_2/param_3`. |
| `/api/property-type/` | GET | Nutritional dimensions defined in the space. |
| `/api/food/{id}/` `/api/unit/{id}/` | DELETE | 204. |

## Don't

- Don't auto-create aliases for cases where the qualifier matters (`kleine X`, `etwas Y`, `gehackte Z`). Always ask first.
- Don't merge into a target without showing the user both sides' `numrecipe` and nutrition coverage — sometimes the "clean" target has zero properties and the import-artifact is the better-loaded one.
- Don't run any cleanup from a duplicates response in Phase 1 — duplicates mean stop and ask.
- In **interactive** mode, don't update `docs/mdimport/backlog.md` — that's a review step the user owns. In **autonomous** mode, advancing the backlog and writing the per-recipe cleanup artifact are *required* steps (the run is the review).
- In **autonomous** mode, never merge/alias/edit/delete a food or unit. Mutation of foods/units is reserved for the batch interactive cleanup session.
- Don't use Tandoor's AI-step-sort endpoint in Phase 4 — burns AI credits and overlaps with `tandoor-recipe-mapping`'s deterministic algorithm.

## Synonyms learned

From past runs, instruction-text synonyms that should be applied during Phase 4 (and merged into `.claude/skills/tandoor-recipe-mapping/SKILL.md` when convenient):

| Food name (as imported) | Also matches in instruction text |
|---|---|
| `Risottoreis` | "Reis" |
| `Spitzpaprika` | "Paprika" |
| `Gemüsebrühe` | "Brühe" |
| `kleine Zucchini` | "Zucchini" |
| `etwas Butter` | "Butter" |
| `Dattel getrocknet` | "Datteln" |
| `Korianderpulver` | "Koriander" |
| `Zimtpulver` | "Zimt" |
| `Kreuzkümmel` | "Kreuzkümmel" (the parser strips `pulver` here but not consistently elsewhere — flag inconsistencies) |

## Related research

- `docs/agents/research/2026-06-07-url-import-flow.md` — how scrape + persist + automation application work end-to-end.
- `docs/agents/research/2026-06-07-automation-creation-merge-ui.md` — how the "Automate" checkbox in the merge UI maps to `POST /api/automation/`; which models support it (Food/Unit/Keyword only).
- `docs/agents/research/2026-06-07-url-import-skill-notes.md` — the lessons-learned draft this skill was built from.
