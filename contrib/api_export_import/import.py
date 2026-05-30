#!/usr/bin/env python3
"""
Tandoor Recipes: Space Data Import

Reads a local export directory (produced by export.py) and pushes all data
to a target Tandoor instance via REST API.

Subcommands:
    validate  — Check export directory structure and internal consistency
    dry-run   — Connect to target, report what would be created (no writes)
    run       — Live import to target instance

Usage:
    python import.py validate ./my_export
    python import.py dry-run ./my_export --target-url http://target:8080 --target-token tda_yyy
    python import.py run ./my_export --target-url http://target:8080 --target-token tda_yyy
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Allow running as script from any directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from contrib.api_export_import.client import APIError, TandoorClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("import")


# ---------------------------------------------------------------------------
# Export Data Loader
# ---------------------------------------------------------------------------

EXPECTED_FILES = [
    "manifest", "property_type", "supermarket_category", "unit", "meal_type",
    "custom_filter", "keyword", "food", "unit_conversion", "supermarket",
    "supermarket_category_relation", "automation", "recipe", "recipe_book",
    "recipe_book_entry", "cook_log", "view_log", "meal_plan", "shopping_list",
    "shopping_list_recipe", "shopping_list_entry", "inventory_location",
    "inventory_entry",
]


class ExportData:
    """Loads and holds all data from an export directory."""

    def __init__(self, export_dir: str):
        self.dir = export_dir
        self.manifest: dict = {}
        self.data: dict[str, list[dict]] = {}

    def load(self):
        self.manifest = self._load_json("manifest")
        for name in EXPECTED_FILES:
            if name == "manifest":
                continue
            self.data[name] = self._load_json(name)

    def _load_json(self, name: str) -> list[dict] | dict:
        path = os.path.join(self.dir, f"{name}.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def get(self, model: str) -> list[dict]:
        return self.data.get(model, [])

    def get_image_path(self, recipe_id: int) -> str | None:
        path = os.path.join(self.dir, "images", f"recipe_{recipe_id}.png")
        if os.path.exists(path):
            return path
        return None


# ---------------------------------------------------------------------------
# Migration State (source ID → target ID mapping)
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
        """Remap a nested FK object, preserving 'name' for Tandoor serializers."""
        if obj is None:
            return None
        src_id = obj.get("id")
        tgt_id = self.get(model, src_id)
        if tgt_id is None:
            return None
        remapped = {"id": tgt_id}
        if "name" in obj:
            remapped["name"] = obj["name"]
        return remapped

    def remap_list(self, model: str, obj_list: list[dict] | None) -> list[dict]:
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
        for model in self.maps:
            self.maps[model] = {int(k): v for k, v in self.maps[model].items()}
        log.info("State loaded from %s", path)


# ---------------------------------------------------------------------------
# Validate subcommand
# ---------------------------------------------------------------------------

def cmd_validate(export_dir: str) -> bool:
    """Validate export directory structure and internal consistency."""
    errors = []
    warnings = []

    # Check manifest
    manifest_path = os.path.join(export_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        errors.append("manifest.json not found")
        print("VALIDATION FAILED: manifest.json missing")
        return False

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"manifest.json is invalid: {e}")
        print(f"VALIDATION FAILED: {errors[0]}")
        return False

    if manifest.get("version") != 1:
        errors.append(f"Unsupported manifest version: {manifest.get('version')}")

    # Check all expected JSON files
    for name in EXPECTED_FILES:
        if name == "manifest":
            continue
        path = os.path.join(export_dir, f"{name}.json")
        if not os.path.exists(path):
            errors.append(f"{name}.json not found")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                errors.append(f"{name}.json is not a JSON array")
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"{name}.json is invalid: {e}")

    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return False

    # Load all data for consistency checks
    export = ExportData(export_dir)
    export.load()

    # Build ID sets for FK validation
    id_sets = {}
    for model in EXPECTED_FILES:
        if model == "manifest":
            continue
        items = export.get(model)
        id_sets[model] = {item["id"] for item in items if "id" in item}

    # Check recipe ingredients reference existing foods and units
    for recipe in export.get("recipe"):
        for step in recipe.get("steps") or []:
            for ing in step.get("ingredients") or []:
                food = ing.get("food")
                if food and isinstance(food, dict) and food.get("id") not in id_sets.get("food", set()):
                    warnings.append(f"Recipe '{recipe.get('name')}': ingredient references food ID {food.get('id')} not in export")
                unit = ing.get("unit")
                if unit and isinstance(unit, dict) and unit.get("id") not in id_sets.get("unit", set()):
                    warnings.append(f"Recipe '{recipe.get('name')}': ingredient references unit ID {unit.get('id')} not in export")

    # Check recipe images exist on disk
    for recipe in export.get("recipe"):
        if recipe.get("image"):
            img_path = export.get_image_path(recipe["id"])
            if img_path is None:
                warnings.append(f"Recipe '{recipe.get('name')}' has image URL but no file in images/")

    # Report
    counts = manifest.get("counts", {})
    print("=== Export Validation Report ===")
    print(f"  Source: {manifest.get('source_url', 'unknown')}")
    print(f"  Exported at: {manifest.get('exported_at', 'unknown')}")
    print(f"  Version: {manifest.get('version', 'unknown')}")
    print()
    for model, count in sorted(counts.items()):
        print(f"  {model}: {count}")
    print()

    if warnings:
        print(f"Warnings ({len(warnings)}):")
        for w in warnings[:20]:
            print(f"  - {w}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more")
        print()

    print("VALIDATION PASSED")
    return True


# ---------------------------------------------------------------------------
# Import Phase Implementations
# ---------------------------------------------------------------------------

def import_simple(target: TandoorClient, state: MigrationState,
                  items: list[dict], endpoint: str, model: str,
                  fields: list[str], dry_run: bool = False):
    """Import a simple (non-tree, non-nested) model."""
    log.info("Phase: %s — %d objects", model, len(items))
    for item in items:
        payload = {f: item.get(f) for f in fields}
        if dry_run:
            state.count(model)
            continue
        created = target.post(endpoint, payload)
        state.put(model, item["id"], created["id"])
        state.count(model)


def import_tree(target: TandoorClient, state: MigrationState,
                items: list[dict], endpoint: str, model: str,
                fields: list[str], extra_fn=None, dry_run: bool = False):
    """Import a treebeard model breadth-first by depth."""
    log.info("Phase: %s — %d objects", model, len(items))
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
    payload.pop("recipe", None)
    payload.pop("substitute", None)


def import_phase1(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 1: Standalone setup data."""
    log.info("=== Phase 1: Standalone setup data ===")

    import_simple(target, state, data.get("property_type"), "property-type", "property_type",
                  ["name", "description", "category", "order", "open_data_slug", "fdc_id"], dry_run=dry_run)

    import_simple(target, state, data.get("supermarket_category"), "supermarket-category", "supermarket_category",
                  ["name", "description", "open_data_slug"], dry_run=dry_run)

    import_simple(target, state, data.get("unit"), "unit", "unit",
                  ["name", "plural_name", "description", "base_unit", "open_data_slug"], dry_run=dry_run)

    import_simple(target, state, data.get("meal_type"), "meal-type", "meal_type",
                  ["name", "order", "color", "default", "created_by"], dry_run=dry_run)

    import_simple(target, state, data.get("custom_filter"), "custom-filter", "custom_filter",
                  ["name", "search", "shared"], dry_run=dry_run)


