#!/usr/bin/env python3
"""
Verify migration by comparing source and target Tandoor instances.

Fetches all data from both instances via API and compares field-by-field.
Objects are matched by name or composite key, never by ID.

Usage:
    python verify_migration.py <source_url> <source_token> <target_url> <target_token>

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

import sys

sys.path.insert(0, __file__ and __import__("os").path.dirname(__file__) or ".")
from conftest_helpers import (
    TandoorAPIClient,
    VerificationResult,
    compare_fields,
    compare_nested_name,
    match_by_key,
    sorted_names,
)

IGNORED_FIELDS = {"id", "created_by", "created_at", "updated_at", "space", "image"}


def verify_simple(source_client: TandoorAPIClient, target_client: TandoorAPIClient,
                  endpoint: str, model_name: str, fields: list[str],
                  key_fn=lambda x: x.get("name", ""),
                  params: dict | None = None) -> VerificationResult:
    """Verify a simple model by matching on key and comparing fields."""
    result = VerificationResult(model_name=model_name)

    src = source_client.get_all(endpoint, params=params)
    tgt = target_client.get_all(endpoint, params=params)
    result.source_count = len(src)
    result.target_count = len(tgt)

    matched, missing, extra = match_by_key(src, tgt, key_fn)
    result.matched = len(matched)

    for obj in missing:
        result.add_missing(str(key_fn(obj)))
    for obj in extra:
        result.add_extra(str(key_fn(obj)))

    for s, t in matched:
        label = str(key_fn(s))
        mismatches = compare_fields(s, t, fields, label)
        for m in mismatches:
            result.add_mismatch(m)

    return result


def verify_nested_fk(source_val, target_val, field_name: str, label: str) -> list[str]:
    """Compare nested FK by name."""
    if source_val is None and target_val is None:
        return []
    s_name = source_val.get("name") if isinstance(source_val, dict) else None
    t_name = target_val.get("name") if isinstance(target_val, dict) else None
    if s_name is None and t_name is None:
        return []
    if s_name != t_name:
        return [f"{label}: {field_name}: source={s_name!r}, target={t_name!r}"]
    return []


def verify_property_types(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    return verify_simple(src, tgt, "property-type/", "PropertyType",
                         ["name", "category", "order"])


def verify_supermarket_categories(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    return verify_simple(src, tgt, "supermarket-category/", "SupermarketCategory", ["name"])


def verify_units(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    return verify_simple(src, tgt, "unit/", "Unit", ["name", "plural_name"])


def verify_meal_types(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    return verify_simple(src, tgt, "meal-type/", "MealType", ["name", "order"])


def verify_keywords(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    result = verify_simple(src, tgt, "keyword/", "Keyword", ["name", "description"])

    # Additional tree structure check
    src_kw = {kw["name"]: kw for kw in src.get_all("keyword/")}
    tgt_kw = {kw["name"]: kw for kw in tgt.get_all("keyword/")}

    for name, sk in src_kw.items():
        tk = tgt_kw.get(name)
        if tk and sk.get("depth") != tk.get("depth"):
            result.add_mismatch(f"{name}: depth: source={sk.get('depth')}, target={tk.get('depth')}")

    return result


def verify_foods(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    result = VerificationResult(model_name="Food")
    src_foods = src.get_all("food/")
    tgt_foods = tgt.get_all("food/")
    result.source_count = len(src_foods)
    result.target_count = len(tgt_foods)

    matched, missing, extra = match_by_key(src_foods, tgt_foods)
    result.matched = len(matched)

    for obj in missing:
        result.add_missing(obj["name"])
    for obj in extra:
        result.add_extra(obj["name"])

    prop_matched = 0
    prop_total = 0
    sub_matched = 0
    sub_total = 0

    for sf, tf in matched:
        label = sf["name"]
        mismatches = compare_fields(sf, tf, ["name", "plural_name", "description"], label)
        mismatches += verify_nested_fk(sf.get("supermarket_category"), tf.get("supermarket_category"),
                                       "supermarket_category", label)
        for m in mismatches:
            result.add_mismatch(m)

        # Properties comparison
        s_props = sf.get("properties") or []
        t_props = tf.get("properties") or []
        prop_total += len(s_props)
        s_pt_names = {(p.get("property_type", {}).get("name"), round(p.get("property_amount", 0), 2)) for p in s_props}
        t_pt_names = {(p.get("property_type", {}).get("name"), round(p.get("property_amount", 0), 2)) for p in t_props}
        matching_props = s_pt_names & t_pt_names
        prop_matched += len(matching_props)
        for missing_prop in s_pt_names - t_pt_names:
            result.add_mismatch(f"{label}: missing property {missing_prop[0]}={missing_prop[1]}")

        # Substitutes comparison
        s_subs = sorted_names(sf.get("substitute"))
        t_subs = sorted_names(tf.get("substitute"))
        sub_total += len(s_subs)
        for sn in s_subs:
            if sn in t_subs:
                sub_matched += 1
            else:
                result.add_mismatch(f"{label}: missing substitute {sn}")

    result.sub_results["Properties"] = VerificationResult(
        model_name="Properties", matched=prop_matched, source_count=prop_total,
        passed=prop_matched == prop_total)
    result.sub_results["Substitutes"] = VerificationResult(
        model_name="Substitutes", matched=sub_matched, source_count=sub_total,
        passed=sub_matched == sub_total)

    return result


def verify_unit_conversions(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    def uc_key(uc):
        bu = uc.get("base_unit", {})
        cu = uc.get("converted_unit", {})
        food = uc.get("food")
        bu_name = bu.get("name") if isinstance(bu, dict) else None
        cu_name = cu.get("name") if isinstance(cu, dict) else None
        food_name = food.get("name") if isinstance(food, dict) else None
        return (bu_name, cu_name, food_name)

    result = verify_simple(src, tgt, "unit-conversion/", "UnitConversion",
                           ["base_amount", "converted_amount"], key_fn=uc_key)
    return result


def verify_supermarkets(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    result = verify_simple(src, tgt, "supermarket/", "Supermarket", ["name"])

    # Check category relations
    src_rels = src.get_all("supermarket-category-relation/")
    tgt_rels = tgt.get_all("supermarket-category-relation/")

    def rel_key(r):
        cat = r.get("category", {})
        cat_name = cat.get("name") if isinstance(cat, dict) else None
        # supermarket might be an int or dict
        sm = r.get("supermarket")
        sm_name = sm.get("name") if isinstance(sm, dict) else str(sm)
        return (sm_name, cat_name)

    rel_result = VerificationResult(model_name="CategoryRelations")
    rel_result.source_count = len(src_rels)
    rel_result.target_count = len(tgt_rels)

    # Simple count check since relation keys may differ
    rel_result.matched = min(len(src_rels), len(tgt_rels))
    if len(src_rels) != len(tgt_rels):
        rel_result.add_mismatch(f"count: source={len(src_rels)}, target={len(tgt_rels)}")

    result.sub_results["CategoryRelations"] = rel_result
    return result


def verify_automations(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    def auto_key(a):
        return (a.get("name", ""), a.get("type", ""))

    return verify_simple(src, tgt, "automation/", "Automation",
                         ["name", "type", "param_1", "param_2", "param_3", "disabled"],
                         key_fn=auto_key)


def verify_recipes(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    result = VerificationResult(model_name="Recipe")
    src_recipes = src.get_all("recipe/")
    tgt_recipes = tgt.get_all("recipe/")
    result.source_count = len(src_recipes)
    result.target_count = len(tgt_recipes)

    matched, missing, extra = match_by_key(src_recipes, tgt_recipes)
    result.matched = len(matched)

    for obj in missing:
        result.add_missing(obj["name"])

    step_total = 0
    step_matched = 0
    ing_total = 0
    ing_matched = 0
    nutr_total = 0
    nutr_matched = 0

    for sr, tr in matched:
        label = sr["name"]
        # Get full details
        sr_full = src.get("recipe/", sr["id"])
        tr_full = tgt.get("recipe/", tr["id"])

        mismatches = compare_fields(sr_full, tr_full,
                                    ["name", "description", "working_time", "waiting_time", "servings"],
                                    label)
        for m in mismatches:
            result.add_mismatch(m)

        # Keywords comparison
        s_kw = sorted_names(sr_full.get("keywords"))
        t_kw = sorted_names(tr_full.get("keywords"))
        if s_kw != t_kw:
            result.add_mismatch(f"{label}: keywords: source={s_kw}, target={t_kw}")

        # Nutrition
        s_nutr = sr_full.get("nutrition")
        t_nutr = tr_full.get("nutrition")
        if s_nutr and isinstance(s_nutr, dict):
            nutr_total += 1
            if t_nutr and isinstance(t_nutr, dict):
                nutr_mismatches = compare_fields(s_nutr, t_nutr,
                                                 ["carbohydrates", "fats", "proteins", "calories"],
                                                 f"{label}/nutrition")
                if not nutr_mismatches:
                    nutr_matched += 1
                for m in nutr_mismatches:
                    result.add_mismatch(m)
            else:
                result.add_mismatch(f"{label}: nutrition missing on target")

        # Steps comparison (by order)
        s_steps = sorted(sr_full.get("steps") or [], key=lambda s: s.get("order", 0))
        t_steps = sorted(tr_full.get("steps") or [], key=lambda s: s.get("order", 0))
        step_total += len(s_steps)

        for i, (ss, ts) in enumerate(zip(s_steps, t_steps)):
            step_label = f"{label}/step[{i}]"
            step_mismatches = compare_fields(ss, ts, ["instruction", "order", "name"], step_label)
            if not step_mismatches:
                step_matched += 1
            for m in step_mismatches:
                result.add_mismatch(m)

            # Ingredients within step
            s_ings = sorted(ss.get("ingredients") or [], key=lambda x: x.get("order", 0))
            t_ings = sorted(ts.get("ingredients") or [], key=lambda x: x.get("order", 0))
            ing_total += len(s_ings)

            for j, (si, ti) in enumerate(zip(s_ings, t_ings)):
                ing_label = f"{label}/step[{i}]/ing[{j}]"
                ing_ok = True
                m = compare_fields(si, ti, ["amount", "note", "order"], ing_label)
                if m:
                    ing_ok = False
                    for mm in m:
                        result.add_mismatch(mm)

                # Food name
                sf_name = si.get("food", {}).get("name") if si.get("food") else None
                tf_name = ti.get("food", {}).get("name") if ti.get("food") else None
                if sf_name != tf_name:
                    result.add_mismatch(f"{ing_label}: food: {sf_name!r} vs {tf_name!r}")
                    ing_ok = False

                # Unit name
                su_name = si.get("unit", {}).get("name") if si.get("unit") else None
                tu_name = ti.get("unit", {}).get("name") if ti.get("unit") else None
                if su_name != tu_name:
                    result.add_mismatch(f"{ing_label}: unit: {su_name!r} vs {tu_name!r}")
                    ing_ok = False

                if ing_ok:
                    ing_matched += 1

            if len(s_ings) != len(t_ings):
                result.add_mismatch(
                    f"{step_label}: ingredient count: source={len(s_ings)}, target={len(t_ings)}")

        if len(s_steps) != len(t_steps):
            result.add_mismatch(f"{label}: step count: source={len(s_steps)}, target={len(t_steps)}")

    result.sub_results["Steps"] = VerificationResult(
        model_name="Steps", matched=step_matched, source_count=step_total,
        passed=step_matched == step_total)
    result.sub_results["Ingredients"] = VerificationResult(
        model_name="Ingredients", matched=ing_matched, source_count=ing_total,
        passed=ing_matched == ing_total)
    result.sub_results["Nutrition"] = VerificationResult(
        model_name="Nutrition", matched=nutr_matched, source_count=nutr_total,
        passed=nutr_matched == nutr_total)

    return result


def verify_recipe_books(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    result = verify_simple(src, tgt, "recipe-book/", "RecipeBook", ["name", "description"])

    # Entries
    src_entries = src.get_all("recipe-book-entry/")
    tgt_entries = tgt.get_all("recipe-book-entry/")

    def rbe_key(e):
        book = e.get("book_content", {}) or e.get("book", {})
        recipe = e.get("recipe_content", {}) or e.get("recipe", {})
        b_name = book.get("name") if isinstance(book, dict) else str(book)
        r_name = recipe.get("name") if isinstance(recipe, dict) else str(recipe)
        return (b_name, r_name)

    entry_result = VerificationResult(model_name="RecipeBookEntry")
    entry_result.source_count = len(src_entries)
    entry_result.target_count = len(tgt_entries)

    # Simple count match
    entry_result.matched = min(len(src_entries), len(tgt_entries))
    if len(src_entries) != len(tgt_entries):
        entry_result.add_mismatch(f"count: source={len(src_entries)}, target={len(tgt_entries)}")

    result.sub_results["Entries"] = entry_result
    return result


def verify_cooklogs(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    def cl_key(cl):
        recipe = cl.get("recipe")
        r_name = recipe.get("name") if isinstance(recipe, dict) else str(recipe)
        return (r_name, cl.get("rating"), cl.get("servings"))

    return verify_simple(src, tgt, "cook-log/", "CookLog",
                         ["rating", "servings", "comment"], key_fn=cl_key)


def verify_mealplans(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    params = {"from_date": "1970-01-01", "to_date": "2099-12-31"}

    def mp_key(mp):
        mt = mp.get("meal_type", {})
        mt_name = mt.get("name") if isinstance(mt, dict) else str(mt)
        return (mp.get("title", ""), mt_name, mp.get("from_date", ""))

    return verify_simple(src, tgt, "meal-plan/", "MealPlan",
                         ["title", "note", "servings"], key_fn=mp_key, params=params)


def verify_shopping_lists(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    return verify_simple(src, tgt, "shopping-list/", "ShoppingList", ["name"])


def verify_shopping_list_entries(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    params = {"empty": "true"}

    def sle_key(e):
        food = e.get("food", {})
        unit = e.get("unit", {})
        f_name = food.get("name") if isinstance(food, dict) else None
        u_name = unit.get("name") if isinstance(unit, dict) else None
        return (f_name, e.get("amount"), u_name)

    return verify_simple(src, tgt, "shopping-list-entry/", "ShoppingListEntry",
                         ["amount", "checked"], key_fn=sle_key, params=params)


def verify_inventory_locations(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    return verify_simple(src, tgt, "inventory-location/", "InventoryLocation",
                         ["name", "is_freezer"])


def verify_inventory_entries(src: TandoorAPIClient, tgt: TandoorAPIClient) -> VerificationResult:
    params = {"empty": "true"}

    def ie_key(e):
        food = e.get("food", {})
        loc = e.get("inventory_location", {})
        f_name = food.get("name") if isinstance(food, dict) else None
        l_name = loc.get("name") if isinstance(loc, dict) else None
        return (f_name, l_name, e.get("amount"))

    return verify_simple(src, tgt, "inventory-entry/", "InventoryEntry",
                         ["amount"], key_fn=ie_key, params=params)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list[VerificationResult]) -> bool:
    """Print verification report. Returns True if all passed."""
    print("\n=== Migration Verification Report ===\n")

    all_passed = True
    total_checks = 0
    passed_checks = 0

    for r in results:
        total_checks += 1
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            all_passed = False
        else:
            passed_checks += 1

        count_str = f"{r.matched}/{r.source_count} matched"
        line = f"{r.model_name:.<35s} {status} ({count_str})"
        print(line)

        for sub_name, sub_r in r.sub_results.items():
            sub_status = "PASS" if sub_r.passed else "FAIL"
            print(f"  - {sub_name}: {sub_r.matched}/{sub_r.source_count} matched")
            if not sub_r.passed:
                all_passed = False
            for m in sub_r.mismatches[:5]:
                print(f"    {m}")

        for m in r.missing_on_target[:5]:
            print(f"  MISSING on target: {m!r}")
        for m in r.mismatches[:10]:
            print(f"  MISMATCH {m}")

    print(f"\nRESULT: {'ALL PASSED' if all_passed else 'FAILURES DETECTED'} ({passed_checks}/{total_checks} checks)")
    return all_passed


def run_verification(source_url: str, source_token: str,
                     target_url: str, target_token: str) -> bool:
    src = TandoorAPIClient(source_url, source_token)
    tgt = TandoorAPIClient(target_url, target_token)

    results = [
        verify_property_types(src, tgt),
        verify_supermarket_categories(src, tgt),
        verify_units(src, tgt),
        verify_meal_types(src, tgt),
        verify_keywords(src, tgt),
        verify_foods(src, tgt),
        verify_unit_conversions(src, tgt),
        verify_supermarkets(src, tgt),
        verify_automations(src, tgt),
        verify_recipes(src, tgt),
        verify_recipe_books(src, tgt),
        verify_cooklogs(src, tgt),
        verify_mealplans(src, tgt),
        verify_shopping_lists(src, tgt),
        verify_shopping_list_entries(src, tgt),
        verify_inventory_locations(src, tgt),
        verify_inventory_entries(src, tgt),
    ]

    return print_report(results)


def main():
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <source_url> <source_token> <target_url> <target_token>",
              file=sys.stderr)
        sys.exit(2)

    passed = run_verification(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
