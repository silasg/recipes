"""Tests for the parsed-recipe -> persistable-recipe transformation."""
import zeit_import as zi

PAGE_URL = "https://www.zeit.de/zeit-magazin/2026/28/halloumi-gebraten-aprikosen-honig-pecannuesse"
ZEIT_KW = {"id": 42, "name": "ZEIT Magazin"}


def parsed_recipe():
    return {
        "name": "Halloumi mit geschmorten Aprikosen",
        "servings": "4 Portionen",
        "servings_text": "",
        "source_url": "",
        "working_time": 60,
        "waiting_time": 0,
        "keywords": [
            {"name": "Sommer", "import_keyword": True},
            {"name": "zeit.de", "import_keyword": False},
            {"name": "noise"},
        ],
        "steps": [
            {
                "order": None,
                "instruction": "Zunächst die Nüsse zubereiten.",
                "ingredients": [
                    {"order": None, "amount": 200.0, "food": {"name": "Aprikosen"}, "unit": {"name": "g"}, "note": "reif"},
                    {"order": None, "amount": 1.0, "food": {"name": "Honig"}, "unit": {"name": "EL"}, "note": ""},
                ],
            },
            {"order": None, "instruction": "Den Halloumi braten.", "ingredients": []},
        ],
    }


class TestKeywords:
    def test_non_import_keywords_dropped(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        names = [k["name"] for k in r["keywords"]]
        assert "zeit.de" not in names
        assert "noise" not in names

    def test_import_keywords_kept(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        assert {"name": "Sommer", "import_keyword": True} in r["keywords"]

    def test_zeit_magazin_keyword_appended(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        assert r["keywords"][-1] == ZEIT_KW

    def test_zeit_keyword_without_id(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, {"name": "ZEIT Magazin"})
        assert r["keywords"][-1] == {"name": "ZEIT Magazin"}


class TestOrders:
    def test_step_orders_sequential(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        assert [s["order"] for s in r["steps"]] == [0, 1]

    def test_ingredient_orders_sequential_per_step(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        assert [i["order"] for i in r["steps"][0]["ingredients"]] == [0, 1]


class TestSourceUrlAndServings:
    def test_source_url_set(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        assert r["source_url"] == PAGE_URL

    def test_string_servings_parsed_to_int(self):
        r = zi.transform_recipe(parsed_recipe(), PAGE_URL, ZEIT_KW)
        assert r["servings"] == 4
        assert r["servings_text"] == "Portionen"

    def test_int_servings_kept(self):
        p = parsed_recipe()
        p["servings"] = 6
        p["servings_text"] = "Stück"
        r = zi.transform_recipe(p, PAGE_URL, ZEIT_KW)
        assert r["servings"] == 6
        assert r["servings_text"] == "Stück"

    def test_missing_servings_falls_back_to_recipe_yield(self):
        p = parsed_recipe()
        p["servings"] = None
        r = zi.transform_recipe(p, PAGE_URL, ZEIT_KW, recipe_yield="2 Portionen")
        assert r["servings"] == 2
        assert r["servings_text"] == "Portionen"

    def test_unparseable_servings_defaults_to_one(self):
        p = parsed_recipe()
        p["servings"] = None
        r = zi.transform_recipe(p, PAGE_URL, ZEIT_KW, recipe_yield=None)
        assert r["servings"] == 1
