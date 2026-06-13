---
name: tandoor-backlog-run
description: Autonomously import the Standard URL backlog into Tandoor — drives one isolated subagent per recipe through url-import autonomous mode (scrape, persist, image, map ingredients→steps, audit, write per-recipe cleanup artifact, advance backlog), records per-recipe problems, and stops short of any food/unit mutation so a later batch session can do all cleanup at once. Use when the user wants to bulk-import the remaining backlog items unattended.
---

# Tandoor backlog autonomous import

## When to use

The user wants the **Standard URL backlog** in `docs/mdimport/backlog.md` imported in bulk without sitting through each one. This skill is the **orchestrator**: it loops the backlog and runs each recipe in its own subagent via `tandoor-url-import` **autonomous mode**. It deliberately does **not** clean up foods/units or fill nutrition — those are batched into a later interactive session (see `tandoor-url-import` Phase 6 + `tandoor-nutrition-backfill`).

Do **not** use this for a single recipe the user wants to walk through interactively — that's plain `tandoor-url-import` interactive mode.

## Prerequisites

1. Credentials at project root: `tandoor_token.txt`, `tandoor_url.txt`.
2. **Seed unit aliases created first** (see `docs/agents/handoffs/2026-06-13-seed-unit-aliases.md`). Doing this before the loop means common cruft (`Gramm`, `Liter`, …) is normalized at scrape time, so the run produces far less Stage-4 noise. The seeding is a separate human-run pre-step, **not** part of this loop.
3. Confirm `docs/mdimport/backlog.md` has the staged **Doing** section (stages 1–5) and a `docs/mdimport/cleanup/` directory exists (the example `recipe-22-wot.md` defines the artifact shape).

## Architecture (and how it manages context)

```
orchestrator (this skill, main loop)
  └─ for each backlog URL, sequentially:
       └─ import subagent  ── runs tandoor-url-import AUTONOMOUS mode (all HTTP)
            └─ mapping sub-subagent (optional) ── pure recipe-domain, zero HTTP
```

- **Per-recipe context isolation is the whole technique.** Each recipe runs in a fresh import subagent whose context holds the bulky scrape JSON, step text, and audit GETs — then dies. The orchestrator keeps only a compact ledger row per recipe. This is what lets the run cover 30+ recipes without overflowing; a flat single-context "goal run" would not.
- **Optional mapping sub-subagent** (see `tandoor-recipe-mapping` → "Mapping as a delegable step"): keeps recipe-domain reasoning free of HTTP noise. Use it for large/sub-recipe imports; inline mapping is fine otherwise. Same pk→order contract either way.
- The orchestrator's ledger row: `{url, recipe_id, stage_reached, new_foods, new_units, problems[], artifact_path}`.

## The loop

For each URL in **Standard URL backlog**, top to bottom:

1. **Skip if already processed** (resumability — see below).
2. Spawn an **import subagent** with the contract below.
3. Append its returned ledger row to the run summary. Do **not** keep its raw output.
4. Continue to the next URL. Never stop the whole run for a single recipe's failure — that becomes a problem note.

After the loop, print the run summary table and point the user at the batch cleanup session.

### Import subagent contract

**Input:** one URL + "run `tandoor-url-import` in **autonomous mode**".

**The subagent must:**
- Run url-import Phases 1–3 + 5 (scrape → persist → image → audit). Map ingredients→steps (stage 3), keying on **pk** (handles sub-recipes).
- **Never** merge/alias/edit/delete any food or unit. Audit only records what's new.
- Write the per-recipe cleanup artifact `docs/mdimport/cleanup/recipe-<RID>-<slug>.md` (new foods/units + suggested action + target id + nutrition coverage; same shape as `recipe-22-wot.md`), including a `## Problems` section.
- Advance `docs/mdimport/backlog.md`: move the URL into **Doing** under the furthest stage reached.

**Output (return to orchestrator — keep it small):** the ledger row only —
`recipe_id`, `stage_reached` (1–3), counts of new foods/units, `problems[]` (specific strings), `artifact_path`. No JSON dumps, no step text.

## Failure routing (per recipe — record, don't halt)

| Situation | Action |
|---|---|
| Soft scrape error (`error:true`/`msg`/empty) | Move URL from *Standard URL backlog* → *Probably needs AI import* with reason. No persist. |
| `duplicates` non-empty | Do not persist (never double-create). Record duplicate id as a problem; leave URL in place. |
| Image both paths fail (incl. host 403) | Record `image: pending (<reason>)`; recipe rests at Stage 1. |
| Ingredients left unmapped | Leave on dump step; list them in the problem note. |
| Any other unexpected error | Record a specific problem string; advance as far as it got. |

All problems are written into the recipe's cleanup-file `## Problems` section **and** surfaced in the run summary, so the joint pass has the full list.

## Resumability

The **backlog file is the checkpoint**. Before processing a URL, skip it if it is already in *Doing*, *Done*, or *Probably needs AI import*. The backlog is updated after each recipe, so a run interrupted by context compaction or a crash resumes correctly by re-reading it — no separate state file.

## Concurrency

Run recipes **sequentially** (or very low concurrency only if proven safe). Imports create/look-up shared `Food`/`Unit` rows; parallel imports can race into duplicate foods, defeating the point of the later cleanup.

## Stop conditions

- Standard URL backlog exhausted, **or**
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
