---
name: tandoor-nutrition-backfill
description: Backfill missing nutrition (Property) and food-scoped UnitConversion rows on a Tandoor recipe so that per-serving nutrition aggregation works end-to-end. Walks the audit, the FDC-or-manual property writes, the unit-conversion creation, and the verification GET. Use after `tandoor-url-import`'s Phase 5 report identifies foods with zero properties or ingredient units with no conversion path to the food's properties_food_unit.
---

# Tandoor nutrition backfill (Property + UnitConversion)

## When to use

A recipe in Tandoor has per-serving nutrition gaps — either (a) a food has no `Property` rows so its `food_properties[*].food_values[<food_id>].value` comes back as `0`/`None`, or (b) the recipe references an ingredient unit that doesn't resolve to the food's `properties_food_unit`, so the aggregator emits `missing_conversion`. This skill closes both gaps for one recipe at a time and confirms by re-reading the recipe.

It is the natural follow-on to `tandoor-url-import` once that skill's Phase 5 verification report flags `0/4` foods or unit gaps. Also runnable standalone against any recipe id.

## Credentials

From project root (gitignored):
- `tandoor_token.txt` — Bearer token
- `tandoor_url.txt` — Base URL

Network: in this sandbox Python's `urllib` is blocked against private IPs while `curl --noproxy '*'` is allowed. Default to `curl` for HTTP and `python3` only for JSON munging. `pip install` is PEP-668-blocked.

## Mental model — read this once before touching anything

Tandoor aggregates per-recipe nutrition in `FoodPropertyHelper.calculate_recipe_properties` (`cookbook/helper/property_helper.py`). For every ingredient, for every PropertyType in the space, it:
1. Calls `UnitConversionHelper.get_conversions(ingredient)` — BFS over `UnitConversion` rows (`food=NULL` is space-wide, `food=X` is food-scoped; both directions of the edge are explored).
2. For each conversion `c` where `c.unit == ingredient.food.properties_food_unit`, contributes
   `(c.amount / food.properties_food_amount) * Property.property_amount`.

That means a backfill is successful only when **both** of these hold:
- The food has a `Property` row for every PropertyType the user cares about.
- For the ingredient's unit, the conversion graph reaches the food's `properties_food_unit` (typically `g`).

Other shape notes worth keeping in mind:
- `PropertyType.fdc_id` is the USDA FoodData Central **nutrient id** (e.g. Proteine=1003, Fett=1004, Kohlenhydrate=1005, Kalorien=1008). `POST /api/food/{id}/fdc/` walks the FDC response and creates a `Property` row whenever `foodNutrients[*].nutrient.id == PropertyType.fdc_id`. A miss → `property_amount=0` (not skipped).
- `Property` has no FK to `Food`. The link is the `FoodProperty` through-table. Posting to `/api/property/` creates an orphan; use nested write on `/api/food/{id}/` or the `/fdc/` action.
- `UnitConversionSerializer.create` is **name-keyed idempotent** (looks up by `(food.name, base_unit.name, converted_unit.name, space)`). Re-POSTing the same payload is safe.
- `space.default_unit` is what the aggregator substitutes when `ingredient.unit IS NULL`. If a recipe has unitless ingredients with piece semantics (`1 Zwiebel`), `space.default_unit` should be `Stück` (or similar) — otherwise the existing `Stück → g` UCs never fire.

Full model + endpoint map: `docs/agents/research/2026-06-07-nutrition-aggregation-and-unit-conversion.md`.

## Pipeline

| # | What | Mutating? |
|---|---|---|
| 0 | Audit — list every (food, unit) pair in the recipe, mark property gaps + conversion gaps | no |
| 1 | Backfill missing properties (FDC where possible, else manual after web research) | yes |
| 2 | Create food-scoped UCs for conversion gaps (each value web-validated) | yes |
| 3 | Verify recipe.food_properties — every value populated, no `missing_*` flags | no |

Stop after Phase 0 and surface the audit. Don't bundle Phase 1/2 — ask per food and per UC. The user has shown they want per-item control.

## Phase 0 — Audit

Fetch (reads only):

