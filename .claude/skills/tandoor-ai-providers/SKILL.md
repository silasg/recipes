---
name: tandoor-ai-providers
description: Create, update, list, or delete AI providers in a Tandoor Recipes instance via REST API. Use when the user wants to add OpenRouter/OpenAI/Anthropic/Gemini providers to Tandoor, edit existing provider descriptions or model names, or batch-configure AI providers without using the slow admin UI.
---

# Tandoor AI Providers

## Credentials

Never hardcode credentials or the instance URL. Read them from project root:
- `tandoor_token.txt` — Tandoor API token (Bearer auth)
- `tandoor_url.txt` — Tandoor instance base URL (private, do not commit)
- `openrouter_api_key.txt` — OpenRouter API key (only needed when creating OpenRouter-backed providers)

## Endpoint

`/api/ai-provider/` — DRF `ModelViewSet` (full CRUD).

```
GET    /api/ai-provider/          # list
GET    /api/ai-provider/<id>/     # detail
POST   /api/ai-provider/          # create
PATCH  /api/ai-provider/<id>/     # update
DELETE /api/ai-provider/<id>/     # delete
```

Auth header: `Authorization: Bearer <token>`.

## Fields

| Field | Notes |
|---|---|
| `name` | required, str |
| `model_name` | required, LiteLLM model id (e.g. `openrouter/anthropic/claude-sonnet-4.5`) |
| `api_key` | required on create, **write-only** (never returned by GET) |
| `url` | optional, e.g. `https://openrouter.ai/api/v1` |
| `description` | optional, plain text |
| `space` | see below |
| `log_credit_cost` | superuser-only, ignored otherwise |

## Space handling (important quirk)

The serializer (`cookbook/serializer.py:AiProviderSerializer.handle_global_space_logic`) overrides `space` based on user role:

| User role | `space` in body | Resulting `space` |
|---|---|---|
| superuser | omitted / null | `null` (global, all spaces) |
| superuser | any truthy value | request.space (your active space) |
| non-superuser | any | request.space |

To target your active space: send `"space": 1` (or any truthy value — it gets overwritten with request.space).
To target a *different* space: not possible via API; switch your active space first via UI.

**Critical:** on `PATCH`, omitting `space` causes a superuser's provider to silently become global. Always include `"space": <id>` in PATCH bodies if you want it to stay space-scoped.

## Resolving the active space

```bash
URL=$(cat tandoor_url.txt)
TOKEN=$(cat tandoor_token.txt)
curl -sS --noproxy '*' -H "Authorization: Bearer $TOKEN" \
  "$URL/api/user-space/" \
  | jq -r '.results[] | select(.active==true) | .space' | head -1
```

## Existing helper script

`contrib/add_ai_providers.sh` — reusable bash script that bulk-creates the user's standard provider lineup (OpenRouter-backed Sonnet/Haiku/GPT-4o/DeepSeek/Gemini variants) with descriptions. Reads `TANDOOR_URL`, `TANDOOR_TOKEN`, `OR_KEY` from env. Edit the `create "..."` lines to change the list.

```bash
export TANDOOR_URL=$(cat tandoor_url.txt)
export TANDOOR_TOKEN=$(cat tandoor_token.txt)
export OR_KEY=$(cat openrouter_api_key.txt)
./contrib/add_ai_providers.sh
```

Not idempotent — re-running creates duplicates.

## Creating a single provider (curl)

```bash
URL=$(cat tandoor_url.txt)
TOKEN=$(cat tandoor_token.txt)
ORKEY=$(cat openrouter_api_key.txt)
SPACE_ID=1  # or resolve dynamically as above
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg n "OR Claude Sonnet 4.5" \
    --arg m "openrouter/anthropic/claude-sonnet-4.5" \
    --arg k "$ORKEY" \
    --arg u "https://openrouter.ai/api/v1" \
    --arg d "Best reasoning + structured output. Use for hard mapping tasks." \
    --argjson s $SPACE_ID \
    '{name:$n, model_name:$m, api_key:$k, url:$u, description:$d, space:$s}')" \
  "$URL/api/ai-provider/"
```

## Updating a description (PATCH)

```bash
URL=$(cat tandoor_url.txt)
TOKEN=$(cat tandoor_token.txt)
ID=2
curl -sS --noproxy '*' -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "new description here", "space": 1}' \
  "$URL/api/ai-provider/$ID/"
```

Remember to include `space` to avoid the global-override quirk.

## Picking model_name

OpenRouter model slugs follow the LiteLLM convention: `openrouter/<provider>/<model>`.
Examples: `openrouter/anthropic/claude-sonnet-4.5`, `openrouter/google/gemini-3.1-flash-lite`, `openrouter/openai/gpt-5.4`.

To verify a slug exists, hit `https://openrouter.ai/api/v1/models` (proxy may block — call from user's shell with `!` prefix if needed).

## Model-to-task recommendations (Tandoor-specific)

Based on Tandoor's AI features (`AiLog.F_FILE_IMPORT`, `F_STEP_SORT`, `F_FOOD_PROPERTIES`, `F_RECIPE_PROPERTIES`):

| Task | Recommended primary | Why |
|---|---|---|
| Nutrition properties for ingredients | Gemini Flash Lite (3.1 or 2.5) | Cheapest, world knowledge sufficient |
| Mapping ingredients → recipe steps | Claude Haiku 4.5 | Reliable anaphora resolution, cheap |
| Cookbook photo OCR | Gemini Flash (3.5 or 2.5) | Strongest vision on dense layouts |
| Social media imports (incl. video) | Gemini Flash | Only vendor with native video support |
| Webpages without semantic tags | Claude Sonnet 4.5 | Best reasoning over messy prose |
| PDFs (text or scanned) | Gemini Flash | 1M+ context handles full cookbook PDFs |

Set Haiku 4.5 as `ai_default_provider` in space settings for everyday calls.

## Verification

After create/update, GET the provider and confirm `space` is what you expected (1 for Unser Space, null for global). `api_key` will never be returned — that's normal.
