# Handoff — Verify UNIT_ALIAS semantics & seed normalization aliases

**Date:** 2026-06-13
**Scope for next session:** Confirm how Tandoor `UNIT_ALIAS` automations match/replace, then create the agreed seed aliases. This is a deliberate **pre-step** done *before* the autonomous backlog import loop — it is NOT part of the automation. Do it interactively with the user.

## Goal

Create UNIT_ALIAS automations so future URL imports auto-normalize spelled-out / variant unit names to the space's canonical short forms, reducing later cleanup noise. Two tiers were agreed (see below). Both tables are approved by the user; the only open question is the exact param semantics.

## Open question to resolve FIRST (blocking)

Does `UNIT_ALIAS` match on **names** or **ids**?

- Working assumption: `param_1` = source **name** string (iexact match against the scraped unit name), `param_2` = target **name** string to replace it with. This must be name-based, because at scrape time the parser emits a name string (e.g. `"Gramm"`), not an id.
- Why it's uncertain: the `tandoor-url-import` skill's alias example reused `$SRC`/`$DST` that were **ids** in a *merge* context, which is ambiguous for aliases.

**How to verify (do this before creating all of them):**
1. Create ONE alias, e.g. `Gramm` → `g`, via:
   ```bash
   TOKEN=$(cat tandoor_token.txt); BASE=$(cat tandoor_url.txt)
   curl -sS --noproxy '*' -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"type":"UNIT_ALIAS","name":"Normalize Gramm -> g","param_1":"Gramm","param_2":"g"}' \
     "$BASE/api/automation/"
   ```
2. Confirm it persisted: `GET $BASE/api/automation/` and inspect the stored `param_1`/`param_2`.
3. Prove it actually fires at scrape time: re-scrape a recipe known to emit `Gramm` (or POST a tiny `{data}` payload to `/api/recipe-from-source/` containing a `"100 Gramm ..."` ingredient line) and check the resulting unit name is `g`, not `Gramm`. Inspect the automation source if needed — likely in `cookbook/helper/` (automation application during scrape/parse).
4. If matching turns out to be **id-based** instead, adjust: look up source/target unit ids and use those. Record the corrected recipe in the skill before batch-creating the rest.

Do not mass-create the Tier-2 aliases until step 3 confirms one actually fires.

## Approved alias lists

All target short forms already exist in the space (ids confirmed 2026-06-13). These are **pure name normalizations** — no amount conversion, zero numeric risk.

### Tier 1 — already duplicated in the space
| source → target | target id |
|---|---|
| `Gramm` → `g` | 6 |
| `Liter` → `l` | 10 |

### Tier 2 — pre-emptive (common in German scrapes)
| source → target | target id |
|---|---|
| `Kilogramm` → `kg` | 8 |
| `Milliliter` → `ml` | 12 |
| `Esslöffel` → `EL` | 3 |
| `Teelöffel` → `TL` | 19 |
| `Messerspitze` → `Msp.` | 13 |
| `Zehen` → `Zehe` | 20 |

Excluded by design: `mittelgroße` (adjective, not a unit — handled as drop-to-note in batch cleanup); `Cup`/`Tasse` (ambiguous measures); all food aliases (need judgment → batch session).

## Important: aliases do NOT retroactively fix existing data

Aliases act only at **scrape time**. The existing duplicate unit rows `Gramm` (id 25) and `Liter` (id 27) — and recipe 22's use of them — are **not** fixed by creating the aliases. They get **merged** (`25→6`, `27→10`) during the later batch cleanup session, alongside every other recipe's Stage-4 items. Creating the aliases now only stops the next ~30 imports from recreating `Gramm`/`Liter`.

## Context / related artifacts (don't duplicate — read these)

- `docs/mdimport/backlog.md` — Kanban backlog. **Doing** is now staged (1 Imported → 5 Properties filled). Recipe 22 sits in Stage 3.
- `docs/mdimport/cleanup/recipe-22-wot.md` — worked example of the per-recipe cleanup artifact the autonomous loop will produce (new foods/units + suggested actions + target ids).
- `.claude/skills/tandoor-url-import/SKILL.md` — Phase 6 wire calls for `POST /api/automation/` (snake_case `param_1/param_2`, `created_by`/`space` auto-injected — don't send them).
- Credentials (gitignored, project root): `tandoor_token.txt`, `tandoor_url.txt`.

## Environment gotchas

- Use `curl --noproxy '*'` for the **Tandoor instance** (private IP; only reachable bypassing the proxy).
- Use the proxy (NO `--noproxy`) for any **public host** (verified 2026-06-13: all external hosts require the proxy in this sandbox; direct connect fails).
- Prefer `curl` over Python `urllib` (urllib is blocked against private IPs here).

## Suggested skills for the next session

- `tandoor-ai-providers` — closest existing reference for the `POST /api/automation/` shape and auth pattern (not aliases specifically, but same API conventions).
- `tandoor-url-import` — Phase 6 documents the automation create call; read that section.
- `confidence-honesty` — the whole point of this session is verifying an assumption; state confidence explicitly before mass-creating.

## Definition of done

1. Verified (with evidence from a real scrape) whether UNIT_ALIAS matches on name or id; corrected the documented recipe if needed.
2. All 8 aliases (Tier 1 + Tier 2) created and confirmed present via `GET /api/automation/`.
3. One-line note added back to `tandoor-url-import` SKILL.md confirming the verified param semantics, so the autonomous loop and batch session rely on a known-good recipe.