```bash
TOKEN=$(cat tandoor_token.txt) ; URL=$(cat tandoor_url.txt)
RID=<recipe id>

curl --noproxy '*' -s -H "Authorization: Bearer $TOKEN" "$URL/api/recipe/$RID/"          > /tmp/recipe.json
curl --noproxy '*' -s -H "Authorization: Bearer $TOKEN" "$URL/api/property-type/"        > /tmp/pts.json
curl --noproxy '*' -s -H "Authorization: Bearer $TOKEN" "$URL/api/space/"                > /tmp/space.json
```

Then, for each distinct food id used by the recipe:
```bash
curl --noproxy '*' -s -H "Authorization: Bearer $TOKEN" "$URL/api/food/$FID/"           > /tmp/food_$FID.json
curl --noproxy '*' -s -G --data-urlencode "food_id=$FID" -H "Authorization: Bearer $TOKEN" "$URL/api/unit-conversion/" > /tmp/uc_$FID.json
```

Present **two tables**:

### Table A — Property gaps

```
#  food (id)                  pfu      properties n/N   fdc_id   plan
1  Risottoreis (358)          (none)   0/4              none     needs backfill — search FDC
2  Spitzpaprika (421)         (none)   0/4              none     needs backfill — search FDC
3  Salz und Pfeffer (386)     (none)   0/4              none     skip (combined food — leave properties unset)
```

