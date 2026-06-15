# Tag harmonization — applied cleanup + future-import automation plan

**Date:** 2026-06-15
**Branch:** `fork/default-unit`
**Scope:** DB-wide keyword (tag) cleanup over all 111 recipes. This file records exactly what was changed and which parts can be made self-healing on future imports via `KEYWORD_ALIAS` automations.

## Target taxonomy (controlled vocabulary)

Casing rule: **German nouns capitalized**, **adjectives lowercase**, publishers as branded.

- **Course/dish-type** (noun): `Hauptgericht`, `Suppe`, `Eintopf`, `Salat`, `Frühstück`, `Dessert`, `Kuchen & Gebäck`, `Beilage`, `Aufstrich & Dip`, `Snack`
- **Diet** (adj): `vegan`, `vegetarisch`
- **Attribute** (adj): `proteinreich`
- **Cuisine** (adj): `italienisch`, `indisch`, `koreanisch`, `japanisch`, `thailändisch`, `äthiopisch`, `mexikanisch`, `orientalisch`, `mediterran`, `deutsch`, `asiatisch` (fallback)
- **Publisher** (branded, sparse — only when a real publisher exists): `ZEIT Magazin`, `EAT SMARTER`, `Chefkoch`, `essen & trinken`, `Cookidoo`
- **Provenance:** `✨ AI` (marks AI-imported recipes)

Result: keyword table went **58 → 30**.

## Cleanup applied (Chunk 1, executed 2026-06-15)

### Merges (`PUT /api/keyword/<src>/merge/<dst>/`)
| src (deleted) | → dst |
|--|--|
| hauptgerichte(21), hauptspeise(22) | Hauptgericht(20) |
| easy vegan(8), vegan food(47), vegan recipe(48), plant-based(37), vegan_high_protein(57) | vegan(46) |
| eatsmarter.de(10) | EAT SMARTER(9) |
| vorwerk international & co. kmg(50) | Cookidoo(5) |

### Renames (`PATCH /api/keyword/<id>/`)
hauptgericht→`Hauptgericht`, frühstück→`Frühstück`, eintopf→`Eintopf`, suppen und suppeneinlagen→`Suppe`, eat smarter→`EAT SMARTER`, cookidoo.de→`Cookidoo`, www.chefkoch.de→`Chefkoch`, www.essen-und-trinken.de→`essen & trinken`

### Deletes (`DELETE /api/keyword/<id>/`, 34 total)
- **Orphans (0 recipes):** asien, eierlikör, einepriselecker.de, gekocht, hülsenfrüchte, indien, marsha, matelli, nico richter, Import, Import 1, www.paleo360.de
- **Outside chosen axes (occasion/attribute/generic):** dinner, easy recipe, weeknight meal, beans, einfach, gelinggarantie, gesund, gemüse, one pot, herbst, sommer, silvester, weihnachten
- **Fragments / single dish-names / ingredient / process junk:** arabisch, asiatische, schnelle asiatische gerichte, tabouleh, shakshuka, käsefondue, pasta, tofu, kichererbsen, kshn-haic

### New vocab keywords created (`POST /api/keyword/`)
Salat(59), Dessert(60), Kuchen & Gebäck(61), Beilage(62), Aufstrich & Dip(63), Snack(64), italienisch(65), indisch(66), koreanisch(67), japanisch(68), thailändisch(69), äthiopisch(70), mexikanisch(71), mediterran(72), deutsch(73), asiatisch(74)

## Future-import automation plan (`KEYWORD_ALIAS`)

`POST /api/automation/` with `{"type":"KEYWORD_ALIAS","param_1":"<incoming name>","param_2":"<canonical name>"}` (name-keyed). At import time an incoming keyword named `param_1` is rewritten to `param_2` before attach.

**What it CAN automate:** canonicalizing *recurring known* names — the publisher domains and the course/diet synonyms we merged. This stops the same junk-named tags from re-entering under a wrong name.

**What it CANNOT do (hard limits):**
1. **Rename only, never delete.** No automation drops a keyword; pure junk (`Import 1`, random English tags, novel source domains) can only be redirected, not suppressed. Novel junk still needs manual deletion.
2. **Only catches predicted names.** A brand-new source domain or AI-invented tag is not covered.
3. **Cannot assign taxonomy.** It will never decide a new recipe is `koreanisch`+`Hauptgericht`. Course/diet/cuisine classification stays manual.

### Proposed alias rule set (~16 rules)

Publisher canonicalization:
| param_1 (incoming) | param_2 (canonical) |
|--|--|
| eatsmarter.de | EAT SMARTER |
| eat smarter | EAT SMARTER |
| www.chefkoch.de | Chefkoch |
| chefkoch.de | Chefkoch |
| www.essen-und-trinken.de | essen & trinken |
| essen-und-trinken.de | essen & trinken |
| cookidoo.de | Cookidoo |
| vorwerk international & co. kmg | Cookidoo |
| zeit.de | ZEIT Magazin |

Course/diet synonym canonicalization:
| param_1 | param_2 |
|--|--|
| hauptgerichte | Hauptgericht |
| hauptspeise | Hauptgericht |
| hauptgericht | Hauptgericht |
| vegan recipe | vegan |
| vegan food | vegan |
| easy vegan | vegan |
| plant-based | vegan |

> Note: aliases are case-sensitive on `param_1` matching in Tandoor; add lowercase + as-seen variants for the domain tags that arrive verbatim from the scraper. These are *not yet created* — pending user go-ahead.

## Phase 2: per-recipe tag assignment — DONE (2026-06-15)

All 111 recipes tagged with course + cuisine (where identifiable) + diet + `proteinreich`, approved in 4 chunks. Provenance tags (`✨ AI`, publisher, `ZEIT Magazin`) preserved. PATCH 200/111. Rules applied:
- One course/dish-type tag per recipe (most specific form).
- Cuisine only where a clear cultural identity exists (blank = international).
- Diet from ingredient inspection (plant-analogue aware: Erdnussbutter/Kokosmilch/Sojahack ≠ animal). #111 (Hähnchen) and #75 (Fischsoße) get no diet tag.
- `proteinreich` on the high-protein recipes (5,15,51,53,55,61,63,74).

**Task 1 (tag harmonization) is fully complete.**

## Task 2: preparation-duration review — DONE (2026-06-15)

Full review of all 111 recipes. `working_time` = active hands-on; `waiting_time` = passive (bake/chill/soak/simmer unattended), derived from step text.
- **41 `wt=0` recipes** given derived wt/wa (approved chunk 1). Two passive times inferred where steps were truncated: #57 no-bake cheesecake (120 min chill), #73 mousse (360 min overnight) — revisit if real numbers surface.
- **70 non-zero recipes** reviewed; 57 left as-is, **13 corrected** (approved chunk 2) — systematic fix: long bake/simmer/chill had been logged as active `working_time` with `wa=0` (e.g. #26, #28, #31, #103), plus one data error (#60 wt=180→20) and one over-estimate (#95 Radieschenbutter 30→10).

**Verification:** 0 recipes remain at `wt=0`; all 111 reviewed. **Task 2 complete.**

## Status: both tasks complete + automations live
- **`KEYWORD_ALIAS` automations created 2026-06-15** — all 16 rules above (ids 230–245). Future imports auto-canonicalize the publisher domains and course/diet synonyms.
- Remaining optional: revisit inferred chill times #57 / #73 if real source numbers surface.
