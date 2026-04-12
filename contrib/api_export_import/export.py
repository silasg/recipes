#!/usr/bin/env python3
"""
Tandoor Recipes: Space Data Export

Extracts all space data from a Tandoor instance via REST API and saves it
to a local directory of JSON files + images.

Usage:
    python export.py \
        --source-url http://source:8080 --source-token tda_xxx \
        --output ./my_export
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

# Allow running as script from any directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from contrib.api_export_import.client import TandoorClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("export")


# Models to export via simple get_all(), in dependency order.
# (endpoint, filename, description)
SIMPLE_MODELS = [
    ("property-type", "property_type", "PropertyType"),
    ("supermarket-category", "supermarket_category", "SupermarketCategory"),
    ("unit", "unit", "Unit"),
    ("meal-type", "meal_type", "MealType"),
    ("custom-filter", "custom_filter", "CustomFilter"),
    ("keyword", "keyword", "Keyword"),
    ("food", "food", "Food"),
    ("unit-conversion", "unit_conversion", "UnitConversion"),
    ("supermarket", "supermarket", "Supermarket"),
    ("supermarket-category-relation", "supermarket_category_relation", "SupermarketCategoryRelation"),
    ("automation", "automation", "Automation"),
    ("recipe-book", "recipe_book", "RecipeBook"),
    ("recipe-book-entry", "recipe_book_entry", "RecipeBookEntry"),
    ("cook-log", "cook_log", "CookLog"),
    ("view-log", "view_log", "ViewLog"),
    ("shopping-list", "shopping_list", "ShoppingList"),
    ("shopping-list-recipe", "shopping_list_recipe", "ShoppingListRecipe"),
    ("inventory-location", "inventory_location", "InventoryLocation"),
]

# Models that need special params
PARAMETERIZED_MODELS = [
    ("meal-plan", "meal_plan", "MealPlan", {"from_date": "1970-01-01", "to_date": "2099-12-31"}),
    ("shopping-list-entry", "shopping_list_entry", "ShoppingListEntry", {"empty": "true"}),
    ("inventory-entry", "inventory_entry", "InventoryEntry", {"empty": "true"}),
]


def write_json(output_dir: str, filename: str, data: list[dict]):
    path = os.path.join(output_dir, f"{filename}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def export_simple_models(client: TandoorClient, output_dir: str) -> dict[str, int]:
    """Export all simple models (single get_all call each)."""
    counts = {}
    for endpoint, filename, desc in SIMPLE_MODELS:
        items = client.get_all(endpoint)
        log.info("%s: %d objects", desc, len(items))
        write_json(output_dir, filename, items)
        counts[filename] = len(items)
    return counts


def export_parameterized_models(client: TandoorClient, output_dir: str) -> dict[str, int]:
    """Export models that need special query params."""
    counts = {}
    for endpoint, filename, desc, params in PARAMETERIZED_MODELS:
        items = client.get_all(endpoint, params=params)
        log.info("%s: %d objects", desc, len(items))
        write_json(output_dir, filename, items)
        counts[filename] = len(items)
    return counts


def export_recipes(client: TandoorClient, output_dir: str) -> dict[str, int]:
    """Export recipes with full details (steps, ingredients, nutrition).

    The list endpoint returns overview data only; we fetch each recipe
    individually to get the complete representation.
    """
    overviews = client.get_all("recipe")
    log.info("Recipe: %d from list endpoint, fetching details...", len(overviews))

    recipes = []
    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    image_count = 0

    for i, overview in enumerate(overviews, 1):
        recipe = client.get_one("recipe", overview["id"])
        recipes.append(recipe)

        # Download image if present
        image_url = recipe.get("image")
        if image_url:
            img_data = client.download_image(image_url)
            if img_data:
                img_path = os.path.join(images_dir, f"recipe_{recipe['id']}.png")
                with open(img_path, "wb") as f:
                    f.write(img_data)
                image_count += 1

        if i % 50 == 0:
            log.info("  ... fetched %d/%d recipes", i, len(overviews))

    log.info("Recipe: %d full details exported, %d images", len(recipes), image_count)
    write_json(output_dir, "recipe", recipes)
    return {"recipe": len(recipes), "_images": image_count}


def run_export(client: TandoorClient, output_dir: str):
    """Run full export from source to output directory."""
    os.makedirs(output_dir, exist_ok=True)

    log.info("=== Starting export to %s ===", output_dir)
    counts = {}

    # Simple models
    counts.update(export_simple_models(client, output_dir))

    # Parameterized models
    counts.update(export_parameterized_models(client, output_dir))

    # Recipes with full details + images
    recipe_counts = export_recipes(client, output_dir)
    counts.update(recipe_counts)

    # Write manifest
    manifest = {
        "version": 1,
        "source_url": client.base_url,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "counts": {k: v for k, v in counts.items() if not k.startswith("_")},
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    log.info("=== Export complete ===")
    for model, count in sorted(counts.items()):
        if not model.startswith("_"):
            log.info("  %s: %d", model, count)
    if recipe_counts.get("_images", 0):
        log.info("  images: %d", recipe_counts["_images"])

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Export Tandoor space data to local directory")
    parser.add_argument("--source-url", required=True, help="Source instance base URL")
    parser.add_argument("--source-token", required=True, help="Source API token")
    parser.add_argument("--output", required=True, help="Output directory path")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP request timeout in seconds")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    client = TandoorClient(args.source_url, args.source_token, timeout=args.timeout)

    try:
        run_export(client, args.output)
    except Exception:
        log.exception("Export failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
