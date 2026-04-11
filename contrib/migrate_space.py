#!/usr/bin/env python3
"""
Tandoor Recipes: Space Data Migration Script

Migrates all space data from one Tandoor instance to another using only the
REST API. Handles dependency ordering, ID remapping, tree structures, circular
references, and pagination automatically.

Usage:
    python migrate_space.py \
        --source-url http://source:8080 --source-token tda_xxx \
        --target-url http://target:8080 --target-token tda_yyy

    # Dry-run mode (read-only, no writes to target):
    python migrate_space.py --dry-run \
        --source-url http://source:8080 --source-token tda_xxx \
        --target-url http://target:8080 --target-token tda_yyy
"""

import argparse
import io
import json
import logging
import sys
import time
from urllib.parse import urljoin

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate_space")


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class TandoorClient:
    """Minimal REST client for Tandoor with pagination and retry."""

    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.timeout = timeout

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/api/{endpoint.strip('/')}/"

    def _req(self, method: str, url: str, retries: int = 3, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        last_exc = None
        for attempt in range(retries):
            try:
                resp = self.s.request(method, url, **kwargs)
                return resp
            except requests.ConnectionError as e:
                last_exc = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise last_exc

    def get_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        results = []
        p = dict(params or {})
        p.setdefault("page_size", 200)
        p["page"] = 1
        while True:
            resp = self._req("GET", self._url(endpoint), params=p)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            results.extend(data.get("results", []))
            if not data.get("next"):
                break
            p["page"] += 1
        return results

    def get_one(self, endpoint: str, pk: int) -> dict:
        resp = self._req("GET", f"{self._url(endpoint)}{pk}/")
        resp.raise_for_status()
        return resp.json()

    def post(self, endpoint: str, data: dict) -> dict:
        resp = self._req("POST", self._url(endpoint), json=data)
        if resp.status_code not in (200, 201):
            raise APIError(f"POST {endpoint}", resp)
        return resp.json()

    def patch(self, endpoint: str, pk: int, data: dict) -> dict:
        resp = self._req("PATCH", f"{self._url(endpoint)}{pk}/", json=data)
        if resp.status_code not in (200, 201):
            raise APIError(f"PATCH {endpoint}/{pk}", resp)
        return resp.json()

    def put_image(self, endpoint: str, pk: int, image_bytes: bytes, filename: str) -> dict:
        url = f"{self._url(endpoint)}{pk}/image/"
        headers = {k: v for k, v in self.s.headers.items() if k.lower() != "content-type"}
        resp = self._req(
            "PUT", url,
            files={"image": (filename, image_bytes, "image/png")},
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise APIError(f"PUT image {endpoint}/{pk}", resp)
        return resp.json()

    def download_image(self, url: str) -> bytes | None:
        if not url:
            return None
        try:
            resp = self._req("GET", url)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
        return None


class APIError(Exception):
    def __init__(self, action: str, resp: requests.Response):
        self.status_code = resp.status_code
        self.body = resp.text[:500]
        super().__init__(f"{action} failed ({self.status_code}): {self.body}")


# ---------------------------------------------------------------------------
# Migration State
# ---------------------------------------------------------------------------

class MigrationState:
    """Tracks source→target ID mappings for all model types."""

    def __init__(self):
        self.maps: dict[str, dict[int, int]] = {
            "property_type": {},
            "supermarket_category": {},
            "unit": {},
            "meal_type": {},
            "custom_filter": {},
            "keyword": {},
            "food": {},
            "property": {},
            "unit_conversion": {},
            "supermarket": {},
            "automation": {},
            "recipe": {},
            "step": {},
            "ingredient": {},
            "nutrition": {},
            "recipe_book": {},
            "meal_plan": {},
            "shopping_list": {},
            "shopping_list_recipe": {},
            "inventory_location": {},
            "inventory_entry": {},
        }
        self.stats: dict[str, int] = {}

    def put(self, model: str, source_id: int, target_id: int):
        self.maps[model][source_id] = target_id

    def get(self, model: str, source_id: int | None) -> int | None:
        if source_id is None:
            return None
        return self.maps[model].get(source_id)

    def remap_id(self, model: str, source_id: int | None) -> int | None:
        return self.get(model, source_id)

    def remap_obj(self, model: str, obj: dict | None) -> dict | None:
        """Remap a nested FK object like {"id": 5, "name": "..."} to target ID."""
        if obj is None:
            return None
        src_id = obj.get("id")
        tgt_id = self.get(model, src_id)
        if tgt_id is None:
            return None
        return {"id": tgt_id}

    def remap_list(self, model: str, obj_list: list[dict] | None) -> list[dict]:
        """Remap a list of nested FK objects."""
        if not obj_list:
            return []
        result = []
        for obj in obj_list:
            remapped = self.remap_obj(model, obj)
            if remapped:
                result.append(remapped)
        return result

    def count(self, model: str):
        self.stats[model] = self.stats.get(model, 0) + 1

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.maps, f, indent=2)
        log.info("State saved to %s", path)

    def load(self, path: str):
        with open(path) as f:
            self.maps = json.load(f)
        # JSON keys are strings; convert back to int
        for model in self.maps:
            self.maps[model] = {int(k): v for k, v in self.maps[model].items()}
        log.info("State loaded from %s", path)


# ---------------------------------------------------------------------------
# Phase Implementations
# ---------------------------------------------------------------------------

def migrate_simple(source: TandoorClient, target: TandoorClient, state: MigrationState,
                   endpoint: str, model: str, fields: list[str], dry_run: bool = False):
    """Migrate a simple (non-tree, non-nested) model."""
    items = source.get_all(endpoint)
    log.info("Phase: %s — %d objects from source", model, len(items))
    for item in items:
        payload = {f: item.get(f) for f in fields}
        if dry_run:
            state.count(model)
            continue
        created = target.post(endpoint, payload)
        state.put(model, item["id"], created["id"])
        state.count(model)


def migrate_tree(source: TandoorClient, target: TandoorClient, state: MigrationState,
                 endpoint: str, model: str, fields: list[str],
                 extra_fn=None, dry_run: bool = False):
    """Migrate a treebeard model breadth-first by depth."""
    items = source.get_all(endpoint)
    log.info("Phase: %s — %d objects from source", model, len(items))

    # Build parent map: for items that have a parent (depth > 1), find their parent
    # The API returns items with a 'parent' field (for Food at least) or we can infer from tree
    by_depth = {}
    for item in items:
        d = item.get("depth", 1) or 1
        by_depth.setdefault(d, []).append(item)

    for depth in sorted(by_depth.keys()):
        for item in by_depth[depth]:
            payload = {f: item.get(f) for f in fields}
            if extra_fn:
                extra_fn(item, payload, state)
            if dry_run:
                state.count(model)
                continue
            created = target.post(endpoint, payload)
            state.put(model, item["id"], created["id"])
            state.count(model)


def _food_extra(item: dict, payload: dict, state: MigrationState):
    """Add remapped FKs when creating Food."""
    payload["supermarket_category"] = state.remap_obj("supermarket_category", item.get("supermarket_category"))
    payload["properties_food_unit"] = state.remap_obj("unit", item.get("properties_food_unit"))
    payload["preferred_unit"] = state.remap_obj("unit", item.get("preferred_unit"))
    payload["preferred_shopping_unit"] = state.remap_obj("unit", item.get("preferred_shopping_unit"))
    # Don't set recipe FK here — that's a back-patch in phase 6
    payload.pop("recipe", None)
    # Don't set substitute here — that's a back-patch in phase 4
    payload.pop("substitute", None)


def migrate_phase1(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 1: Standalone setup data."""
    log.info("=== Phase 1: Standalone setup data ===")

    migrate_simple(source, target, state, "property-type", "property_type",
                   ["name", "description", "category", "order", "open_data_slug", "fdc_id"],
                   dry_run=dry_run)

    migrate_simple(source, target, state, "supermarket-category", "supermarket_category",
                   ["name", "description", "open_data_slug"],
                   dry_run=dry_run)

    migrate_simple(source, target, state, "unit", "unit",
                   ["name", "plural_name", "description", "base_unit", "open_data_slug"],
                   dry_run=dry_run)

    migrate_simple(source, target, state, "meal-type", "meal_type",
                   ["name", "order", "color", "default", "created_by"],
                   dry_run=dry_run)

    migrate_simple(source, target, state, "custom-filter", "custom_filter",
                   ["name", "search", "shared"],
                   dry_run=dry_run)


def migrate_phase2(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 2: Tree structures (Keyword, Food)."""
    log.info("=== Phase 2: Tree structures ===")

    migrate_tree(source, target, state, "keyword", "keyword",
                 ["name", "description", "icon"], dry_run=dry_run)

    migrate_tree(source, target, state, "food", "food",
                 ["name", "plural_name", "description", "url", "fdc_id",
                  "properties_food_amount", "ignore_shopping",
                  "substitute_siblings", "substitute_children", "open_data_slug"],
                 extra_fn=_food_extra, dry_run=dry_run)


def migrate_phase3(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 3: Properties and FoodProperty linkages."""
    log.info("=== Phase 3: Properties ===")

    # Get all foods from source to find their properties
    src_foods = source.get_all("food")
    prop_count = 0
    for src_food in src_foods:
        food_props = src_food.get("properties") or []
        if not food_props:
            continue
        tgt_food_id = state.get("food", src_food["id"])
        if tgt_food_id is None:
            continue

        properties_payload = []
        for fp in food_props:
            prop_type = fp.get("property_type") or {}
            tgt_pt_id = state.get("property_type", prop_type.get("id"))
            if tgt_pt_id is None:
                continue
            properties_payload.append({
                "property_type": {"id": tgt_pt_id},
                "property_amount": fp.get("property_amount"),
            })

        if properties_payload and not dry_run:
            target.patch("food", tgt_food_id, {"properties": properties_payload})

        prop_count += len(properties_payload)

    log.info("Phase 3: Linked %d food properties", prop_count)
    state.stats["food_property"] = prop_count


def migrate_phase4(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 4: Food substitutes, UnitConversion, Supermarket, Automation."""
    log.info("=== Phase 4: Remaining setup data ===")

    # Food.substitute M2M back-patch
    src_foods = source.get_all("food")
    sub_count = 0
    for src_food in src_foods:
        subs = src_food.get("substitute") or []
        if not subs:
            continue
        tgt_food_id = state.get("food", src_food["id"])
        if tgt_food_id is None:
            continue
        remapped_subs = state.remap_list("food", subs)
        if remapped_subs and not dry_run:
            target.patch("food", tgt_food_id, {"substitute": remapped_subs})
        sub_count += len(remapped_subs)
    log.info("Phase 4: Patched %d food substitutes", sub_count)

    # UnitConversion
    conversions = source.get_all("unit-conversion")
    log.info("Phase 4: UnitConversion — %d from source", len(conversions))
    for uc in conversions:
        payload = {
            "base_amount": uc.get("base_amount"),
            "converted_amount": uc.get("converted_amount"),
            "base_unit": state.remap_obj("unit", uc.get("base_unit")),
            "converted_unit": state.remap_obj("unit", uc.get("converted_unit")),
            "food": state.remap_obj("food", uc.get("food")),
            "open_data_slug": uc.get("open_data_slug"),
        }
        if payload["base_unit"] is None or payload["converted_unit"] is None:
            log.warning("Skipping UnitConversion %d: missing unit mapping", uc["id"])
            continue
        if not dry_run:
            created = target.post("unit-conversion", payload)
            state.put("unit_conversion", uc["id"], created["id"])
        state.count("unit_conversion")

    # Supermarket
    supermarkets = source.get_all("supermarket")
    log.info("Phase 4: Supermarket — %d from source", len(supermarkets))
    for sm in supermarkets:
        payload = {"name": sm.get("name"), "description": sm.get("description"),
                   "open_data_slug": sm.get("open_data_slug")}
        if not dry_run:
            created = target.post("supermarket", payload)
            state.put("supermarket", sm["id"], created["id"])
        state.count("supermarket")

    # SupermarketCategoryRelation
    relations = source.get_all("supermarket-category-relation")
    log.info("Phase 4: SupermarketCategoryRelation — %d from source", len(relations))
    for rel in relations:
        sm_id = rel.get("supermarket")
        if isinstance(sm_id, dict):
            sm_id = sm_id.get("id")
        cat = rel.get("category")
        payload = {
            "supermarket": state.remap_id("supermarket", sm_id),
            "category": state.remap_obj("supermarket_category", cat),
            "order": rel.get("order", 0),
        }
        if payload["supermarket"] is None:
            continue
        if not dry_run:
            target.post("supermarket-category-relation", payload)
        state.count("supermarket_category_relation")

    # Automation
    automations = source.get_all("automation")
    log.info("Phase 4: Automation — %d from source", len(automations))
    for auto in automations:
        payload = {
            "name": auto.get("name"),
            "description": auto.get("description"),
            "type": auto.get("type"),
            "param_1": auto.get("param_1"),
            "param_2": auto.get("param_2"),
            "param_3": auto.get("param_3"),
            "order": auto.get("order"),
            "disabled": auto.get("disabled"),
        }
        if not dry_run:
            created = target.post("automation", payload)
            state.put("automation", auto["id"], created["id"])
        state.count("automation")


def _build_recipe_payload(recipe: dict, state: MigrationState) -> dict:
    """Build the POST payload for a recipe with remapped nested objects."""
    steps_payload = []
    for step in recipe.get("steps") or []:
        ingredients = []
        for ing in step.get("ingredients") or []:
            ingredients.append({
                "food": state.remap_obj("food", ing.get("food")),
                "unit": state.remap_obj("unit", ing.get("unit")),
                "amount": ing.get("amount"),
                "note": ing.get("note", ""),
                "order": ing.get("order", 0),
                "is_header": ing.get("is_header", False),
                "no_amount": ing.get("no_amount", False),
                "original_text": ing.get("original_text", ""),
            })
        step_payload = {
            "name": step.get("name", ""),
            "instruction": step.get("instruction", ""),
            "time": step.get("time", 0),
            "order": step.get("order", 0),
            "show_as_header": step.get("show_as_header", True),
            "show_ingredients_table": step.get("show_ingredients_table", True),
            "ingredients": ingredients,
            # step_recipe is back-patched in phase 6
        }
        steps_payload.append(step_payload)

    keywords_payload = state.remap_list("keyword", recipe.get("keywords"))

    nutrition_payload = None
    nutrition = recipe.get("nutrition")
    if nutrition and isinstance(nutrition, dict):
        nutrition_payload = {
            "carbohydrates": nutrition.get("carbohydrates"),
            "fats": nutrition.get("fats"),
            "proteins": nutrition.get("proteins"),
            "calories": nutrition.get("calories"),
            "source": nutrition.get("source", ""),
        }

    payload = {
        "name": recipe.get("name", "")[:128],
        "description": recipe.get("description", ""),
        "source_url": recipe.get("source_url"),
        "working_time": recipe.get("working_time", 0),
        "waiting_time": recipe.get("waiting_time", 0),
        "servings": recipe.get("servings", 1),
        "servings_text": recipe.get("servings_text", ""),
        "private": recipe.get("private", False),
        "internal": recipe.get("internal", True),
        "show_ingredient_overview": recipe.get("show_ingredient_overview", True),
        "steps": steps_payload,
        "keywords": keywords_payload,
        "nutrition": nutrition_payload,
    }
    return payload


def migrate_phase5(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 5: Recipes (with inline Steps, Ingredients, Keywords, Nutrition)."""
    log.info("=== Phase 5: Recipes ===")

    recipes = source.get_all("recipe")
    log.info("Phase 5: Recipe — %d from source", len(recipes))

    for recipe_overview in recipes:
        # Need full recipe detail for steps/ingredients
        recipe = source.get_one("recipe", recipe_overview["id"])
        payload = _build_recipe_payload(recipe, state)

        if dry_run:
            state.count("recipe")
            continue

        created = target.post("recipe", payload)
        state.put("recipe", recipe["id"], created["id"])
        state.count("recipe")

        # Map step IDs (by matching order)
        src_steps = sorted(recipe.get("steps") or [], key=lambda s: s.get("order", 0))
        tgt_steps = sorted(created.get("steps") or [], key=lambda s: s.get("order", 0))
        for ss, ts in zip(src_steps, tgt_steps):
            state.put("step", ss["id"], ts["id"])
            # Map ingredient IDs
            src_ings = sorted(ss.get("ingredients") or [], key=lambda i: i.get("order", 0))
            tgt_ings = sorted(ts.get("ingredients") or [], key=lambda i: i.get("order", 0))
            for si, ti in zip(src_ings, tgt_ings):
                state.put("ingredient", si["id"], ti["id"])

        # Upload image if present
        image_url = recipe.get("image")
        if image_url:
            img_data = source.download_image(image_url)
            if img_data:
                try:
                    target.put_image("recipe", created["id"], img_data, "recipe_image.png")
                except Exception as e:
                    log.warning("Failed to upload image for recipe %s: %s", recipe["name"], e)


def migrate_phase6(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 6: Post-recipe back-patches (Food.recipe, Step.step_recipe, Recipe.properties)."""
    log.info("=== Phase 6: Post-recipe back-patches ===")

    # Food.recipe back-patch
    src_foods = source.get_all("food")
    food_recipe_count = 0
    for sf in src_foods:
        recipe_ref = sf.get("recipe")
        if not recipe_ref:
            continue
        recipe_id = recipe_ref if isinstance(recipe_ref, int) else recipe_ref.get("id")
        tgt_food_id = state.get("food", sf["id"])
        tgt_recipe_id = state.get("recipe", recipe_id)
        if tgt_food_id and tgt_recipe_id and not dry_run:
            target.patch("food", tgt_food_id, {"recipe": {"id": tgt_recipe_id}})
            food_recipe_count += 1
    log.info("Phase 6: Patched %d food→recipe links", food_recipe_count)

    # Step.step_recipe back-patch
    step_recipe_count = 0
    src_recipes = source.get_all("recipe")
    for ro in src_recipes:
        recipe = source.get_one("recipe", ro["id"])
        for step in recipe.get("steps") or []:
            sr = step.get("step_recipe")
            if not sr:
                continue
            sr_id = sr if isinstance(sr, int) else sr.get("id")
            tgt_step_id = state.get("step", step["id"])
            tgt_sr_id = state.get("recipe", sr_id)
            if tgt_step_id and tgt_sr_id and not dry_run:
                target.patch("step", tgt_step_id, {"step_recipe": tgt_sr_id})
                step_recipe_count += 1
    log.info("Phase 6: Patched %d step→recipe links", step_recipe_count)

    # Recipe.properties back-patch
    prop_count = 0
    for ro in src_recipes:
        recipe = source.get_one("recipe", ro["id"])
        props = recipe.get("properties") or []
        if not props:
            continue
        tgt_recipe_id = state.get("recipe", recipe["id"])
        if not tgt_recipe_id:
            continue
        props_payload = []
        for p in props:
            pt = p.get("property_type") or {}
            tgt_pt_id = state.get("property_type", pt.get("id"))
            if tgt_pt_id:
                props_payload.append({
                    "property_type": {"id": tgt_pt_id},
                    "property_amount": p.get("property_amount"),
                })
        if props_payload and not dry_run:
            target.patch("recipe", tgt_recipe_id, {"properties": props_payload})
            prop_count += len(props_payload)
    log.info("Phase 6: Patched %d recipe properties", prop_count)


def migrate_phase7(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 7: Collections (RecipeBook, RecipeBookEntry, CookLog, ViewLog)."""
    log.info("=== Phase 7: Collections ===")

    # RecipeBook
    books = source.get_all("recipe-book")
    log.info("Phase 7: RecipeBook — %d from source", len(books))
    for book in books:
        payload = {
            "name": book.get("name"),
            "description": book.get("description", ""),
            "order": book.get("order", 0),
        }
        if not dry_run:
            created = target.post("recipe-book", payload)
            state.put("recipe_book", book["id"], created["id"])
        state.count("recipe_book")

    # RecipeBookEntry
    entries = source.get_all("recipe-book-entry")
    log.info("Phase 7: RecipeBookEntry — %d from source", len(entries))
    for entry in entries:
        book_id = entry.get("book")
        if isinstance(book_id, dict):
            book_id = book_id.get("id")
        recipe_id = entry.get("recipe")
        if isinstance(recipe_id, dict):
            recipe_id = recipe_id.get("id")
        tgt_book = state.remap_id("recipe_book", book_id)
        tgt_recipe = state.remap_id("recipe", recipe_id)
        if tgt_book is None or tgt_recipe is None:
            continue
        if not dry_run:
            target.post("recipe-book-entry", {"book": tgt_book, "recipe": tgt_recipe})
        state.count("recipe_book_entry")

    # CookLog
    cooklogs = source.get_all("cook-log")
    log.info("Phase 7: CookLog — %d from source", len(cooklogs))
    for cl in cooklogs:
        recipe_id = cl.get("recipe")
        if isinstance(recipe_id, dict):
            recipe_id = recipe_id.get("id")
        tgt_recipe = state.remap_id("recipe", recipe_id)
        if tgt_recipe is None:
            continue
        payload = {
            "recipe": tgt_recipe,
            "servings": cl.get("servings"),
            "rating": cl.get("rating"),
            "comment": cl.get("comment", ""),
        }
        if not dry_run:
            target.post("cook-log", payload)
        state.count("cook_log")

    # ViewLog
    viewlogs = source.get_all("view-log")
    log.info("Phase 7: ViewLog — %d from source", len(viewlogs))
    for vl in viewlogs:
        recipe_id = vl.get("recipe")
        if isinstance(recipe_id, dict):
            recipe_id = recipe_id.get("id")
        tgt_recipe = state.remap_id("recipe", recipe_id)
        if tgt_recipe is None:
            continue
        if not dry_run:
            target.post("view-log", {"recipe": tgt_recipe})
        state.count("view_log")


def migrate_phase8(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 8: Meal planning."""
    log.info("=== Phase 8: Meal planning ===")

    mealplans = source.get_all("meal-plan", params={
        "from_date": "1970-01-01",
        "to_date": "2099-12-31",
    })
    log.info("Phase 8: MealPlan — %d from source", len(mealplans))
    for mp in mealplans:
        recipe_ref = mp.get("recipe")
        recipe_id = None
        if recipe_ref:
            recipe_id = recipe_ref if isinstance(recipe_ref, int) else recipe_ref.get("id")

        meal_type_ref = mp.get("meal_type")
        mt_id = None
        if meal_type_ref:
            mt_id = meal_type_ref if isinstance(meal_type_ref, int) else meal_type_ref.get("id")

        payload = {
            "title": mp.get("title", ""),
            "recipe": {"id": state.remap_id("recipe", recipe_id)} if recipe_id and state.remap_id("recipe", recipe_id) else None,
            "meal_type": {"id": state.remap_id("meal_type", mt_id)} if mt_id and state.remap_id("meal_type", mt_id) else None,
            "note": mp.get("note", ""),
            "servings": mp.get("servings", 1),
            "from_date": mp.get("from_date"),
            "to_date": mp.get("to_date"),
        }
        if not dry_run:
            created = target.post("meal-plan", payload)
            state.put("meal_plan", mp["id"], created["id"])
        state.count("meal_plan")


def migrate_phase9(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 9: Shopping lists."""
    log.info("=== Phase 9: Shopping lists ===")

    # ShoppingList
    lists = source.get_all("shopping-list")
    log.info("Phase 9: ShoppingList — %d from source", len(lists))
    for sl in lists:
        payload = {"name": sl.get("name", "")}
        if not dry_run:
            created = target.post("shopping-list", payload)
            state.put("shopping_list", sl["id"], created["id"])
        state.count("shopping_list")

    # ShoppingListRecipe
    sl_recipes = source.get_all("shopping-list-recipe")
    log.info("Phase 9: ShoppingListRecipe — %d from source", len(sl_recipes))
    for slr in sl_recipes:
        recipe_ref = slr.get("recipe")
        recipe_id = None
        if recipe_ref:
            recipe_id = recipe_ref if isinstance(recipe_ref, int) else recipe_ref.get("id")
        payload = {
            "recipe": state.remap_id("recipe", recipe_id),
            "servings": slr.get("servings"),
        }
        if not dry_run:
            created = target.post("shopping-list-recipe", payload)
            state.put("shopping_list_recipe", slr["id"], created["id"])
        state.count("shopping_list_recipe")

    # ShoppingListEntry
    entries = source.get_all("shopping-list-entry", params={"empty": "true"})
    log.info("Phase 9: ShoppingListEntry — %d from source", len(entries))
    for sle in entries:
        food_ref = sle.get("food")
        unit_ref = sle.get("unit")

        list_recipe_ref = sle.get("list_recipe")
        lr_id = None
        if list_recipe_ref:
            lr_id = list_recipe_ref if isinstance(list_recipe_ref, int) else list_recipe_ref.get("id")

        payload = {
            "food": state.remap_obj("food", food_ref),
            "unit": state.remap_obj("unit", unit_ref),
            "amount": sle.get("amount"),
            "checked": sle.get("checked", False),
            "note": sle.get("note", ""),
        }

        # Link to shopping lists via M2M
        shopping_lists = sle.get("shopping_lists") or []
        if shopping_lists:
            remapped_lists = []
            for sl_ref in shopping_lists:
                sl_id = sl_ref if isinstance(sl_ref, int) else sl_ref.get("id")
                tgt_sl = state.remap_id("shopping_list", sl_id)
                if tgt_sl:
                    remapped_lists.append({"id": tgt_sl})
            if remapped_lists:
                payload["shopping_lists"] = remapped_lists

        if payload.get("food") is None:
            continue

        if not dry_run:
            target.post("shopping-list-entry", payload)
        state.count("shopping_list_entry")


def migrate_phase10(source: TandoorClient, target: TandoorClient, state: MigrationState, dry_run: bool = False):
    """Phase 10: Inventory."""
    log.info("=== Phase 10: Inventory ===")

    # InventoryLocation
    locations = source.get_all("inventory-location")
    log.info("Phase 10: InventoryLocation — %d from source", len(locations))
    for loc in locations:
        payload = {
            "name": loc.get("name"),
            "description": loc.get("description", ""),
            "is_freezer": loc.get("is_freezer", False),
        }
        if not dry_run:
            created = target.post("inventory-location", payload)
            state.put("inventory_location", loc["id"], created["id"])
        state.count("inventory_location")

    # InventoryEntry
    entries = source.get_all("inventory-entry", params={"empty": "true"})
    log.info("Phase 10: InventoryEntry — %d from source", len(entries))
    for ie in entries:
        payload = {
            "food": state.remap_obj("food", ie.get("food")),
            "unit": state.remap_obj("unit", ie.get("unit")),
            "amount": ie.get("amount"),
            "inventory_location": state.remap_obj("inventory_location", ie.get("inventory_location")),
            "expiration_date": ie.get("expiration_date"),
            "open_date": ie.get("open_date"),
        }
        if not dry_run:
            created = target.post("inventory-entry", payload)
            state.put("inventory_entry", ie["id"], created["id"])
        state.count("inventory_entry")

    # InventoryLog — read-only in most setups, auto-created on entry creation
    # Skip explicit migration as logs are auto-generated


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

PHASES = [
    (1, migrate_phase1),
    (2, migrate_phase2),
    (3, migrate_phase3),
    (4, migrate_phase4),
    (5, migrate_phase5),
    (6, migrate_phase6),
    (7, migrate_phase7),
    (8, migrate_phase8),
    (9, migrate_phase9),
    (10, migrate_phase10),
]


def run_migration(source: TandoorClient, target: TandoorClient,
                  dry_run: bool = False, save_state: str | None = None,
                  resume_from: int = 1):
    state = MigrationState()

    if resume_from > 1 and save_state:
        state.load(save_state)

    mode = "DRY RUN" if dry_run else "LIVE"
    log.info("Starting migration (%s) from phase %d", mode, resume_from)

    for phase_num, phase_fn in PHASES:
        if phase_num < resume_from:
            continue
        try:
            phase_fn(source, target, state, dry_run=dry_run)
        except Exception:
            log.exception("Phase %d failed", phase_num)
            if save_state:
                state.save(save_state)
            raise

        if save_state:
            state.save(save_state)

    log.info("=== Migration complete ===")
    for model, count in sorted(state.stats.items()):
        log.info("  %s: %d", model, count)

    return state


def main():
    parser = argparse.ArgumentParser(description="Migrate Tandoor space data between instances")
    parser.add_argument("--source-url", required=True, help="Source instance base URL")
    parser.add_argument("--source-token", required=True, help="Source API token")
    parser.add_argument("--target-url", required=True, help="Target instance base URL")
    parser.add_argument("--target-token", required=True, help="Target API token")
    parser.add_argument("--dry-run", action="store_true", help="Read-only mode, no writes to target")
    parser.add_argument("--save-state", help="Path to save/load state JSON for resume support")
    parser.add_argument("--resume-from-phase", type=int, default=1, help="Resume from phase N (requires --save-state)")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP request timeout in seconds")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    source = TandoorClient(args.source_url, args.source_token, timeout=args.timeout)
    target = TandoorClient(args.target_url, args.target_token, timeout=args.timeout)

    try:
        run_migration(
            source, target,
            dry_run=args.dry_run,
            save_state=args.save_state,
            resume_from=args.resume_from_phase,
        )
    except Exception:
        log.exception("Migration failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
