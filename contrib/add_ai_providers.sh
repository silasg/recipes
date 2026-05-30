#!/usr/bin/env bash
# Bulk-create AI providers in a Tandoor instance via REST API.
#
# Usage:
#   TANDOOR_URL=$(cat tandoor_url.txt) \
#   TANDOOR_TOKEN=$(cat tandoor_token.txt) \
#   OR_KEY=$(cat openrouter_api_key.txt) \
#   ./add_ai_providers.sh
#
# Providers land in your currently-active space (resolved at runtime).
# Re-running creates duplicates - delete old ones via UI first if needed.
#
# Requires: curl, jq

set -euo pipefail

: "${TANDOOR_URL:?need TANDOOR_URL (your Tandoor instance base URL)}"
: "${TANDOOR_TOKEN:?need TANDOOR_TOKEN (Tandoor API token)}"
: "${OR_KEY:?need OR_KEY (OpenRouter API key)}"

OR_URL="${OR_URL:-https://openrouter.ai/api/v1}"
AUTH="Authorization: Bearer $TANDOOR_TOKEN"

echo "Resolving active space..."
SPACE_ID=$(curl -sS --noproxy '*' -H "$AUTH" "$TANDOOR_URL/api/user-space/" \
  | jq -r '.results[] | select(.active==true) | .space' | head -1)
[[ -n "$SPACE_ID" ]] || { echo "Could not resolve active space"; exit 1; }
echo "  -> space id $SPACE_ID"

create() {
  local name="$1" model="$2" desc="$3"
  local payload code body resp
  payload=$(jq -n \
    --arg n "$name" --arg m "$model" --arg k "$OR_KEY" \
    --arg u "$OR_URL" --arg d "$desc" --argjson s "$SPACE_ID" \
    '{name:$n, model_name:$m, api_key:$k, url:$u, description:$d, space:$s}')
  resp=$(curl -sS --noproxy '*' -X POST \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d "$payload" -w $'\n__HTTP__%{http_code}' \
    "$TANDOOR_URL/api/ai-provider/")
  body=${resp%__HTTP__*}
  code=${resp##*__HTTP__}
  printf "[HTTP %s] %s\n" "$code" "$name"
  [[ "$code" =~ ^2 ]] || { echo "  ERROR: $body"; return 1; }
}

create "OR Gemini Flash 3.5" \
  "openrouter/google/gemini-3.5-flash" \
  "Vision + video + huge context window. Primary for: cookbook photos (OCR), social media imports incl. video, scanned/long PDFs, webpages without semantic tags. Fallback: Gemini 2.5 Flash."

create "OR Claude Sonnet 4.5" \
  "openrouter/anthropic/claude-sonnet-4.5" \
  "Best reasoning + most reliable strict structured output. Primary for: webpages without semantic tags (digging structure out of messy prose). Fallback for: ingredient-to-step mapping when Haiku misses. No video. ~5x pricier than Haiku - reserve for hard cases."

create "OR GPT-4o" \
  "openrouter/openai/gpt-4o" \
  "Solid all-rounder with strong native JSON mode. Fallback for: cookbook photos and scanned PDFs when Gemini OCR misreads. 128K context only - avoid for full cookbook PDFs. No video."

create "OR DeepSeek V3" \
  "openrouter/deepseek/deepseek-chat" \
  "Cheap text-only reasoning. Fallback when other text models are rate-limited or down. No vision, no video - not useful for any image/video import task."

create "OR Gemini 3.1 Flash Lite" \
  "openrouter/google/gemini-3.1-flash-lite" \
  "Cheapest fast model with vision. Primary for: filling nutrition properties for ingredients (high-volume, simple structured extraction with world knowledge). Fallback: Gemini 2.5 Flash Lite."

create "OR Gemini 2.5 Flash" \
  "openrouter/google/gemini-2.5-flash" \
  "Stable fallback for Gemini 3.5 Flash. Same use cases: cookbook photos, video, long/scanned PDFs, webpages. Use if 3.5 has issues or rate limits."

create "OR Gemini 2.5 Flash Lite" \
  "openrouter/google/gemini-2.5-flash-lite" \
  "Stable fallback for Gemini 3.1 Flash Lite. Same use case: cheap nutrition property extraction at high volume."

create "OR Claude Haiku 4.5" \
  "openrouter/anthropic/claude-haiku-4.5" \
  "Cheap, fast, reliable structured output. Recommended as ai_default_provider. Primary for: mapping ingredients to recipe steps (anaphora resolution like 'the dough'), general everyday calls. No video."

echo "Done."
