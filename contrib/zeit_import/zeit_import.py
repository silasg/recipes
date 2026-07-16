#!/usr/bin/env python3
"""
ZEIT Wochenmarkt -> Tandoor recipe import (deterministic, no AI).

Fetches a ZEIT (Magazin) Wochenmarkt article with a logged-in cookie jar,
extracts the schema.org Recipe objects from the page's ItemList JSON-LD,
injects the article-body paragraphs as recipeInstructions, parses the result
through Tandoor's own /api/recipe-from-source/ endpoint, persists the recipe,
attaches the article image, distributes ingredients to their first-mention
step and prints a nutrition/cleanliness audit report.

All HTTP goes through `curl` subprocesses (see README: Python urllib is
blocked against private IPs in the sandbox this was built in, and the cookie
jar handling relies on curl's Netscape jar support anyway).

Usage:
    python3 zeit_import.py [--dry-run] [--force] [--keep-going]
        [--tandoor-url URL] [--token-file FILE] URL [URL ...]
"""

import argparse
import copy
import html as html_lib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("zeit_import")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
ZEIT_KEYWORD_NAME = "ZEIT Magazin"
LOGIN_WALL_MARKER = "<title>Bei DIE ZEIT anmelden</title>"
PAYWALL_TEASER_MARKER = "data-is-truncated-by-paywall"
GENERIC_LD_NAMES = {"", "wochenmarkt"}

# kitshn iOS auto-timer breaks on Unicode dashes in duration ranges
DASH_RANGE_RE = re.compile(r"(\d+)\s*[‐‑‒–—−]\s*(\d+)(\s*(?:Minuten|Min|Sekunden|Sek|Stunden|Std|h|min))")

