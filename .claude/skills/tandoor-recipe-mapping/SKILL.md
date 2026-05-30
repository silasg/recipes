---
name: tandoor-recipe-mapping
description: Map a Tandoor recipe's ingredients to their first-mentioned step via the REST API. Use when the user has recipes where all ingredients are dumped on one step ("unmapped") and wants ingredients distributed across the cooking steps where they're first referenced. Also use for auditing which recipes need mapping.
---

# Tandoor Recipe Ingredient → Step Mapping

## When to use

Tandoor recipes imported from URLs/PDFs often arrive with all ingredients on a single "dump" step while subsequent steps have instruction text but no ingredients attached. This skill maps each ingredient to the first step that references it, without using Tandoor's built-in AI endpoint (which costs space credits).

## Credentials

Read from project root (never commit these):
- `tandoor_token.txt` — Tandoor API token
- `tandoor_url.txt` — Tandoor instance base URL

## Data model (Tandoor 2.6.9 / fork)

- `Recipe.steps` is M2M to `Step`
- `Step.ingredients` is M2M to `Ingredient`
- `Ingredient` has **no FK to Recipe** — ingredients only exist on steps. An ingredient with no step is effectively orphaned.
- Each `Ingredient` row is unique (its own pk + food + amount + unit). The same food appearing in multiple recipes produces multiple Ingredient rows.

## Auditing (find unmapped recipes)

Definition of "unmapped": multi-step recipe where all ingredients sit on one step. Single-step recipes are trivially mapped.

```bash
TOKEN=$(cat tandoor_token.txt)
URL=$(cat tandoor_url.txt)
curl -sS --noproxy '*' -H "Authorization: Bearer $TOKEN" "$URL/api/recipe/?limit=100" \
  | jq -r '.results[].id' > /tmp/ids.txt
while read rid; do
  curl -sS --noproxy '*' -H "Authorization: Bearer $TOKEN" "$URL/api/recipe/$rid/" \
    | jq -r '
      .name as $n |
      (.steps | length) as $stepN |
      ([.steps[].ingredients | length] | add // 0) as $total |
      ([.steps[] | select((.ingredients|length) > 0)] | length) as $stepsWithIng |
      (if ($stepN == 1 or $stepsWithIng > 1) then $total else 0 end) as $mapped |
      ($total - $mapped) as $unmapped |
      "\($n): \($mapped) mapped, \($unmapped) unmapped"'
done < /tmp/ids.txt
```

## Mapping algorithm

For each ingredient (identified by `food.name`):
1. Read **all step instructions completely** (don't truncate).
2. Find the **first** step (by `order`) whose instruction text mentions the food name.
3. If the food name doesn't appear literally, check common synonyms (see below).
4. If still no match, leave on the dump step and report it for manual review.

When an ingredient is mentioned in multiple steps, always pick the **first** step (the user's rule). Don't duplicate.

### German synonym map (extend as you encounter new ones)

| Food name | Also matches |
|---|---|
| `Feta` | "Schafskäse" |
| `Hokkaido` | "Kürbis" |
| `Basmatireis` | "Reis" |
| `Currypulver` | "Curry" |
| `Chilipulver` | "Chili" (when not the fresh chili) |
| `Kurkumapulver` | "Kurkuma" |
| `glatte Petersilie` | "Petersilie", "Petersilienblätter" |
| `geriebener Gouda` | "Käse" (when only cheese in recipe) |
| `Kidneybohnen` | "Bohnen" |
| `Schlagsahne` | "Sahne" |
| `Schmand` | "Crème fraîche" pattern (different products though — don't conflate) |

For substring matching, lowercase both sides. Plural German endings (-n, -en, -e) are usually handled by substring match since the food name is the stem.

## API quirk: M2M PATCH doesn't auto-unlink

`PATCH /api/step/<id>/` with a new `ingredients` list **sets that step's M2M** to the provided list but does **not** remove the ingredient from other steps' M2Ms. To make state exact, **PATCH every step** (including ones that should become empty) with their full target list.

## Script template (Python)

```python
import json, urllib.request, urllib.error

TOKEN = open('tandoor_token.txt').read().strip()
BASE = open('tandoor_url.txt').read().strip()

def api(method, path, data=None):
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(f'{BASE}{path}', data=body, method=method)
    req.add_header('Authorization', f'Bearer {TOKEN}')
    if body: req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, e.read().decode()

def apply_mapping(recipe_id, mapping):
    """mapping: dict UI-step-index (1-based) -> list of food names"""
    _, rec = api('GET', f'/api/recipe/{recipe_id}/')
    by_food = {i['food']['name']: i for s in rec['steps'] for i in s['ingredients']}
    step_pks = [s['id'] for s in rec['steps']]

    target = {pk: [] for pk in step_pks}
    consumed = set()
    for ui_idx, foods in mapping.items():
        pk = step_pks[ui_idx - 1]
        for f in foods:
            if f not in by_food:
                print(f"  WARN: '{f}' not in recipe; ignored"); continue
            target[pk].append(by_food[f]); consumed.add(f)

    unmapped = [f for f in by_food if f not in consumed]
    if unmapped:
        print(f"  WARN: unmapped (left on first step): {unmapped}")
        for f in unmapped: target[step_pks[0]].append(by_food[f])

    for pk in step_pks:
        code, resp = api('PATCH', f'/api/step/{pk}/', {'ingredients': target[pk]})
        print(f"  step pk={pk}: HTTP {code}, {len(target[pk])} ingredients")

    _, rec2 = api('GET', f'/api/recipe/{recipe_id}/')
    for idx, s in enumerate(rec2['steps'], 1):
        print(f"  UI step {idx}: {[i['food']['name'] for i in s['ingredients']]}")

# Example
apply_mapping(1, {
    1: ['Gemüsebrühe', 'Bulgur'],
    2: ['Paprika, rot'],
    # ...
})
```

## Workflow

1. **Inspect** the recipe with `curl /api/recipe/<id>/` and print every step's full instruction (use `jq -r '.steps | to_entries[] | "=== UI Step \(.key + 1) ===\n\(.value.instruction)\n"'`). **Read all of it.**
2. **List** ingredients by `food.name`.
3. **Build** the UI-index → food-names mapping by reading the German cooking instructions. For each food name, find first mention. For ambiguous synonyms, consult the table above or ask the user.
4. **Show** the proposed mapping to the user before applying (especially the first time, or when many ingredients lack literal matches).
5. **Apply** with `apply_mapping`. The script PATCHes every step with its target list.
6. **Verify** by re-fetching and printing each step's ingredients.

## Don't

- Don't use Tandoor's `/api/ai-step-sort/` endpoint unless the user explicitly asks — it burns AI credits and may need a different mapping policy.
- Don't truncate instruction text when reading. The user explicitly wants full-text analysis.
- Don't move an ingredient to multiple steps. First mention only.
- Don't trust naive heuristics like "largest step = dump" — that breaks on legitimately distributed recipes (e.g. prep step + cook step with even ingredient split).

## Edge cases

- **Single-step recipes**: nothing to do. Skip.
- **Empty first step (no instruction)**: leave its ingredient list empty; map ingredients to the steps with instructions.
- **Ingredients in no step instruction at all**: report to user, leave on dump step. Common for "season to taste" salt/pepper if recipe doesn't say "abschmecken".
- **Foreign or fork-specific instance**: if the user mentions a different Tandoor version (e.g. upstream `develop`), confirm the model structure still has `Step.ingredients` M2M before applying.