`pfu` = `properties_food_unit.name` (or `(none)` if unset). `n/N` = distinct PropertyType.id covered / total PropertyTypes in space. Dedupe by `property_type.id` — `len(food.properties)` can be higher (see §Don't on dedupe).

### Table B — UnitConversion gaps

For every (ingredient, food) row in the recipe where `ingredient.unit != food.properties_food_unit`:

```
#  step  food (id)       recipe unit   food pfu   existing UCs                    gap
1   1    Knoblauch (222) Zehe (20)     g          Zehe→5g                         covered
2   2    Safran (379)    Prise (15)    g          (none)                          1 Prise → ?g
3   2    Kreuzkümmel(242) Msp. (13)    g          TL→3g, EL→13g                   1 Msp.  → ?g
```

Mark "covered" iff the existing UC graph (transitive, both directions, plus any space-wide rows) connects `ingredient.unit` to `food.properties_food_unit`. Ignore ingredients with `amount=0` or `no_amount=true` — the aggregator excludes them anyway.

### Table C — Unitless ingredients vs space.default_unit

If `space.default_unit` is set, flag every ingredient with `unit: null`. The aggregator will retry these as `Ingredient(amount=i.amount, unit=space.default_unit, food=i.food)`:
- If the substituted unit is the food's `pfu` (e.g. `default_unit=g`, food pfu=g), the aggregator computes "i.amount grams" — wrong for piece-based foods.
- If the substituted unit has a UC to the food's `pfu` (e.g. `default_unit=Stück`, food has `Stück → 90 g`), it resolves correctly.

Surface this to the user. Changing `space.default_unit` affects every recipe; never change it from inside this skill — surface a recommendation and let the user own the call.

Stop here. Wait for per-item go-ahead.

## Phase 1 — Backfill missing properties

For each food in Table A flagged "needs backfill", do steps 1a–1d in order. Skip foods classified "skip".

### 1a — Decide source per food

Read the space's PropertyType `fdc_id` values once:

```bash
python3 -c "
import json
for pt in json.load(open('/tmp/pts.json')).get('results', []):
    print(f\"  pt {pt['id']:>2} {pt['name']:18s} fdc_id={pt['fdc_id']}\")
"
```

The `fdc_id` value determines which USDA dataType can populate which PropertyType:
- If Kalorien.fdc_id = **1008** (Energy, kcal, Atwater General Factors): comes from **SR Legacy** entries reliably. **Foundation** entries usually report Energy under nutrient_id `2047` or `2048` (Atwater Specific) instead — they will land as `0` after `/fdc/`.
- For Proteine (1003), Fett (1004), Kohlenhydrate (1005): both Foundation and SR Legacy report under those ids. Safe across datasets.

So: pick the FDC dataType per food deliberately. Walk the search results from Foundation first (newer, more rigorous), then SR Legacy, then Survey (FNDDS), then Branded.

### 1b — FDC search

```bash
for dt in "Foundation" "SR Legacy" "Survey (FNDDS)" ; do
  echo "=== $dt: $QUERY ==="
  curl --noproxy '*' -s -G --data-urlencode "query=$QUERY" --data-urlencode "dataType=$dt" \
    -H "Authorization: Bearer $TOKEN" "$URL/api/fdc-search/" \
  | python3 -c "
import json,sys
d=json.load(sys.stdin); foods=d.get('foods') or d.get('results') or []
print(f'  {len(foods)} hits')
[print(f\"  - fdc_id={f.get('fdc_id') or f.get('fdcId')}  name={(f.get('description') or '')[:90]}\") for f in foods[:8]]"
done
```

### 1c — Web-validate the FDC pick BEFORE writing

This is required, not optional. FDC's string search is naive: "rice" returns flours and rice dishes too; "saffron" works but "arborio" returns zero in SR Legacy. Always cross-check that the FDC entry's actual nutrition matches what's expected for the dish ingredient.

Two-source rule:
1. The FDC entry itself (description + macro values).
2. One independent source (Matvaretabellen.no, Bundeslebensmittelschlüssel, Foodiary, Nutritionix, or the food's national equivalent).

Helpful web queries:

| Food category | Search template |
|---|---|
| Rice variants | `<variant> rice nutrition per 100g protein fat carbs calories raw uncooked` |
| Vegetables (generic) | `<food> nutrition 100g protein carbs fat kalorien` |
| Spices | `<spice> spice nutrition per 100g USDA` |
| Branded / regional | add country (`<food> nutrition gramm Deutschland` for German products) |

If WebFetch of `fdc.nal.usda.gov/food-details/<id>/nutrients` returns 403 (it usually does from sandbox), call `https://api.nal.usda.gov/fdc/v1/food/<id>?api_key=DEMO_KEY` from your host instead — or rely entirely on independent sources and accept that you're matching FDC by name + macro band.

Present to the user: chosen fdc_id, USDA description, four macros (Proteine/Fett/Kohlenhydrate/Kalorien per 100g), one cross-reference URL. Get explicit "yes" before writing.

### 1d — Apply via `/fdc/`

```bash
curl --noproxy '*' -s -w "\nHTTP %{http_code}\n" -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"fdc_id\": $FDC_ID}" \
  "$URL/api/food/$FID/fdc/"
```

The action does several things atomically (`cookbook/views/api.py:1127`):
- Sets `food.fdc_id`, `food.properties_food_amount = 100`, `food.properties_food_unit = <Unit where base_unit='g'>` (auto-creates `g` if missing).
- Deletes existing `Property` rows whose `PropertyType.fdc_id IS NOT NULL`.
- For each PropertyType with `fdc_id`, writes a new `Property` (value from the FDC payload, or `0` if the nutrient_id wasn't returned).
- Bulk-creates the `FoodProperty` through-rows.

**Read the response carefully.** Inspect each `Property` in the response:
- If any expected-positive value comes back as `0.0`, that's a nutrient_id mismatch (most common: Energy on Foundation entries). Two recovery paths:
  - **Switch to SR Legacy.** Re-search SR Legacy for the same food, retry `/fdc/` with the new fdc_id (this re-writes all four Property rows, so you only need to confirm SR Legacy values are reasonable).
  - **Patch just the wrong one.** For Kalorien, compute via Atwater (`4*Proteine + 4*Kohlenhydrate + 9*Fett`, all per 100g) and PATCH:
    ```bash
    curl --noproxy '*' -s -w "\nHTTP %{http_code}\n" -X PATCH \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"property_amount": 31.37, "property_type": {"id": 4, "name": "Kalorien"}}' \
      "$URL/api/property/$PROPERTY_ID/"
    ```

- HTTP 429 = USDA API rate limit (per-key, on Tandoor's `FDC_API_KEY`). Either wait + retry, or fall through to 1e.

### 1e — Manual fallback (rate-limited or no FDC match)

Read the values from one authoritative source (USDA via web, Bundeslebensmittelschlüssel, Matvaretabellen.no), then PATCH the Food directly. This uses the same nested write semantics as `FoodSerializer` (`cookbook/serializer.py:880`):

```bash
# Find the canonical 'g' Unit id once
G_ID=$(curl --noproxy '*' -s -G --data-urlencode "query=g" -H "Authorization: Bearer $TOKEN" "$URL/api/unit/" \
  | python3 -c "import json,sys; print(next(u['id'] for u in (json.load(sys.stdin).get('results') or []) if u['name']=='g'))")

curl --noproxy '*' -s -w "\nHTTP %{http_code}\n" -X PATCH \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{
    \"fdc_id\": $FDC_ID,
    \"properties_food_amount\": 100,
    \"properties_food_unit\": {\"id\": $G_ID, \"name\": \"g\"},
    \"properties\": [
      {\"property_amount\": 11.43, \"property_type\": {\"id\": 1, \"name\": \"Proteine\"}},
      {\"property_amount\": 5.85,  \"property_type\": {\"id\": 2, \"name\": \"Fett\"}},
      {\"property_amount\": 65.37, \"property_type\": {\"id\": 3, \"name\": \"Kohlenhydrate\"}},
      {\"property_amount\": 310,   \"property_type\": {\"id\": 4, \"name\": \"Kalorien\"}}
    ]
  }" \
  "$URL/api/food/$FID/"
```

Set `fdc_id` even when writing manually, if the values came from a USDA entry — it's the breadcrumb that lets a future operator re-derive.

### 1f — Skip combined/conflated foods

Combined foods like "Salz und Pfeffer" — leave `properties_food_unit IS NULL` and `properties` empty. The aggregator emits `value: 0` for these (excluded from `missing_value` because of the `IS NULL` early-out at `property_helper.py:74-77`). Assigning fake combined values would mislead totals on every dish using one or the other. Surface this as a recommendation; if the user wants to split into separate Foods, that's out of scope here.

## Phase 2 — Create food-scoped UnitConversions

For each row in Table B with `gap != covered`.

### 2a — Web-research the conversion factor (mandatory)

Never invent these. Densities and per-piece weights differ by food and by source. Search:

| Pattern | Search template |
|---|---|
| Piece (Stück) → g | `weight of one <food> grams average` and `<food> wiegt gramm` |
| TL/teaspoon → g | `1 teaspoon <food> in grams` (USDA-cited sources preferred: ChefSolver, FreeFoodTips, Traditional Oven) |
| EL/tablespoon → g | `1 tablespoon <food> in grams` |
| Msp./Messerspitze → g | `Messerspitze <food> gramm` — German cooking unit; typical range 0.1–0.5 g, default ~0.4 g for fine ground spices |
| Prise → g | `pinch of <food> grams` — varies by spice (saffron pinch ≈ 0.05 g; salt pinch ≈ 0.3 g; sugar pinch ≈ 0.4 g) |
| Zehe → g | `weight of one clove garlic grams` (canonical ~4–5 g) |
| ml → g (any liquid) | `density of <food> g/ml` |

Cross-reference two sources for any value used. Surface the proposed conversion to the user with both URLs. Wait for "yes".

Common per-spice values that this session ground-truthed (use these as anchors; still confirm before posting):

| Conversion | Typical | Notes |
|---|---|---|
| 1 Zehe Knoblauch | 4–5 g | clove size varies; 5g is a safe middle |
| 1 Prise (saffron) | 0.05 g | classic 15–20 threads; Saveur |
| 1 Prise (salt/sugar) | 0.3–0.4 g | finger pinch |
| 1 Msp. ground spice | 0.3–0.5 g | ground cumin/coriander ≈ 0.4 g |
| 1 TL ground cinnamon | 2.6–3 g | USDA density 0.56 g/mL → 2.6g for level tsp |
| 1 TL salt | 5–6 g | fine table salt |
| 1 EL ground spice | 6–9 g | depends on density |
| 1 Stück red pointed pepper | 100–150 g | smaller than bell |
| 1 Stück bell pepper | 120–150 g | USDA medium ≈ 119 g |
| 1 Stück onion (medium) | 90–120 g | |
| 1 Stück zucchini (small) | 150–250 g | depends on "klein"/"mittel" qualifier |
| 1 ml white wine | ≈ 1 g | density ≈ 1.0 g/ml |

### 2b — POST the UC

```bash
curl --noproxy '*' -s -w "\nHTTP %{http_code}\n" -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{
    \"base_amount\": 1,
    \"base_unit\": {\"name\": \"Prise\"},
    \"converted_amount\": 0.05,
    \"converted_unit\": {\"name\": \"g\"},
    \"food\": {\"id\": 379, \"name\": \"Safran\"}
  }" \
  "$URL/api/unit-conversion/"
```

Notes:
- 201 on create, 200 on idempotent return (serializer matches on `(food.name iexact, base_unit.name iexact, converted_unit.name iexact, space)`).
- `food: null` for space-wide conversions. Default to **food-scoped** for densities (1 TL salt ≠ 1 TL cinnamon) and for piece-units (1 Zehe Knoblauch ≠ 1 Zehe Kardamom). Only use space-wide for shape-only cooking-volume units when the cook genuinely treats them as a fixed volume regardless of contents — rare in practice.
- `base_unit`/`converted_unit` accept nested writes: known unit names match by name; unknown names get created. If you're not sure the unit exists, GET `/api/unit/?query=<name>` first.
- The action does not invalidate the conversion graph cache, but Tandoor reads UC fresh on each recipe GET — Phase 3 will see the new row immediately.

### 2c — Default-unit fix for unitless ingredients

If Table C showed unitless ingredients and `space.default_unit` resolves wrong (e.g. set to `g` while pieces are needed), do **not** silently change `space.default_unit` from this skill — it touches every recipe. Surface the recommendation:

> Recommended: set `space.default_unit` to `Stück` so `1 Zwiebel` / `1 Spitzpaprika` / `1 Zucchini` resolve through the existing `Stück → g` food-scoped UCs.

If the user prefers a per-ingredient PATCH instead:

```bash
curl --noproxy '*' -s -X PATCH \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"unit\": {\"id\": $STUCK_ID, \"name\": \"Stück\"}}" \
  "$URL/api/ingredient/$ING_ID/"
```

## Phase 3 — Verify aggregation

```bash
curl --noproxy '*' -s -H "Authorization: Bearer $TOKEN" "$URL/api/recipe/$RID/" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin); servings = d.get('servings') or 1
print(f\"Recipe: {d.get('name')}  servings={servings}\")
fp = d.get('food_properties') or {}
for pt_id in sorted(fp.keys(), key=lambda k: fp[k].get('order', 0)):
    p = fp[pt_id]; total = p.get('total_value', 0); miss = ' MISSING' if p.get('missing_value') else ''
    print(f\"\\n{p.get('name'):18s} total={total:>10.2f} {p.get('unit') or '':5s}  per_serving={total/servings:>8.2f}{miss}\")
    for fid, fv in p.get('food_values', {}).items():
        v = fv.get('value'); flags = []
        if 'missing_conversion' in fv:
            mc = fv['missing_conversion']; flags.append(f\"missing_conv {mc['base_unit']['name']}->{mc['converted_unit']['name']}\")
        if fv.get('missing_unit'): flags.append('missing_unit')
        flags_s = '  ['+', '.join(flags)+']' if flags else ''
        print(f\"    {fv['food']['name']:24s} = {v if v is not None else 'None':>10}{flags_s}\")
"
```

A clean result has, for every PropertyType:
- `missing_value: false`
- Every entry in `food_values` has a numeric `value` (no `None`)
- No `missing_conversion` or `missing_unit` flags on any food_value
- `total_value > 0` (unless the recipe is genuinely zero-anything)

If anything's still flagged, the source of the gap is one of:

| Symptom | Likely cause | Fix |
|---|---|---|
| `missing_conversion: {base_unit: X, converted_unit: pfu}` | UC missing or doesn't bridge X → pfu | Add a food-scoped UC (Phase 2). |
| `missing_unit: true` on a food + `space.default_unit IS NULL` | Ingredient has no unit + space has no default | Set `space.default_unit` (user owns this) OR PATCH the ingredient with a unit. |
| `value: None` everywhere for a food | `properties_food_unit IS NULL` or `properties_food_amount = 0` | Phase 1 (run `/fdc/` or manual PATCH). |
| `value: 0` for one PropertyType only | Nutrient_id mismatch in FDC response | Patch that single Property via `PATCH /api/property/$PID/`. |
| Total looks orders-of-magnitude off | A food's stored property is wrong (pre-existing data quality) | Out of scope for this skill — surface to user as "data quality issue on food $FID, not a backfill gap". |

Sanity-check the per-serving totals against expected ranges for the dish type:
- Risotto with cream/butter: 400–600 kcal/serving
- Vegetable curry on rice: 350–500 kcal/serving
- Salad-based main: 200–400 kcal/serving

If the result is wildly off, investigate — don't claim success.

## API quick reference

| Endpoint | Method | Notes |
|---|---|---|
| `/api/recipe/{id}/` | GET | `food_properties` is the computed aggregate. Computed on every GET; no cache. |
| `/api/property-type/` | GET | PropertyType list with `fdc_id` mapping. |
| `/api/space/` | GET | Read `default_unit`. |
| `/api/food/{id}/` | GET | Read `properties_food_unit`, `properties_food_amount`, `fdc_id`, `properties`. |
| `/api/food/{id}/` | PATCH | Writable nested — set pfu+amount+fdc_id+properties in one call. Replaces the M2M when `properties` is included. |
| `/api/food/{id}/fdc/` | POST | `{fdc_id: N}`. Auto-sets pfu=g, amount=100. 200 on success, 429 on FDC rate limit, 500 on parse error. |
| `/api/fdc-search/` | GET | `?query=<str>&dataType=Foundation\|SR%20Legacy\|Survey%20(FNDDS)\|Branded`. Defaults to Foundation. |
| `/api/property/{id}/` | PATCH | Fix one Property when `/fdc/` produced a 0 (nutrient_id mismatch). |
| `/api/unit-conversion/` | GET | `?food_id=N` lists food-scoped UCs (plus space-wide if `food_id` is omitted). |
| `/api/unit-conversion/` | POST | Name-keyed idempotent. `food: null` = space-wide. Nested-write creates unknown unit names. |
| `/api/unit/?query=<name>` | GET | Look up Unit id by name. |
| `/api/ingredient/{id}/` | PATCH | Set `unit` on a specific ingredient (alternative to changing `space.default_unit`). |

## Don'ts

- **Don't invent nutrition values.** USDA FDC, SR Legacy, or an official national database, with a URL the user can verify. Reading "looks right" off training data is not a source.
- **Don't trust an FDC string match.** Always cross-check the actual macro values against an independent source before writing.
- **Don't write Kalorien as 0** when the FDC response returned 0. That's a nutrient_id mismatch. Patch from Atwater (`4*P + 4*C + 9*F`, per 100g) or switch dataType.
- **Don't assign properties to combined foods** like "Salz und Pfeffer", "Gewürze gemischt". Either split the food (out of scope here) or leave properties unset.
- **Don't create space-wide UCs** for densities that vary by food. 1 TL salt = 6 g; 1 TL cinnamon = 3 g; 1 TL saffron threads = 0.7 g.
- **Don't use `/aiproperties/`** as the default source. It's available, but the LLM has been observed to invent values. Use it only when both FDC and web research come up empty, and verify the result against an independent source before accepting.
- **Don't change `space.default_unit` silently.** It affects every recipe in the space. Surface a recommendation and let the user decide.
- **Don't dedupe Property rows from this skill.** Some foods carry duplicate `Property` rows of the same PropertyType (a separate cleanup workflow). The aggregator at `property_helper.py:30` iterates `food.properties.all()` and will sum duplicates — so a food with two identical Property rows for Kalorien=358 contributes 716 kcal/100g. If Phase 3 totals look 2× too high, check `food.properties` for duplicate `property_type.id`.
- **Don't use Python `urllib`** for Tandoor calls — sandbox blocks 192.168.x.x. Use `curl --noproxy '*'`.
- **Don't commit** without explicit instruction (per `CLAUDE.md`).

## Related research and skills

- `docs/agents/research/2026-06-07-nutrition-aggregation-and-unit-conversion.md` — full model + endpoint map; the source-of-truth for the wire shapes and aggregation semantics above.
- `.claude/skills/tandoor-url-import/SKILL.md` — invokes this skill after Phase 5 once gaps are identified.
- `.claude/skills/tandoor-recipe-mapping/SKILL.md` — sibling, runs before this skill in the import pipeline.