H2_RE = re.compile(r"<h2([^>]*)>(.*?)</h2>", re.S)
P_RE = re.compile(r"<p\s+[^>]*class=\"([^\"]*)\"[^>]*>(.*?)</p>", re.S)
LD_SCRIPT_RE = re.compile(r"<script type=\"application/ld\+json\">(.*?)</script>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


class ZeitImportError(Exception):
    pass


class LoginWallError(ZeitImportError):
    pass


# ---------------------------------------------------------------------------
# HTTP plumbing (single curl choke point, monkeypatched in tests)
# ---------------------------------------------------------------------------

def run_curl(args: list[str]) -> tuple[int, bytes]:
    """Run curl with the given extra args; return (http_status, body_bytes)."""
    fd, out_path = tempfile.mkstemp(prefix="zeit_curl_")
    os.close(fd)
    try:
        proc = subprocess.run(["curl", "-sS", "-o", out_path, "-w", "%{http_code}", *args],
                              capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise ZeitImportError(f"curl exited {proc.returncode}: {proc.stderr.strip()}")
        with open(out_path, "rb") as f:
            body = f.read()
        return int(proc.stdout.strip() or 0), body
    finally:
        os.unlink(out_path)


def tandoor_api(method: str, path: str, payload: dict | None = None, *, base: str, token: str) -> tuple[int, dict]:
    """Call the Tandoor REST API (private IP -> --noproxy). Returns (status, parsed_json_or_empty_dict)."""
    args = ["--noproxy", "*", "-X", method, "-H", f"Authorization: Bearer {token}"]
    if payload is not None:
        args += ["-H", "Content-Type: application/json", "--data", json.dumps(payload)]
    args.append(f"{base}{path}")
    status, body = run_curl(args)
    if not body.strip():
        return status, {}
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {"_raw": body.decode("utf-8", errors="replace")}


def fetch_zeit_page(url: str, jar_path: str) -> str:
    """Fetch a zeit.de page through the cookie jar (public host -> no --noproxy)."""
    status, body = run_curl(["-L", "-A", USER_AGENT, "-b", jar_path, "-c", jar_path, url])
    if status != 200:
        raise ZeitImportError(f"GET {url} returned HTTP {status}")
    return body.decode("utf-8", errors="replace")


def download_to_tempfile(url: str, suffix: str = ".jpg") -> str:
    """Download a public URL (e.g. img.zeit.de) to a temp file with a real image extension."""
    status, body = run_curl(["-L", "-A", USER_AGENT, url])
    if status != 200 or not body:
        raise ZeitImportError(f"image download failed: HTTP {status} for {url}")
    fd, path = tempfile.mkstemp(prefix="zeit_img_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(body)
    return path


def upload_recipe_image(recipe_id: int, image_path: str, *, base: str, token: str) -> tuple[int, dict]:
    args = ["--noproxy", "*", "-X", "PUT", "-H", f"Authorization: Bearer {token}",
            "-F", f"image=@{image_path}", f"{base}/api/recipe/{recipe_id}/image/"]
    status, body = run_curl(args)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {"_raw": body.decode("utf-8", errors="replace")}


# ---------------------------------------------------------------------------
# Cookie jar bootstrap
# ---------------------------------------------------------------------------

def build_netscape_jar(cookie_header: str) -> str:
    """Convert a single-line Cookie-header export into Netscape cookie jar format."""
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookie_header.strip().split("; "):
        name, sep, value = pair.partition("=")
        if not sep:
            continue
        lines.append(f".zeit.de\tTRUE\t/\tTRUE\t2000000000\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def ensure_cookie_jar(jar_path: str, cookies_path: str) -> str:
    """Create the Netscape jar from the Cookie-header export if it doesn't exist yet."""
    if os.path.exists(jar_path):
        return jar_path
    if not os.path.exists(cookies_path):
        raise ZeitImportError(f"neither cookie jar ({jar_path}) nor cookie export ({cookies_path}) found - "
                              f"export your zeit.de cookies to {cookies_path}")
    with open(cookies_path, encoding="utf-8") as f:
        header = f.read()
    with open(jar_path, "w", encoding="utf-8") as f:
        f.write(build_netscape_jar(header))
    log.info("bootstrapped cookie jar %s from %s", jar_path, cookies_path)
    return jar_path


def check_page_access(html: str) -> list[str]:
    """Raise on login wall, warn on paywall teaser. Returns list of warnings."""
    if LOGIN_WALL_MARKER in html:
        raise LoginWallError("got the ZEIT login page - session cookies are dead; re-export zeit_cookies.txt "
                             "from a logged-in browser and delete the stale zeit_cookie_jar.txt")
    warnings = []
    if PAYWALL_TEASER_MARKER in html:
        warnings.append("page is truncated by paywall (not logged in?) - instructions will be incomplete")
    return warnings


# ---------------------------------------------------------------------------
# Extraction: JSON-LD + article body paragraphs
# ---------------------------------------------------------------------------

def strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(TAG_RE.sub("", fragment))).strip()


def normalize_dashes(text: str) -> str:
    """ASCII-ize Unicode dashes in duration ranges ('2–3 Minuten' -> '2-3 Minuten')."""
    return DASH_RANGE_RE.sub(r"\1-\2\3", text)


def is_credit_paragraph(text: str) -> bool:
    return text.startswith("Übersetzung:") or text.startswith("©") or "Guardian News & Media" in text


def parse_servings(recipe_yield) -> tuple[int | None, str]:
    """'4 Portionen' -> (4, 'Portionen')."""
    if not recipe_yield:
        return None, ""
    m = re.search(r"(\d+)", recipe_yield)
    if not m:
        return None, recipe_yield.strip()
    return int(m.group(1)), recipe_yield[m.end():].strip()


def _repair_json(text: str) -> str:
    """Fix comma defects ZEIT's template emits when a list slot is empty (e.g. '[,"160 g Mehl"' on 2026/26)."""
    text = re.sub(r"([\[,])\s*,", r"\1", text)  # leading comma after [ or double comma
    return re.sub(r",\s*([\]}])", r"\1", text)  # trailing comma before ] or }


def _ld_recipes(html: str) -> list[dict]:
    for m in LD_SCRIPT_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            try:
                data = json.loads(_repair_json(m.group(1)))
            except ValueError:
                continue
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            recipes = []
            for element in data.get("itemListElement", []):
                item = element.get("item", element) if isinstance(element, dict) else None
                if isinstance(item, dict) and item.get("@type") == "Recipe":
                    recipes.append(item)
            if recipes:
                return recipes
    raise ZeitImportError("no ItemList JSON-LD with Recipe items found on page")


def _body_events(html: str) -> tuple[list[tuple[int, str, bool]], list[tuple[int, str]]]:
    """Return ([(pos, text, has_subheading_class)], [(pos, paragraph_text)]) from the visible article body."""
    # everything from the hidden paywall-teaser copy onwards is a duplicate - cut it off
    cutoff = html.find("data-paywall hidden")
    region = html[:cutoff] if cutoff != -1 else html
    h2s = []
    for m in H2_RE.finditer(region):
        text = strip_tags(m.group(2))
        if text:
            h2s.append((m.start(), text, "article__subheading" in m.group(1)))
    paragraphs = []
    for m in P_RE.finditer(region):
        classes = m.group(1)
        if "paragraph" not in classes or "article__item" not in classes:
            continue
        text = strip_tags(m.group(2))
        if text and not is_credit_paragraph(text):
            paragraphs.append((m.start(), text))
    return h2s, paragraphs


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().casefold()


def _anchor_recipes(ld_recipes: list[dict], h2s: list[tuple[int, str, bool]]) -> list[tuple[dict, str, int | None]]:
    """Match each JSON-LD recipe to its body <h2>. Returns [(ld_item, resolved_name, anchor_pos_or_None)]."""
    used = set()
    anchors: list[int | None] = [None] * len(ld_recipes)
    names = [item.get("name") or "" for item in ld_recipes]
    # pass 1: match by name against any h2 (class-independent - some pages lack article__subheading)
    for idx, item in enumerate(ld_recipes):
        norm = _norm_name(names[idx])
        if norm in GENERIC_LD_NAMES:
            continue
        for h_idx, (pos, text, _) in enumerate(h2s):
            h_norm = _norm_name(text)
            if h_idx not in used and (h_norm == norm or norm in h_norm or h_norm in norm):
                anchors[idx] = h_idx
                used.add(h_idx)
                break
    # pass 2: generic/unmatched names get the unused subheading h2s in document order (name from h2)
    free_subheadings = [i for i, (_, _, sub) in enumerate(h2s) if sub and i not in used]
    for idx in range(len(ld_recipes)):
        if anchors[idx] is None and free_subheadings:
            h_idx = free_subheadings.pop(0)
            anchors[idx] = h_idx
            used.add(h_idx)
            if _norm_name(names[idx]) in GENERIC_LD_NAMES:
                names[idx] = h2s[h_idx][1]
    return [(item, names[idx], h2s[anchors[idx]][0] if anchors[idx] is not None else None)
            for idx, item in enumerate(ld_recipes)]


def extract_recipes(html: str, page_url: str) -> list[dict]:
    """Build one schema.org Recipe dict per JSON-LD recipe, with body paragraphs as recipeInstructions.

    The returned dicts never contain a 'url' key: Tandoor would re-scrape the
    page with a host-specific parser and return empty ingredients.
    """
    ld_recipes = _ld_recipes(html)
    h2s, paragraphs = _body_events(html)
    anchored = _anchor_recipes(ld_recipes, h2s)

    boundaries = sorted(pos for _, _, pos in anchored if pos is not None)
    results = []
    for item, name, anchor_pos in anchored:
        if anchor_pos is None:
            # fallback: paragraphs after the first h2, or all but the intro paragraph
            log.warning("no h2 anchor found for recipe %r - falling back to positional paragraph split", name)
            start = h2s[0][0] if h2s else (paragraphs[0][0] if paragraphs else 0)
            end = float("inf")
        else:
            start = anchor_pos
            later = [b for b in boundaries if b > anchor_pos]
            end = later[0] if later else float("inf")
        texts = [normalize_dashes(text) for pos, text in paragraphs if start < pos < end]
        if not texts:
            log.warning("recipe %r: no instruction paragraphs found in body", name)
        recipe = {"@context": "https://schema.org", "@type": "Recipe", "name": name,
                  "recipeIngredient": item.get("recipeIngredient") or [],
                  "recipeInstructions": [{"@type": "HowToStep", "text": t} for t in texts]}
        for key in ("image", "recipeYield", "totalTime", "description", "keywords", "author", "datePublished"):
            if item.get(key) is not None:
                recipe[key] = item[key]
        results.append(recipe)
    return results


def image_url_from_ld(item: dict) -> str | None:
    image = item.get("image")
    if isinstance(image, dict):
        return image.get("url")
    if isinstance(image, list) and image:
        return image_url_from_ld({"image": image[0]})
    if isinstance(image, str):
        return image
    return None


# ---------------------------------------------------------------------------
# Transform parsed recipe for persisting
# ---------------------------------------------------------------------------

def transform_recipe(parsed: dict, page_url: str, zeit_keyword: dict, recipe_yield: str | None = None) -> dict:
    """Make the recipe-from-source parse persistable via POST /api/recipe/."""
    recipe = copy.deepcopy(parsed)
    recipe["keywords"] = [k for k in recipe.get("keywords", []) if k.get("import_keyword")] + [dict(zeit_keyword)]
    for step_idx, step in enumerate(recipe.get("steps", [])):
        if step.get("order") is None:
            step["order"] = step_idx
        for ing_idx, ingredient in enumerate(step.get("ingredients", [])):
            if ingredient.get("order") is None:
                ingredient["order"] = ing_idx
    recipe["source_url"] = page_url

    servings = recipe.get("servings")
    if isinstance(servings, str):
        count, text = parse_servings(servings)
        recipe["servings"] = count if count else 1
        if text and not recipe.get("servings_text"):
            recipe["servings_text"] = text
    elif not servings:
        count, text = parse_servings(recipe_yield)
        recipe["servings"] = count if count else 1
        if text and not recipe.get("servings_text"):
            recipe["servings_text"] = text
    return recipe


# ---------------------------------------------------------------------------
# First-mention ingredient -> step mapping (pure)
# ---------------------------------------------------------------------------

def _match_candidates(food_name: str) -> list[tuple[str, str]]:
    """Ordered (kind, token) candidates: full name as word, prefixes (>=4 chars), then first word of compounds."""
    name = food_name.strip()
    if not name:
        return []
    candidates: list[tuple[str, str]] = [("word", name)]
    candidates += [("prefix", name[:length]) for length in range(len(name), 3, -1)]
    first_word = re.split(r"[\s\-]+", name)[0]
    if first_word and first_word != name:
        candidates.append(("word", first_word))
        candidates += [("prefix", first_word[:length]) for length in range(len(first_word), 3, -1)]
    return candidates


def map_ingredients_to_steps(step_texts: list[str], foods: list[tuple[int, str]]) -> dict[int, int]:
    """First-mention mapping: {ingredient_key: earliest step index}. Unmatched ingredients are omitted."""
    lowered = [t.lower() for t in step_texts]
    result = {}
    for key, food_name in foods:
        for kind, token in _match_candidates(food_name):
            pattern = re.compile(r"\b" + re.escape(token.lower()) + (r"\b" if kind == "word" else ""))
            hit = next((idx for idx, text in enumerate(lowered) if pattern.search(text)), None)
            if hit is not None:
                result[key] = hit
                break
    return result


def build_step_assignments(step_texts: list[str], ingredients: list[dict]) -> tuple[dict[int, list[dict]], list[str]]:
    """Disjoint step-index -> ingredient-objects assignment; unmatched ingredients stay on step 0."""
    foods = [(ing["id"], (ing.get("food") or {}).get("name") or "") for ing in ingredients]
    mapping = map_ingredients_to_steps(step_texts, foods)
    assignments: dict[int, list[dict]] = {idx: [] for idx in range(len(step_texts))}
    unmatched = []
    for ing in ingredients:
        step_idx = mapping.get(ing["id"])
        if step_idx is None:
            unmatched.append((ing.get("food") or {}).get("name") or "?")
            step_idx = 0
        assignments[step_idx].append(ing)
    return assignments, unmatched


# ---------------------------------------------------------------------------
# Pipeline stages against Tandoor
# ---------------------------------------------------------------------------

def resolve_zeit_keyword(*, base: str, token: str) -> dict:
    query = urllib.parse.quote(ZEIT_KEYWORD_NAME)
    status, body = tandoor_api("GET", f"/api/keyword/?query={query}", base=base, token=token)
    if status == 200:
        for row in body.get("results", []):
            if row.get("name") == ZEIT_KEYWORD_NAME:
                return {"id": row["id"], "name": row["name"]}
    return {"name": ZEIT_KEYWORD_NAME}


def parse_via_tandoor(ld_recipe: dict, page_url: str, *, base: str, token: str) -> dict:
    """Non-mutating: let Tandoor split ingredient strings into amount/unit/food/note."""
    payload = {"url": page_url, "data": json.dumps(ld_recipe, ensure_ascii=False)}
    status, body = tandoor_api("POST", "/api/recipe-from-source/", payload, base=base, token=token)
    if status != 200:
        raise ZeitImportError(f"recipe-from-source returned HTTP {status}: {str(body)[:300]}")
    if body.get("error"):
        raise ZeitImportError(f"recipe-from-source soft failure: {body.get('msg')}")
    return body


def print_parsed_recipe(recipe: dict, duplicates: list):
    print(f"\n=== {recipe.get('name')} ===")
    print(f"servings: {recipe.get('servings')} {recipe.get('servings_text') or ''}".rstrip())
    print(f"working/waiting time: {recipe.get('working_time')}/{recipe.get('waiting_time')} min")
    steps = recipe.get("steps", [])
    print(f"steps: {len(steps)}")
    print(f"{'amount':>8}  {'unit':<8} {'food':<28} note")
    for step in steps:
        for ing in step.get("ingredients", []):
            amount = ing.get("amount") or ""
            unit = (ing.get("unit") or {}).get("name") or ""
            food = (ing.get("food") or {}).get("name") or ""
            print(f"{amount:>8}  {unit:<8} {food:<28} {ing.get('note') or ''}".rstrip())
    if duplicates:
        print(f"DUPLICATES: {', '.join(f'#{d.get('id')} {d.get('name')}' for d in duplicates)}")


def persist_recipe(recipe: dict, *, base: str, token: str) -> int:
    status, body = tandoor_api("POST", "/api/recipe/", recipe, base=base, token=token)
    if status not in (200, 201):
        raise ZeitImportError(f"POST /api/recipe/ returned HTTP {status}: {str(body)[:500]}")
    return body["id"]


def attach_image(recipe_id: int, image_url: str, *, base: str, token: str) -> str | None:
    """Download + attach the article image. Failure = warn, don't abort."""
    tmp_path = None
    try:
        tmp_path = download_to_tempfile(image_url)
        status, body = upload_recipe_image(recipe_id, tmp_path, base=base, token=token)
        image_field = body.get("image") or ""
        if status == 200 and image_field.startswith("/media/recipes/"):
            return image_field
        log.warning("image upload for recipe %s failed: HTTP %s %s", recipe_id, status, str(body)[:200])
    except ZeitImportError as exc:
        log.warning("image attach for recipe %s failed: %s", recipe_id, exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return None


def distribute_ingredients(recipe_id: int, *, base: str, token: str) -> list[str]:
    """First-mention mapping over the persisted recipe. PATCHes every step (disjoint sets). Returns unmatched food names."""
    status, recipe = tandoor_api("GET", f"/api/recipe/{recipe_id}/", base=base, token=token)
    if status != 200:
        raise ZeitImportError(f"GET /api/recipe/{recipe_id}/ returned HTTP {status}")
    steps = recipe.get("steps", [])
    if len(steps) < 2:
        return []
    step_texts = [s.get("instruction") or "" for s in steps]
    ingredients = [ing for s in steps for ing in s.get("ingredients", [])]
    assignments, unmatched = build_step_assignments(step_texts, ingredients)
    for idx, step in enumerate(steps):
        status, _ = tandoor_api("PATCH", f"/api/step/{step['id']}/", {"ingredients": assignments[idx]}, base=base, token=token)
        if status != 200:
            raise ZeitImportError(f"PATCH /api/step/{step['id']}/ returned HTTP {status}")
        log.info("  step %d (pk %s): %d ingredients", idx + 1, step["id"], len(assignments[idx]))
    return unmatched


def audit_recipe(recipe_id: int, *, base: str, token: str) -> str:
    """Read-only audit: missing nutrition values/conversions + new-food/new-unit report."""
    _, recipe = tandoor_api("GET", f"/api/recipe/{recipe_id}/", base=base, token=token)
    lines = [f"audit for recipe #{recipe_id} '{recipe.get('name')}':"]

    for _, prop in sorted((recipe.get("food_properties") or {}).items(), key=lambda kv: int(kv[0])):
        for food_value in (prop.get("food_values") or {}).values():
            food_name = (food_value.get("food") or {}).get("name") or "?"
            if food_value.get("value") is None:
                lines.append(f"  [missing value]      {prop.get('name')}: {food_name}")
            conversion = food_value.get("missing_conversion")
            if conversion:
                lines.append(f"  [missing conversion] {prop.get('name')}: {food_name} "
                             f"{conversion['base_unit']['name']} -> {conversion['converted_unit']['name']}")
            if food_value.get("missing_unit"):
                lines.append(f"  [missing unit]       {prop.get('name')}: {food_name} (no unit, no space default)")

    foods, units = {}, {}
    for step in recipe.get("steps", []):
        for ing in step.get("ingredients", []):
            if ing.get("food"):
                foods[ing["food"]["id"]] = ing["food"]["name"]
            if ing.get("unit"):
                units[ing["unit"]["id"]] = ing["unit"]["name"]

    for food_id, name in sorted(foods.items()):
        _, food = tandoor_api("GET", f"/api/food/{food_id}/?extended=1", base=base, token=token)
        numrecipe = food.get("numrecipe")
        covered = {p["property_type"]["id"] for p in (food.get("properties") or []) if p.get("property_amount") is not None}
        flag = "  <- NEW food (needs nutrition backfill)" if numrecipe == 1 else ""
        lines.append(f"  food {name} (id {food_id}): numrecipe={numrecipe}, nutrition {len(covered)}/4{flag}")
    for unit_id, name in sorted(units.items()):
        _, unit = tandoor_api("GET", f"/api/unit/{unit_id}/?extended=1", base=base, token=token)
        numrecipe = unit.get("numrecipe")
        flag = "  <- NEW unit" if numrecipe == 1 else ""
        lines.append(f"  unit {name} (id {unit_id}): numrecipe={numrecipe}{flag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-URL driver
# ---------------------------------------------------------------------------

def process_url(url: str, *, base: str, token: str, jar_path: str, dry_run: bool, force: bool) -> bool:
    """Import all recipes on one Wochenmarkt page. Returns True on success."""
    log.info("fetching %s", url)
    html = fetch_zeit_page(url, jar_path)
    for warning in check_page_access(html):
        log.warning("%s", warning)
    ld_recipes = extract_recipes(html, url)
    log.info("found %d recipe(s) in JSON-LD", len(ld_recipes))

    ok = True
    for ld_recipe in ld_recipes:
        parse_response = parse_via_tandoor(ld_recipe, url, base=base, token=token)
        recipe, duplicates = parse_response["recipe"], parse_response.get("duplicates") or []

        if dry_run:
            print_parsed_recipe(recipe, duplicates)
            continue
        if duplicates and not force:
            dup_list = ", ".join(f"#{d.get('id')} {d.get('name')}" for d in duplicates)
            log.warning("skipping '%s': duplicate of %s (use --force to import anyway)", recipe.get("name"), dup_list)
            continue

        zeit_keyword = resolve_zeit_keyword(base=base, token=token)
        payload = transform_recipe(recipe, url, zeit_keyword, recipe_yield=ld_recipe.get("recipeYield"))
        recipe_id = persist_recipe(payload, base=base, token=token)
        log.info("created recipe #%d '%s'", recipe_id, payload.get("name"))

        image_url = image_url_from_ld(ld_recipe)
        if image_url:
            image_field = attach_image(recipe_id, image_url, base=base, token=token)
            if image_field:
                log.info("image attached: %s", image_field)
        else:
            log.warning("no image url in JSON-LD")

        unmatched = distribute_ingredients(recipe_id, base=base, token=token)
        if unmatched:
            log.warning("ingredients left on step 1 (no instruction mention): %s", ", ".join(unmatched))

        print(audit_recipe(recipe_id, base=base, token=token))
    return ok


def read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def main():
    parser = argparse.ArgumentParser(description="Deterministic ZEIT Wochenmarkt -> Tandoor recipe import")
    parser.add_argument("urls", nargs="+", metavar="URL", help="zeit.de Wochenmarkt article URLs")
    parser.add_argument("--dry-run", action="store_true", help="fetch + extract + parse only, persist nothing")
    parser.add_argument("--force", action="store_true", help="import even when Tandoor reports duplicates")
    parser.add_argument("--keep-going", action="store_true", help="continue with the next URL after a failure")
    parser.add_argument("--tandoor-url", help="Tandoor base URL (default: $TANDOOR_URL or <project-root>/tandoor_url.txt)")
    parser.add_argument("--token-file", help="file with Tandoor API token (default: $TANDOOR_TOKEN_FILE or <project-root>/tandoor_token.txt)")
    args = parser.parse_args()

    base = args.tandoor_url or os.environ.get("TANDOOR_URL") or read_file(os.path.join(PROJECT_ROOT, "tandoor_url.txt"))
    token_file = args.token_file or os.environ.get("TANDOOR_TOKEN_FILE") or os.path.join(PROJECT_ROOT, "tandoor_token.txt")
    token = read_file(token_file)
    jar_path = os.environ.get("ZEIT_COOKIE_JAR") or os.path.join(PROJECT_ROOT, "zeit_cookie_jar.txt")
    ensure_cookie_jar(jar_path, os.path.join(PROJECT_ROOT, "zeit_cookies.txt"))

    failures = 0
    for url in args.urls:
        try:
            if not process_url(url, base=base.rstrip("/"), token=token, jar_path=jar_path, dry_run=args.dry_run, force=args.force):
                failures += 1
        except ZeitImportError as exc:
            failures += 1
            log.error("%s: %s", url, exc)
            if not args.keep_going:
                sys.exit(1)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
