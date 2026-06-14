---
name: tandoor-backlog-run
description: Autonomously import a backlog section into Tandoor — drives one isolated subagent per recipe through autonomous-mode import (Standard URL backlog via url-import; Instagram reels via instagram-import AI extraction), each subagent scraping, persisting, imaging, mapping ingredients→steps, auditing, writing a per-recipe cleanup artifact and advancing the backlog. Records per-recipe problems and stops short of any food/unit mutation so a later batch session can do all cleanup at once. Use when the user wants to bulk-import the remaining backlog items (URL or Instagram) unattended.
---

# Tandoor backlog autonomous import

## When to use

The user wants a **backlog section** in `docs/mdimport/backlog.md` imported in bulk without sitting through each one. This skill is the **orchestrator**: it loops a chosen section and runs each recipe in its own subagent in **autonomous mode**. It deliberately does **not** clean up foods/units or fill nutrition — those are batched into a later interactive session (see `tandoor-url-import` Phase 6 + `tandoor-nutrition-backfill`).

Do **not** use this for a single recipe the user wants to walk through interactively — that's plain `tandoor-url-import` (or `tandoor-instagram-import`) interactive mode.

## Source modes

Pick one per run (ask the user if ambiguous). The mode selects **which backlog section** is looped and **which front-end skill** each subagent runs; everything downstream (persist → image → map → audit → cleanup artifact → backlog advance) is identical.

| Mode | Backlog section | Subagent front-end | Extra setup |
|---|---|---|---|
| **url** (default) | `## Standard URL backlog` | `tandoor-url-import` autonomous (Phases 1–5) | seed unit aliases (below) |
| **instagram** | `## Instagram / Facebook / freeform` → `### Instagram` | `tandoor-instagram-import` autonomous (its P0–1 scrape+AI, then delegates url-import P2–5) | agent-browser + Chromium; pick an AI provider **once** at run start |

**Routing of results is the same for both modes:** a successfully-imported recipe (URL or reel) lands in the shared **Doing → Stage 3** pipeline with a cleanup artifact, so the later batch session has one unified queue. Only *unimportable* items stay annotated in place in their source section (see Failure routing).

## Prerequisites

1. Credentials at project root: `tandoor_token.txt`, `tandoor_url.txt`.
2. **(url mode) Seed unit aliases created first.** Doing this before the loop means common cruft (`Gramm`, `Liter`, …) is normalized at scrape time, so the run produces far less Stage-4 noise. The seeding is a separate human-run pre-step, **not** part of this loop.
3. **(instagram mode) agent-browser + a Chromium binary** (sandbox: `/usr/bin/chromium`; install once with `npm i -g agent-browser`), the space has **AI enabled** with at least one **working** provider, and you have **chosen the provider id once** for the whole run (recommend **OR Gemini Flash 3.5**; avoid Claude providers — markdown-fence bug). See `tandoor-instagram-import` for the provider gotcha table.
4. Confirm `docs/mdimport/backlog.md` has the staged **Doing** section (stages 1–5) and a `docs/mdimport/cleanup/` directory exists (the example artifact shape is documented in `tandoor-url-import` autonomous mode).

## Architecture (and how it manages context)

```
orchestrator (this skill, main loop)
  └─ for each backlog item in the chosen section, sequentially:
       └─ import subagent  ── runs the mode's front-end AUTONOMOUS (all HTTP)
            │                   url:       tandoor-url-import P1–5
            │                   instagram: tandoor-instagram-import P0–1 → url-import P2–5
            └─ mapping sub-subagent (optional) ── pure recipe-domain, zero HTTP
```

- **Per-recipe context isolation is the whole technique.** Each recipe runs in a fresh import subagent whose context holds the bulky scrape JSON, step text, and audit GETs — then dies. The orchestrator keeps only a compact ledger row per recipe. This is what lets the run cover 30+ recipes without overflowing; a flat single-context "goal run" would not.
- **Optional mapping sub-subagent** (see `tandoor-recipe-mapping` → "Mapping as a delegable step"): keeps recipe-domain reasoning free of HTTP noise. Use it for large/sub-recipe imports; inline mapping is fine otherwise. Same pk→order contract either way.
- The orchestrator's ledger row: `{url, recipe_id, stage_reached, new_foods, new_units, problems[], artifact_path}`.

## The loop

