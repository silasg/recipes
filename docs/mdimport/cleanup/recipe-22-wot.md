# Import cleanup — Recipe 22 "Wot Rezept aus Äthiopien"

- **Recipe ID:** 22
- **Source:** https://www.fernweh-koch.de/aethiopisches-wot/
- **Imported:** 2026-06-13
- **Pipeline run:** url-import Phases 1–5 + recipe-mapping (nutrition backfill NOT yet done)

All entities below were **created automatically during this import** — they had `numrecipe == 1` with recipe 22 as the sole referrer at audit time. "Suggested action" follows the url-import skill's Phase 6 / Phase 7 classification. Nothing here has been executed yet.

## New foods (5)

| # | id | name | nutrition | classification | suggested action | target |
|---|----|------|-----------|----------------|------------------|--------|
| 1 | 499 | Berbere Gewürz | 0/4 | genuinely new spice, no clean target | **keep** — backfill nutrition via `tandoor-nutrition-backfill`. Optionally rename → `Berbere` | — |
| 2 | 500 | etwas Salz | 0/4 | qualifier cruft (clean target exists) | **edit ingredient in-place** → rebind to `Salz`, move "etwas" to note (or clear); then **delete orphan** 500. **No alias** (qualifier would be silently dropped on future scrapes) | `Salz` (384) |
| 3 | 501 | gehackte Tomaten | 0/4 | form variant of a tinned-tomato product | **merge + FOOD_ALIAS** — but name differs from target; **confirm with user first** | `Stückige Tomaten (Konserve)` (425) — or `Flammengeröstete Dosentomaten` (115) / `passierte Tomaten` (324) |
| 4 | 502 | grüne Chilischoten | 0/4 | plural form, clean singular exists | **merge + FOOD_ALIAS** `grüne Chilischoten -> grüne Chilischote` | `grüne Chilischote` (177) |
| 5 | 503 | etwas Öl | 0/4 | qualifier cruft (clean target exists) | **edit ingredient in-place** → rebind to `Öl`, move "etwas" to note (or clear); then **delete orphan** 503. **No alias** | `Öl` (492) — recipe text says generic "heißes Öl"; do not bind to `Olivenöl` (307) |

## New units (3)

| # | id | name | classification | suggested action | target |
|---|----|------|----------------|------------------|--------|
| 6 | 25 | Gramm | spelled-out duplicate of canonical | **merge + UNIT_ALIAS** `Gramm -> g` | `g` (6) |
| 7 | 27 | Liter | spelled-out duplicate of canonical | **merge + UNIT_ALIAS** `Liter -> l` | `l` (10) |
| 8 | 26 | mittelgroße | adjective-as-unit cruft (from "3 mittelgroße Zwiebeln"), no real unit | **edit ingredient** → drop unit (set null), move "mittelgroß" to note; then **delete orphan** 26. **No alias** | — (none) |

## Notes

- Foods reused cleanly from the space (not new, full 4/4 nutrition): `Ingwer (201), Kartoffel (212), Knoblauch (222), Möhre (293), Olivenöl (307), Paprika (314), rote Linsen (367), rote Zwiebel (370), Tomate (437), Tomatenmark (440), Wasser (459), Zwiebel (486)`.
- Units reused cleanly: `Dose (2), EL (3), Stück (17)`.
- Items 2, 5, 8 (`etwas Salz`, `etwas Öl`, `mittelgroße`) are **edit-in-place, no-alias** by design: an alias would silently drop the "etwas"/"mittelgroß" qualifier on every future scrape from this and other sources.
- Item 3 (`gehackte Tomaten`) is the only judgment call — target name does not match source, so it needs explicit user confirmation before merging.
- After any in-place edits (items 2, 5, 8), the orphaned source foods/units drop to `numrecipe == 0` and are safe to DELETE (Phase 7).