def import_phase2(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 2: Tree structures (Keyword, Food)."""
    log.info("=== Phase 2: Tree structures ===")

    import_tree(target, state, data.get("keyword"), "keyword", "keyword",
                ["name", "description", "icon"], dry_run=dry_run)

    import_tree(target, state, data.get("food"), "food", "food",
                ["name", "plural_name", "description", "url", "fdc_id",
                 "properties_food_amount", "ignore_shopping",
                 "substitute_siblings", "substitute_children", "open_data_slug"],
                extra_fn=_food_extra, dry_run=dry_run)


def import_phase3(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 3: Properties and FoodProperty linkages."""
    log.info("=== Phase 3: Properties ===")

    src_foods = data.get("food")
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
                "property_type": {"id": tgt_pt_id, "name": prop_type.get("name", "")},
                "property_amount": fp.get("property_amount"),
            })

        if properties_payload and not dry_run:
            target.patch("food", tgt_food_id, {"properties": properties_payload})

        prop_count += len(properties_payload)

    log.info("Phase 3: Linked %d food properties", prop_count)
    state.stats["food_property"] = prop_count


def import_phase4(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 4: Food substitutes, UnitConversion, Supermarket, Automation."""
    log.info("=== Phase 4: Remaining setup data ===")

    # Food.substitute M2M back-patch
    src_foods = data.get("food")
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
    conversions = data.get("unit_conversion")
    log.info("Phase 4: UnitConversion — %d from export", len(conversions))
    for uc in conversions:
        food_remapped = state.remap_obj("food", uc.get("food"))
        payload = {
            "base_amount": uc.get("base_amount"),
            "converted_amount": uc.get("converted_amount"),
            "base_unit": state.remap_obj("unit", uc.get("base_unit")),
            "converted_unit": state.remap_obj("unit", uc.get("converted_unit")),
            "open_data_slug": uc.get("open_data_slug"),
        }
        if food_remapped is not None:
            payload["food"] = food_remapped
        if payload["base_unit"] is None or payload["converted_unit"] is None:
            log.warning("Skipping UnitConversion %d: missing unit mapping", uc["id"])
            continue
        if not dry_run:
            created = target.post("unit-conversion", payload)
            state.put("unit_conversion", uc["id"], created["id"])
        state.count("unit_conversion")

    # Supermarket
    supermarkets = data.get("supermarket")
    log.info("Phase 4: Supermarket — %d from export", len(supermarkets))
    for sm in supermarkets:
        payload = {"name": sm.get("name"), "description": sm.get("description"),
                   "open_data_slug": sm.get("open_data_slug")}
        if not dry_run:
            created = target.post("supermarket", payload)
            state.put("supermarket", sm["id"], created["id"])
        state.count("supermarket")

    # SupermarketCategoryRelation
    relations = data.get("supermarket_category_relation")
    log.info("Phase 4: SupermarketCategoryRelation — %d from export", len(relations))
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
    automations = data.get("automation")
    log.info("Phase 4: Automation — %d from export", len(automations))
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


def import_phase5(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 5: Recipes (with inline Steps, Ingredients, Keywords, Nutrition)."""
    log.info("=== Phase 5: Recipes ===")

    recipes = data.get("recipe")
    log.info("Phase 5: Recipe — %d from export", len(recipes))

    for recipe in recipes:
        # Build payload with remapped IDs
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
            src_ings = sorted(ss.get("ingredients") or [], key=lambda i: i.get("order", 0))
            tgt_ings = sorted(ts.get("ingredients") or [], key=lambda i: i.get("order", 0))
            for si, ti in zip(src_ings, tgt_ings):
                state.put("ingredient", si["id"], ti["id"])

        # Upload image from export directory
        img_path = data.get_image_path(recipe["id"])
        if img_path:
            try:
                with open(img_path, "rb") as f:
                    img_data = f.read()
                target.put_image("recipe", created["id"], img_data, "recipe_image.png")
            except Exception as e:
                log.warning("Failed to upload image for recipe %s: %s", recipe["name"], e)


def import_phase6(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 6: Post-recipe back-patches (Food.recipe, Step.step_recipe, Recipe.properties)."""
    log.info("=== Phase 6: Post-recipe back-patches ===")

    # Food.recipe back-patch
    src_foods = data.get("food")
    food_recipe_count = 0
    for sf in src_foods:
        recipe_ref = sf.get("recipe")
        if not recipe_ref:
            continue
        recipe_id = recipe_ref if isinstance(recipe_ref, int) else recipe_ref.get("id")
        tgt_food_id = state.get("food", sf["id"])
        tgt_recipe_id = state.get("recipe", recipe_id)
        if tgt_food_id and tgt_recipe_id and not dry_run:
            recipe_name = recipe_ref.get("name", "") if isinstance(recipe_ref, dict) else ""
            target.patch("food", tgt_food_id, {"recipe": {"id": tgt_recipe_id, "name": recipe_name}})
            food_recipe_count += 1
    log.info("Phase 6: Patched %d food→recipe links", food_recipe_count)

    # Step.step_recipe back-patch (via recipe PUT to avoid Step API bug)
    step_recipe_count = 0
    for recipe in data.get("recipe"):
        has_step_recipe = any(step.get("step_recipe") for step in recipe.get("steps") or [])
        if not has_step_recipe:
            continue

        tgt_recipe_id = state.get("recipe", recipe["id"])
        if not tgt_recipe_id or dry_run:
            continue
        tgt_recipe = target.get_one("recipe", tgt_recipe_id)
        tgt_steps = sorted(tgt_recipe.get("steps") or [], key=lambda s: s.get("order", 0))
        src_steps = sorted(recipe.get("steps") or [], key=lambda s: s.get("order", 0))
        updated = False
        for ss, ts in zip(src_steps, tgt_steps):
            sr = ss.get("step_recipe")
            if not sr:
                continue
            sr_id = sr if isinstance(sr, int) else sr.get("id")
            tgt_sr_id = state.get("recipe", sr_id)
            if tgt_sr_id:
                ts["step_recipe"] = tgt_sr_id
                updated = True
                step_recipe_count += 1
        if updated:
            target.put("recipe", tgt_recipe_id, tgt_recipe)
    log.info("Phase 6: Patched %d step→recipe links", step_recipe_count)

    # Recipe.properties back-patch
    prop_count = 0
    for recipe in data.get("recipe"):
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
                    "property_type": {"id": tgt_pt_id, "name": pt.get("name", "")},
                    "property_amount": p.get("property_amount"),
                })
        if props_payload and not dry_run:
            target.patch("recipe", tgt_recipe_id, {"properties": props_payload})
            prop_count += len(props_payload)
    log.info("Phase 6: Patched %d recipe properties", prop_count)


def import_phase7(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 7: Collections (RecipeBook, RecipeBookEntry, CookLog, ViewLog)."""
    log.info("=== Phase 7: Collections ===")

    # RecipeBook
    books = data.get("recipe_book")
    log.info("Phase 7: RecipeBook — %d from export", len(books))
    for book in books:
        payload = {
            "name": book.get("name"),
            "description": book.get("description", ""),
            "order": book.get("order", 0),
            "shared": [],
        }
        if not dry_run:
            created = target.post("recipe-book", payload)
            state.put("recipe_book", book["id"], created["id"])
        state.count("recipe_book")

    # RecipeBookEntry
    entries = data.get("recipe_book_entry")
    log.info("Phase 7: RecipeBookEntry — %d from export", len(entries))
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
    cooklogs = data.get("cook_log")
    log.info("Phase 7: CookLog — %d from export", len(cooklogs))
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
    viewlogs = data.get("view_log")
    log.info("Phase 7: ViewLog — %d from export", len(viewlogs))
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


def import_phase8(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 8: Meal planning."""
    log.info("=== Phase 8: Meal planning ===")

    mealplans = data.get("meal_plan")
    log.info("Phase 8: MealPlan — %d from export", len(mealplans))
    for mp in mealplans:
        recipe_ref = mp.get("recipe")
        recipe_id = None
        if recipe_ref:
            recipe_id = recipe_ref if isinstance(recipe_ref, int) else recipe_ref.get("id")

        meal_type_ref = mp.get("meal_type")
        mt_id = None
        if meal_type_ref:
            mt_id = meal_type_ref if isinstance(meal_type_ref, int) else meal_type_ref.get("id")

        recipe_name = recipe_ref.get("name", "") if isinstance(recipe_ref, dict) else ""
        mt_name = meal_type_ref.get("name", "") if isinstance(meal_type_ref, dict) else ""

        tgt_recipe_id = state.remap_id("recipe", recipe_id) if recipe_id else None
        tgt_mt_id = state.remap_id("meal_type", mt_id) if mt_id else None

        payload = {
            "title": mp.get("title", ""),
            "recipe": {"id": tgt_recipe_id, "name": recipe_name} if tgt_recipe_id else None,
            "meal_type": {"id": tgt_mt_id, "name": mt_name} if tgt_mt_id else None,
            "note": mp.get("note", ""),
            "servings": mp.get("servings", 1),
            "from_date": mp.get("from_date"),
            "to_date": mp.get("to_date"),
        }
        if not dry_run:
            created = target.post("meal-plan", payload)
            state.put("meal_plan", mp["id"], created["id"])
        state.count("meal_plan")


def import_phase9(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 9: Shopping lists."""
    log.info("=== Phase 9: Shopping lists ===")

    lists = data.get("shopping_list")
    log.info("Phase 9: ShoppingList — %d from export", len(lists))
    for sl in lists:
        payload = {"name": sl.get("name", "")}
        if not dry_run:
            created = target.post("shopping-list", payload)
            state.put("shopping_list", sl["id"], created["id"])
        state.count("shopping_list")

    # ShoppingListRecipe
    sl_recipes = data.get("shopping_list_recipe")
    log.info("Phase 9: ShoppingListRecipe — %d from export", len(sl_recipes))
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
    entries = data.get("shopping_list_entry")
    log.info("Phase 9: ShoppingListEntry — %d from export", len(entries))
    for sle in entries:
        food_ref = sle.get("food")
        unit_ref = sle.get("unit")

        payload = {
            "food": state.remap_obj("food", food_ref),
            "unit": state.remap_obj("unit", unit_ref),
            "amount": sle.get("amount"),
            "checked": sle.get("checked", False),
            "note": sle.get("note", ""),
        }

        shopping_lists = sle.get("shopping_lists") or []
        if shopping_lists:
            remapped_lists = []
            for sl_ref in shopping_lists:
                sl_id = sl_ref if isinstance(sl_ref, int) else sl_ref.get("id")
                tgt_sl = state.remap_id("shopping_list", sl_id)
                if tgt_sl:
                    sl_name = sl_ref.get("name", "") if isinstance(sl_ref, dict) else ""
                    remapped_lists.append({"id": tgt_sl, "name": sl_name})
            if remapped_lists:
                payload["shopping_lists"] = remapped_lists

        if payload.get("food") is None:
            continue

        if not dry_run:
            target.post("shopping-list-entry", payload)
        state.count("shopping_list_entry")


def import_phase10(target: TandoorClient, state: MigrationState, data: ExportData, dry_run: bool = False):
    """Phase 10: Inventory."""
    log.info("=== Phase 10: Inventory ===")

    # InventoryLocation
    locations = data.get("inventory_location")
    log.info("Phase 10: InventoryLocation — %d from export", len(locations))
    for loc in locations:
        household_ref = loc.get("household")
        hh_name = household_ref.get("name", "Default Household") if isinstance(household_ref, dict) else "Default Household"
        payload = {
            "name": loc.get("name"),
            "description": loc.get("description", ""),
            "is_freezer": loc.get("is_freezer", False),
            "household": {"name": hh_name},
        }
        if not dry_run:
            created = target.post("inventory-location", payload)
            state.put("inventory_location", loc["id"], created["id"])
        state.count("inventory_location")

    # InventoryEntry
    entries = data.get("inventory_entry")
    log.info("Phase 10: InventoryEntry — %d from export", len(entries))
    for ie in entries:
        loc_ref = ie.get("inventory_location")
        loc_remapped = state.remap_obj("inventory_location", loc_ref)
        if loc_remapped is not None:
            src_hh = loc_ref.get("household") if isinstance(loc_ref, dict) else None
            hh_name = src_hh.get("name", "Default Household") if isinstance(src_hh, dict) else "Default Household"
            loc_remapped["household"] = {"name": hh_name}

        payload = {
            "food": state.remap_obj("food", ie.get("food")),
            "unit": state.remap_obj("unit", ie.get("unit")),
            "amount": ie.get("amount"),
            "inventory_location": loc_remapped,
            "expiration_date": ie.get("expiration_date"),
            "open_date": ie.get("open_date"),
        }
        if not dry_run:
            created = target.post("inventory-entry", payload)
            state.put("inventory_entry", ie["id"], created["id"])
        state.count("inventory_entry")


# ---------------------------------------------------------------------------
# Import orchestrator
# ---------------------------------------------------------------------------

PHASES = [
    (1, import_phase1),
    (2, import_phase2),
    (3, import_phase3),
    (4, import_phase4),
    (5, import_phase5),
    (6, import_phase6),
    (7, import_phase7),
    (8, import_phase8),
    (9, import_phase9),
    (10, import_phase10),
]


def run_import(target: TandoorClient, data: ExportData,
               dry_run: bool = False, save_state: str | None = None,
               resume_from: int = 1):
    state = MigrationState()

    if resume_from > 1 and save_state:
        state.load(save_state)

    mode = "DRY RUN" if dry_run else "LIVE"
    log.info("Starting import (%s) from phase %d", mode, resume_from)

    for phase_num, phase_fn in PHASES:
        if phase_num < resume_from:
            continue
        try:
            phase_fn(target, state, data, dry_run=dry_run)
        except Exception:
            log.exception("Phase %d failed", phase_num)
            if save_state:
                state.save(save_state)
            raise

        if save_state:
            state.save(save_state)

    log.info("=== Import complete ===")
    for model, count in sorted(state.stats.items()):
        log.info("  %s: %d", model, count)

    return state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import Tandoor space data from export directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    p_validate = subparsers.add_parser("validate", help="Check export directory structure and consistency")
    p_validate.add_argument("export_dir", help="Path to export directory")

    # dry-run
    p_dryrun = subparsers.add_parser("dry-run", help="Preview import without writing to target")
    p_dryrun.add_argument("export_dir", help="Path to export directory")
    p_dryrun.add_argument("--target-url", required=True, help="Target instance base URL")
    p_dryrun.add_argument("--target-token", required=True, help="Target API token")
    p_dryrun.add_argument("--timeout", type=int, default=60, help="HTTP request timeout")
    p_dryrun.add_argument("-v", "--verbose", action="store_true")

    # run
    p_run = subparsers.add_parser("run", help="Import data to target instance")
    p_run.add_argument("export_dir", help="Path to export directory")
    p_run.add_argument("--target-url", required=True, help="Target instance base URL")
    p_run.add_argument("--target-token", required=True, help="Target API token")
    p_run.add_argument("--save-state", help="Path to save/load state JSON for resume")
    p_run.add_argument("--resume-from-phase", type=int, default=1, help="Resume from phase N")
    p_run.add_argument("--timeout", type=int, default=60, help="HTTP request timeout")
    p_run.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if hasattr(args, "verbose") and args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.command == "validate":
        ok = cmd_validate(args.export_dir)
        sys.exit(0 if ok else 1)

    # Load export data
    data = ExportData(args.export_dir)
    data.load()

    target = TandoorClient(args.target_url, args.target_token,
                           timeout=getattr(args, "timeout", 60))

    try:
        if args.command == "dry-run":
            run_import(target, data, dry_run=True)
        elif args.command == "run":
            run_import(target, data,
                       save_state=args.save_state,
                       resume_from=args.resume_from_phase)
    except Exception:
        log.exception("Import failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
