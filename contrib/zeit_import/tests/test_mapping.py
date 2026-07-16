"""Tests for the pure first-mention ingredient -> step mapping."""
import zeit_import as zi


def test_exact_word_match():
    steps = ["Die Nüsse rösten.", "Mit Honig und Salz abschmecken."]
    result = zi.map_ingredients_to_steps(steps, [(11, "Honig")])
    assert result == {11: 1}


def test_earliest_step_wins():
    steps = ["Honig erhitzen.", "Honig dazugeben."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Honig")])
    assert result == {1: 0}


def test_case_insensitive():
    steps = ["ZUERST DEN HALLOUMI BRATEN."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Halloumi")])
    assert result == {1: 0}


def test_inflection_via_prefix_food_plural_text_singular():
    # food "Auberginen" -> prefix "Aubergine" matches text
    steps = ["Die Aubergine würfeln."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Auberginen")])
    assert result == {1: 0}


def test_inflection_via_prefix_food_singular_text_plural():
    steps = ["Die Auberginen würfeln."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Aubergine")])
    assert result == {1: 0}


def test_compound_name_first_word():
    steps = ["Den Halloumi von beiden Seiten anbraten."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Halloumi-Käse")])
    assert result == {1: 0}


def test_full_name_beats_prefix_in_earlier_step():
    # full word "Zucker" only in step 1; step 0 has only the prefix inside "Zuckerschoten"
    steps = ["Zuckerschoten putzen.", "Zucker einrühren."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Zucker")])
    assert result == {1: 1}


def test_short_name_requires_word_boundary():
    # "Ei" must not match inside "einer"
    steps = ["In einer Pfanne erhitzen.", "Ein Ei aufschlagen."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Ei")])
    assert result == {1: 1}


def test_unmatched_ingredient_absent_from_result():
    steps = ["Die Nüsse rösten."]
    result = zi.map_ingredients_to_steps(steps, [(1, "Pfeffer"), (2, "Nüsse")])
    assert result == {2: 0}


def test_multiple_ingredients():
    steps = [
        "Zunächst die Pecannüsse in einer Pfanne rösten.",
        "Aprikosen halbieren, mit Honig und Balsamico schmoren.",
        "Den Halloumi braten und mit Meersalz bestreuen.",
    ]
    foods = [(1, "Pecannüsse"), (2, "Aprikosen"), (3, "Honig"), (4, "Halloumi"), (5, "Meersalz"), (6, "Olivenöl")]
    result = zi.map_ingredients_to_steps(steps, foods)
    assert result == {1: 0, 2: 1, 3: 1, 4: 2, 5: 2}


def test_build_step_assignments_disjoint_and_complete():
    # unmatched ingredients land on step 0; every ingredient appears exactly once
    steps = ["Nüsse rösten.", "Honig einrühren."]
    ingredients = [
        {"id": 100, "food": {"name": "Nüsse"}},
        {"id": 101, "food": {"name": "Honig"}},
        {"id": 102, "food": {"name": "Safran"}},
    ]
    assignments, unmatched = zi.build_step_assignments(steps, ingredients)
    assert [i["id"] for i in assignments[0]] == [100, 102]
    assert [i["id"] for i in assignments[1]] == [101]
    assert unmatched == ["Safran"]