For each item in the **chosen section** (url mode → *Standard URL backlog*; instagram mode → *### Instagram*), top to bottom:

1. **Skip if already processed** (resumability — see below).
2. Spawn an **import subagent** with the contract below (passing the AI provider id in instagram mode).
3. Append its returned ledger row to the run summary. Do **not** keep its raw output.
4. Continue to the next item. Never stop the whole run for a single recipe's failure — that becomes a problem note.

After the loop, print the run summary table and point the user at the batch cleanup session.

### Import subagent contract

**Input:** one backlog item (URL or reel URL) + the mode + "run the mode's front-end in **autonomous mode**" (+ `ai_provider_id` in instagram mode).

**The subagent must:**
- **url mode:** run `tandoor-url-import` Phases 1–3 + 5 (scrape → persist → image → audit).
- **instagram mode:** run `tandoor-instagram-import` Phase 0–1 (agent-browser caption scrape → `/api/ai-import/` with the given provider; set `recipe["source_url"]` to the reel URL before persist), then continue through url-import Phases 2–3 + 5. Use a `.jpg`/`.png` temp file for the image, never `.img`.
- Map ingredients→steps (stage 3), keying on **pk** (handles sub-recipes). *(Instagram captions are often method-poor — if there are no real instruction steps, that's expected; record it as a problem, don't fail.)*
- **Never** merge/alias/edit/delete any food or unit. Audit only records what's new.
- Write the per-recipe cleanup artifact `docs/mdimport/cleanup/recipe-<RID>-<slug>.md` (new foods/units + suggested action + target id + nutrition coverage; shape per `tandoor-url-import` autonomous mode), including a `## Problems` section.
- Advance `docs/mdimport/backlog.md`: move the item into the shared **Doing** section under the furthest stage reached (both modes route into the same pipeline).

**Output (return to orchestrator — keep it small):** the ledger row only —
`recipe_id`, `stage_reached` (1–3), counts of new foods/units, `problems[]` (specific strings), `artifact_path`. No JSON dumps, no step text.

## Failure routing (per recipe — record, don't halt)

| Situation | Action |
|---|---|
| Soft scrape error (`error:true`/`msg`/empty) — **url** | Move URL from *Standard URL backlog* → *Probably needs AI import* with reason. No persist. |
| `duplicates` non-empty | Do not persist (never double-create). Record duplicate id as a problem; leave item in place. |
| Image both paths fail (incl. host 403) | Record `image: pending (<reason>)`; recipe rests at Stage 1. |
| Ingredients left unmapped | Leave on dump step; list them in the problem note. |
| **(instagram)** Caption didn't render (empty `description`) | Login wall / private / age-gated / deleted / rate-limited. Don't call the AI. Leave the URL in `### Instagram` with an inline reason. No persist. |
| **(instagram)** AI fence error (`error:true` + ```` ```json ````) | Provider returned fenced JSON (a Claude provider). Record it; with a non-Claude provider chosen for the run this shouldn't recur. No persist for this item. |
| **(instagram)** Caption has ingredients but no method | Persist anyway; record problem `no cooking method in caption — steps must be added manually`. |
| **(instagram)** Facebook URL | Out of scope (login/members wall). Skip; leave annotated in place. |
| Any other unexpected error | Record a specific problem string; advance as far as it got. |

All problems are written into the recipe's cleanup-file `## Problems` section **and** surfaced in the run summary, so the joint pass has the full list.

## Resumability

The **backlog file is the checkpoint**. Before processing an item, skip it if it is already in *Doing*, *Done*, or *Probably needs AI import* (url) / annotated as unimportable in `### Instagram` (instagram). The backlog is updated after each recipe, so a run interrupted by context compaction or a crash resumes correctly by re-reading it — no separate state file.

## Concurrency

Run recipes **sequentially** (or very low concurrency only if proven safe). Imports create/look-up shared `Food`/`Unit` rows; parallel imports can race into duplicate foods, defeating the point of the later cleanup. **In instagram mode this is doubly important:** each reel spins up its own headless Chromium session, and Instagram's anonymous endpoint throttles bursts — parallel reel scrapes multiply the browser footprint and trip rate-limits/login-walls. Keep it sequential.

## Stop conditions

- the chosen backlog section exhausted, **or**
- the user gave a count/limit, **or**
- repeated infrastructure failure (e.g. instance returning 5xx for several consecutive recipes) — stop and report rather than burn through the list.

## Does NOT do (by design)

- No food/unit merges, aliases, in-place edits, or deletes (reserved for the batch interactive session via `tandoor-url-import` Phase 6).
- No nutrition backfill (reserved for `tandoor-nutrition-backfill`).
- No seeding of aliases (separate human pre-step).

## Handoff to the batch cleanup session

When the loop finishes, every imported recipe sits in **Doing/Stage 3** with a cleanup artifact under `docs/mdimport/cleanup/`. Tell the user the next step is a single interactive session that:
1. Reads all cleanup artifacts, **globally dedups** cruft across recipes (e.g. one `gehackte Tomaten` decision applied everywhere), runs the merges/aliases/edits (Phase 6/7), advancing recipes to Stage 4.
2. Runs `tandoor-nutrition-backfill` for the remaining new foods, advancing to Stage 5 → Done.
