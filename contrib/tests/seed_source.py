#!/usr/bin/env python3
"""
Populate a Tandoor source instance with realistic test data via REST API.

Creates data in dependency order matching the 10 migration phases.
Returns a manifest dict summarizing what was created.

Usage:
    python seed_source.py <base_url> <token>
"""

import json
import sys

# Allow importing conftest_helpers from the same directory
sys.path.insert(0, __file__ and __import__("os").path.dirname(__file__) or ".")
from conftest_helpers import TandoorAPIClient


def seed(client: TandoorAPIClient) -> dict:
    """Populate the source instance. Returns manifest of created objects."""
    manifest = {}

    # ===== Phase 1: Standalone setup data =====
    print("Seeding Phase 1: Standalone setup data...")

    # PropertyType (one per category)
    property_types = []
    for name, cat in [("Calories", "NUTRITION"), ("Gluten", "ALLERGEN"),
                      ("Price per kg", "PRICE"), ("Protein Goal", "GOAL"),
                      ("Shelf Life", "OTHER")]:
        pt = client.post("property-type/", {"name": name, "category": cat})
        property_types.append(pt)
    manifest["property_type"] = property_types

    # SupermarketCategory
    sm_categories = []
    for name in ["Produce", "Dairy", "Meat", "Bakery"]:
        sc = client.post("supermarket-category/", {"name": name})
        sm_categories.append(sc)
    manifest["supermarket_category"] = sm_categories
    sc_by_name = {sc["name"]: sc for sc in sm_categories}

    # Unit
    units = []
    for name, plural in [("g", "g"), ("kg", "kg"), ("ml", "ml"),
                         ("L", "L"), ("cups", "cups"), ("pieces", "pieces")]:
        u = client.post("unit/", {"name": name, "plural_name": plural})
        units.append(u)
    manifest["unit"] = units
    u_by_name = {u["name"]: u for u in units}

    # MealType
    meal_types = []
    for name in ["Breakfast", "Lunch", "Dinner"]:
        mt = client.post("meal-type/", {"name": name})
        meal_types.append(mt)
    manifest["meal_type"] = meal_types
    mt_by_name = {mt["name"]: mt for mt in meal_types}

    # ===== Phase 2: Tree structures =====
    print("Seeding Phase 2: Tree structures...")

    # Keywords — tree: Cuisine→European→Italian/French, Cuisine→Asian, Dietary→Vegetarian/Vegan, Quick Meals
    keywords = {}
    for name in ["Cuisine", "Dietary", "Quick Meals"]:
        kw = client.post("keyword/", {"name": name})
        keywords[name] = kw

    for name in ["European", "Asian"]:
        kw = client.post("keyword/", {"name": name})
        keywords[name] = kw

    for name in ["Italian", "French", "Vegetarian", "Vegan"]:
        kw = client.post("keyword/", {"name": name})
        keywords[name] = kw

    manifest["keyword"] = list(keywords.values())

    # Foods — tree structure created flat (API handles tree via get_or_create)
    # Root foods: Protein, Dairy, Vegetables, Salt
    foods = {}

    # Root level
    for name in ["Protein", "Dairy category", "Vegetables"]:
        f = client.post("food/", {"name": name})
        foods[name] = f

    f = client.post("food/", {"name": "Salt"})  # root leaf, no category
    foods["Salt"] = f

    # Depth 2
    for name in ["Meat", "Fish"]:
        f = client.post("food/", {"name": name})
        foods[name] = f

    # Depth 3 and leaf foods with categories
    f = client.post("food/", {
        "name": "Chicken Breast",
        "supermarket_category": {"name": "Meat"},
    })
    foods["Chicken Breast"] = f

    f = client.post("food/", {
        "name": "Ground Beef",
        "supermarket_category": {"name": "Meat"},
    })
    foods["Ground Beef"] = f

    f = client.post("food/", {"name": "Salmon",
                               "supermarket_category": {"name": "Meat"}})
    foods["Salmon"] = f

    f = client.post("food/", {"name": "Milk",
                               "supermarket_category": {"name": "Dairy"}})
    foods["Milk"] = f

    f = client.post("food/", {"name": "Cheese",
                               "supermarket_category": {"name": "Dairy"}})
    foods["Cheese"] = f

    f = client.post("food/", {"name": "Tomato",
                               "supermarket_category": {"name": "Produce"}})
    foods["Tomato"] = f

    f = client.post("food/", {"name": "Onion",
                               "supermarket_category": {"name": "Produce"}})
    foods["Onion"] = f

    manifest["food"] = list(foods.values())
    f_by_name = {f["name"]: f for f in foods.values()}
    pt_by_name = {pt["name"]: pt for pt in property_types}

    # ===== Phase 3: Food Properties =====
    print("Seeding Phase 3: Food properties...")

    # Tomato: 3 properties
    client.patch("food/", f_by_name["Tomato"]["id"], {
        "properties": [
            {"property_type": {"name": "Calories"}, "property_amount": 18.0},
            {"property_type": {"name": "Price per kg"}, "property_amount": 3.50},
            {"property_type": {"name": "Shelf Life"}, "property_amount": 7.0},
        ],
        "properties_food_amount": 100,
        "properties_food_unit": {"name": "g"},
    })

    # Chicken Breast: 2 properties
    client.patch("food/", f_by_name["Chicken Breast"]["id"], {
        "properties": [
            {"property_type": {"name": "Calories"}, "property_amount": 165.0},
            {"property_type": {"name": "Protein Goal"}, "property_amount": 31.0},
        ],
        "properties_food_amount": 100,
        "properties_food_unit": {"name": "g"},
    })

    # Milk: 1 property
    client.patch("food/", f_by_name["Milk"]["id"], {
        "properties": [
            {"property_type": {"name": "Calories"}, "property_amount": 42.0},
        ],
        "properties_food_amount": 100,
        "properties_food_unit": {"name": "ml"},
    })

    # ===== Phase 4: Food substitutes, UnitConversion, Supermarket, Automation =====
    print("Seeding Phase 4: Setup data with dependencies...")

    # Food substitutes: Chicken↔Ground Beef, Milk↔Cheese
    client.patch("food/", f_by_name["Chicken Breast"]["id"], {
        "substitute": [{"id": f_by_name["Ground Beef"]["id"]}],
    })
    client.patch("food/", f_by_name["Ground Beef"]["id"], {
        "substitute": [{"id": f_by_name["Chicken Breast"]["id"]}],
    })
    client.patch("food/", f_by_name["Milk"]["id"], {
        "substitute": [{"id": f_by_name["Cheese"]["id"]}],
    })

    # UnitConversion: 2 generic + 2 food-specific
    conversions = []
    uc = client.post("unit-conversion/", {
        "base_amount": 1, "base_unit": {"name": "kg"},
        "converted_amount": 1000, "converted_unit": {"name": "g"},
    })
    conversions.append(uc)

    uc = client.post("unit-conversion/", {
        "base_amount": 1, "base_unit": {"name": "L"},
        "converted_amount": 1000, "converted_unit": {"name": "ml"},
    })
    conversions.append(uc)

    uc = client.post("unit-conversion/", {
        "base_amount": 1, "base_unit": {"name": "cups"},
        "converted_amount": 240, "converted_unit": {"name": "ml"},
        "food": {"name": "Milk"},
    })
    conversions.append(uc)

    uc = client.post("unit-conversion/", {
        "base_amount": 1, "base_unit": {"name": "pieces"},
        "converted_amount": 150, "converted_unit": {"name": "g"},
        "food": {"name": "Tomato"},
    })
    conversions.append(uc)
    manifest["unit_conversion"] = conversions

    # Supermarkets with category relations
    supermarkets = []
    sm1 = client.post("supermarket/", {"name": "Main Grocery"})
    supermarkets.append(sm1)
    sm2 = client.post("supermarket/", {"name": "Corner Shop"})
    supermarkets.append(sm2)
    manifest["supermarket"] = supermarkets

    # SupermarketCategoryRelation
    sm_relations = []
    for sm, cats in [(sm1, ["Produce", "Dairy"]), (sm2, ["Meat", "Bakery"])]:
        for i, cat_name in enumerate(cats):
            rel = client.post("supermarket-category-relation/", {
                "supermarket": sm["id"],
                "category": {"name": cat_name},
                "order": i,
            })
            sm_relations.append(rel)
    manifest["supermarket_category_relation"] = sm_relations

    # Automations
    automations = []
    auto_defs = [
        {"name": "chicken alias", "type": "FOOD_ALIAS", "param_1": "poulet", "param_2": "Chicken Breast"},
        {"name": "gram alias", "type": "UNIT_ALIAS", "param_1": "gramme", "param_2": "g"},
        {"name": "veggie alias", "type": "KEYWORD_ALIAS", "param_1": "veg", "param_2": "Vegetarian"},
        {"name": "remove ads", "type": "DESCRIPTION_REPLACE", "param_1": "sponsored", "param_2": ""},
        {"name": "skip pinch", "type": "NEVER_UNIT", "param_1": "pinch"},
    ]
    for ad in auto_defs:
        a = client.post("automation/", ad)
        automations.append(a)
    manifest["automation"] = automations

    # ===== Phase 5: Recipes =====
    print("Seeding Phase 5: Recipes...")

    recipes = {}

    # Recipe 1: Simple Salad
    r1 = client.post("recipe/", {
        "name": "Simple Salad",
        "description": "A fresh garden salad",
        "working_time": 10,
        "waiting_time": 0,
        "servings": 2,
        "keywords": [{"name": "Quick Meals"}, {"name": "Vegetarian"}],
        "steps": [{
            "name": "Prepare",
            "instruction": "Chop vegetables and toss together.",
            "order": 0,
            "ingredients": [
                {"food": {"name": "Tomato"}, "unit": {"name": "pieces"}, "amount": 3, "order": 0},
                {"food": {"name": "Onion"}, "unit": {"name": "pieces"}, "amount": 1, "order": 1},
                {"food": {"name": "Salt"}, "unit": {"name": "g"}, "amount": 2, "order": 2},
            ],
        }],
    })
    recipes["Simple Salad"] = r1

    # Recipe 2: Complex Pasta (3 steps, nutrition, properties)
    r2 = client.post("recipe/", {
        "name": "Complex Pasta",
        "description": "A multi-step pasta dish",
        "working_time": 30,
        "waiting_time": 20,
        "servings": 4,
        "servings_text": "portions",
        "keywords": [
            {"name": "European"},
            {"name": "Italian"},
            {"name": "Cuisine"},
        ],
        "nutrition": {
            "carbohydrates": 65.0,
            "fats": 12.0,
            "proteins": 18.0,
            "calories": 450.0,
            "source": "manual",
        },
        "steps": [
            {
                "name": "Prep",
                "instruction": "Prepare all ingredients.",
                "order": 0,
                "ingredients": [],  # step with 0 ingredients
            },
            {
                "name": "Cook sauce",
                "instruction": "Sauté onions, add tomatoes, simmer 15 min.",
                "order": 1,
                "ingredients": [
                    {"food": {"name": "Tomato"}, "unit": {"name": "g"}, "amount": 400, "order": 0},
                    {"food": {"name": "Onion"}, "unit": {"name": "pieces"}, "amount": 2, "order": 1},
                ],
            },
            {
                "name": "Combine",
                "instruction": "Mix pasta with sauce, add cheese on top.",
                "order": 2,
                "ingredients": [
                    {"food": {"name": "Cheese"}, "unit": {"name": "g"}, "amount": 50, "order": 0},
                    {"food": {"name": "Salt"}, "unit": {"name": "g"}, "amount": 5, "order": 1},
                    {"food": {"name": "Tomato"}, "unit": {"name": "g"}, "amount": 100, "order": 2},
                    {"food": {"name": "Milk"}, "unit": {"name": "ml"}, "amount": 50, "order": 3},
                    {"food": {"name": "Onion"}, "unit": {"name": "pieces"}, "amount": 1, "order": 4},
                ],
            },
        ],
    })
    recipes["Complex Pasta"] = r2

    # Recipe 3: Untitled Test (empty description, no keywords, no source_url)
    r3 = client.post("recipe/", {
        "name": "Untitled Test",
        "description": "",
        "steps": [{
            "instruction": "Do something.",
            "order": 0,
            "ingredients": [
                {"food": {"name": "Salt"}, "unit": None, "amount": 1, "order": 0, "no_amount": True},
            ],
        }],
    })
    recipes["Untitled Test"] = r3

    # Recipe 4: Sub-Recipe Container (step 2 references recipe 1)
    r4 = client.post("recipe/", {
        "name": "Sub-Recipe Container",
        "description": "Contains a sub-recipe reference",
        "keywords": [{"name": "Quick Meals"}],
        "steps": [
            {
                "instruction": "Make the main dish.",
                "order": 0,
                "ingredients": [
                    {"food": {"name": "Chicken Breast"}, "unit": {"name": "g"}, "amount": 200, "order": 0},
                ],
            },
            {
                "instruction": "Serve with salad on the side.",
                "order": 1,
                "ingredients": [],
                # step_recipe back-patched in phase 6
            },
        ],
    })
    recipes["Sub-Recipe Container"] = r4

    # Recipe 5: Chicken Dinner
    r5 = client.post("recipe/", {
        "name": "Chicken Dinner",
        "description": "A hearty chicken dinner",
        "working_time": 45,
        "waiting_time": 30,
        "servings": 4,
        "keywords": [{"name": "Cuisine"}, {"name": "European"}],
        "nutrition": {
            "carbohydrates": 30.0,
            "fats": 15.0,
            "proteins": 40.0,
            "calories": 550.0,
        },
        "steps": [{
            "instruction": "Season chicken, roast with vegetables.",
            "order": 0,
            "ingredients": [
                {"food": {"name": "Chicken Breast"}, "unit": {"name": "g"}, "amount": 500, "order": 0},
                {"food": {"name": "Tomato"}, "unit": {"name": "pieces"}, "amount": 2, "order": 1},
                {"food": {"name": "Onion"}, "unit": {"name": "pieces"}, "amount": 1, "order": 2},
                {"food": {"name": "Salt"}, "unit": {"name": "g"}, "amount": 3, "order": 3},
            ],
        }],
    })
    recipes["Chicken Dinner"] = r5

    manifest["recipe"] = list(recipes.values())

    # ===== Phase 6: Back-patches =====
    print("Seeding Phase 6: Back-patches...")

    # Food.recipe: Link "Chicken Breast" food to "Chicken Dinner" recipe
    client.patch("food/", f_by_name["Chicken Breast"]["id"], {
        "recipe": {"id": recipes["Chicken Dinner"]["id"]},
    })

    # Step.step_recipe: Link step 2 of "Sub-Recipe Container" to "Simple Salad"
    # Update via recipe API (direct step PATCH causes 500 on some versions)
    r4_detail = client.get("recipe/", r4["id"])
    r4_steps = sorted(r4_detail.get("steps", []), key=lambda s: s.get("order", 0))
    if len(r4_steps) >= 2:
        r4_steps[1]["step_recipe"] = r1["id"]
        client.put("recipe/", r4["id"], r4_detail)

    # ===== Phase 7: Collections =====
    print("Seeding Phase 7: Collections...")

    # RecipeBooks
    book1 = client.post("recipe-book/", {"name": "Favorites", "shared": []})
    book2 = client.post("recipe-book/", {"name": "Weeknight Dinners", "shared": []})
    manifest["recipe_book"] = [book1, book2]

    # RecipeBookEntries
    book_entries = []
    for book, recipe_names in [(book1, ["Simple Salad", "Complex Pasta", "Chicken Dinner"]),
                               (book2, ["Chicken Dinner"])]:
        for rname in recipe_names:
            entry = client.post("recipe-book-entry/", {
                "book": book["id"],
                "recipe": recipes[rname]["id"],
            })
            book_entries.append(entry)
    manifest["recipe_book_entry"] = book_entries

    # CookLogs
    cooklogs = []
    for rname, rating, servings in [("Simple Salad", 4, 2), ("Complex Pasta", 5, 4), ("Chicken Dinner", 3, 6)]:
        cl = client.post("cook-log/", {
            "recipe": recipes[rname]["id"],
            "rating": rating,
            "servings": servings,
            "comment": f"Cooked {rname}",
        })
        cooklogs.append(cl)
    manifest["cook_log"] = cooklogs

    # ===== Phase 8: Meal Planning =====
    print("Seeding Phase 8: Meal planning...")

    mealplans = []
    mp_defs = [
        {"title": "Monday breakfast", "recipe_name": "Simple Salad",
         "meal_type": {"id": mt_by_name["Breakfast"]["id"], "name": "Breakfast"},
         "from_date": "2027-06-01T08:00:00", "to_date": "2027-06-01T09:00:00", "servings": 2},
        {"title": "Monday lunch", "recipe_name": "Complex Pasta",
         "meal_type": {"id": mt_by_name["Lunch"]["id"], "name": "Lunch"},
         "from_date": "2027-06-01T12:00:00", "to_date": "2027-06-01T13:00:00", "servings": 4},
        {"title": "Monday dinner", "recipe_name": "Chicken Dinner",
         "meal_type": {"id": mt_by_name["Dinner"]["id"], "name": "Dinner"},
         "from_date": "2027-06-01T18:00:00", "to_date": "2027-06-01T19:00:00", "servings": 4},
        {"title": "Note only plan", "recipe_name": None,
         "meal_type": {"id": mt_by_name["Lunch"]["id"], "name": "Lunch"},
         "from_date": "2027-06-02T12:00:00", "to_date": "2027-06-02T13:00:00",
         "note": "Just a note, no recipe", "servings": 1},
    ]
    for mp_def in mp_defs:
        recipe_name = mp_def.pop("recipe_name")
        if recipe_name:
            r = recipes[recipe_name]
            mp_def["recipe"] = {"id": r["id"], "name": r["name"]}
        mp = client.post("meal-plan/", mp_def)
        mealplans.append(mp)
    manifest["meal_plan"] = mealplans

    # ===== Phase 9: Shopping =====
    print("Seeding Phase 9: Shopping lists...")

    sl1 = client.post("shopping-list/", {"name": "Weekly"})
    sl2 = client.post("shopping-list/", {"name": "Party"})
    manifest["shopping_list"] = [sl1, sl2]

    # ShoppingListEntries
    sle_defs = [
        {"food": {"name": "Tomato"}, "unit": {"name": "pieces"}, "amount": 6},
        {"food": {"name": "Milk"}, "unit": {"name": "L"}, "amount": 2},
        {"food": {"name": "Chicken Breast"}, "unit": {"name": "kg"}, "amount": 1},
        {"food": {"name": "Cheese"}, "unit": {"name": "g"}, "amount": 200},
        {"food": {"name": "Onion"}, "unit": None, "amount": 5},  # unit=null
        {"food": {"name": "Salt"}, "unit": {"name": "g"}, "amount": 500},
    ]
    sle_list = []
    for sle_def in sle_defs:
        sle = client.post("shopping-list-entry/", sle_def)
        sle_list.append(sle)
    manifest["shopping_list_entry"] = sle_list

    # ===== Phase 10: Inventory =====
    print("Seeding Phase 10: Inventory...")

    inv_locations = []
    for name, is_freezer in [("Fridge", False), ("Pantry", False), ("Freezer", True)]:
        loc = client.post("inventory-location/", {"name": name, "is_freezer": is_freezer, "household": {"name": "Default Household"}})
        inv_locations.append(loc)
    manifest["inventory_location"] = inv_locations
    loc_by_name = {loc["name"]: loc for loc in inv_locations}

    inv_entries = []
    hh = {"name": "Default Household"}
    ie_defs = [
        {"food": {"name": "Milk"}, "unit": {"name": "L"},
         "amount": 1, "inventory_location": {"name": "Fridge", "household": hh}},
        {"food": {"name": "Cheese"}, "unit": {"name": "g"},
         "amount": 500, "inventory_location": {"name": "Fridge", "household": hh}},
        {"food": {"name": "Chicken Breast"}, "unit": {"name": "g"},
         "amount": 800, "inventory_location": {"name": "Freezer", "household": hh}},
        {"food": {"name": "Salt"}, "unit": {"name": "kg"},
         "amount": 2, "inventory_location": {"name": "Pantry", "household": hh}},
        {"food": {"name": "Tomato"}, "unit": {"name": "pieces"},
         "amount": 0, "inventory_location": {"name": "Fridge", "household": hh}},  # amount=0
    ]
    for ie_def in ie_defs:
        ie = client.post("inventory-entry/", ie_def)
        inv_entries.append(ie)
    manifest["inventory_entry"] = inv_entries

    print("Seeding complete!")
    return manifest


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <base_url> <token>", file=sys.stderr)
        sys.exit(2)

    base_url = sys.argv[1]
    token = sys.argv[2]
    client = TandoorAPIClient(base_url, token)

    manifest = seed(client)
    print(json.dumps({k: len(v) if isinstance(v, list) else v
                      for k, v in manifest.items()}, indent=2))


if __name__ == "__main__":
    main()
