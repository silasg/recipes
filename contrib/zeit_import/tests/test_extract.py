"""Tests for JSON-LD + article-body extraction from ZEIT Wochenmarkt pages."""
import pytest

import zeit_import as zi

PAGE_URL = "https://www.zeit.de/zeit-magazin/2026/28/halloumi-gebraten-aprikosen-honig-pecannuesse"


class TestSingleRecipe:
    def test_extracts_one_recipe(self, load_fixture):
        recipes = zi.extract_recipes(load_fixture("halloumi.html"), PAGE_URL)
        assert len(recipes) == 1

    def test_basic_fields(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("halloumi.html"), PAGE_URL)
        assert r["name"] == "Halloumi mit geschmorten Aprikosen"
        assert r["@type"] == "Recipe"
        assert len(r["recipeIngredient"]) == 12
        assert r["recipeYield"] == "4 Portionen"
        assert r["totalTime"] == "PT60M"
        assert r["image"]["url"].startswith("https://img.zeit.de/")

    def test_url_key_is_stripped(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("halloumi.html"), PAGE_URL)
        assert "url" not in r  # url triggers Tandoor's host-specific re-scrape -> empty ingredients

    def test_instructions_injected_as_howtosteps(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("halloumi.html"), PAGE_URL)
        steps = r["recipeInstructions"]
        assert len(steps) == 4
        assert all(s["@type"] == "HowToStep" for s in steps)
        assert steps[0]["text"].startswith("Zunächst die Nüsse zubereiten")
        assert steps[1]["text"].startswith("Aprikosen halbieren oder vierteln")
        assert steps[2]["text"].startswith("Den Halloumi von beiden Seiten")
        assert steps[3]["text"].startswith("Den Halloumi auf Tellern verteilen")

    def test_intro_paragraph_excluded(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("halloumi.html"), PAGE_URL)
        texts = " ".join(s["text"] for s in r["recipeInstructions"])
        assert "Restaurant in London" not in texts

    def test_hidden_paywall_duplicate_excluded(self, load_fixture):
        # the trailing <div ... data-paywall hidden> repeats the intro paragraph
        (r,) = zi.extract_recipes(load_fixture("halloumi.html"), PAGE_URL)
        texts = [s["text"] for s in r["recipeInstructions"]]
        assert not any("Restaurant in London" in t for t in texts)
        assert len(texts) == 4


class TestMultiRecipe:
    def test_extracts_two_recipes(self, load_fixture):
        recipes = zi.extract_recipes(load_fixture("quitte_multi.html"), "https://www.zeit.de/2011/40/Wochenmarkt-Quitte")
        assert [r["name"] for r in recipes] == ["Hähnchen mit Quitten", "Gebackene Quitten"]

    def test_paragraphs_split_by_h2_position(self, load_fixture):
        r1, r2 = zi.extract_recipes(load_fixture("quitte_multi.html"), "https://www.zeit.de/2011/40/Wochenmarkt-Quitte")
        assert len(r1["recipeInstructions"]) == 1
        assert r1["recipeInstructions"][0]["text"].startswith("Quitten waschen, putzen")
        assert len(r2["recipeInstructions"]) == 1
        assert r2["recipeInstructions"][0]["text"].startswith("Quitten waschen. Im Ganzen")

    def test_intro_excluded_from_both(self, load_fixture):
        r1, r2 = zi.extract_recipes(load_fixture("quitte_multi.html"), "https://www.zeit.de/2011/40/Wochenmarkt-Quitte")
        for r in (r1, r2):
            joined = " ".join(s["text"] for s in r["recipeInstructions"])
            assert "Claudia Roden" not in joined
            assert "arabische Welt" not in joined


class TestGenericName:
    def test_name_falls_back_to_h2(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("generic_name.html"), PAGE_URL)
        assert r["name"] == "Halloumi mit geschmorten Aprikosen"

    def test_credit_paragraphs_stripped(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("generic_name.html"), PAGE_URL)
        texts = [s["text"] for s in r["recipeInstructions"]]
        assert len(texts) == 4
        assert not any("Übersetzung" in t for t in texts)
        assert not any("©" in t for t in texts)
        assert not any("Guardian News & Media" in t for t in texts)

    def test_unicode_dash_normalized_in_instructions(self, load_fixture):
        (r,) = zi.extract_recipes(load_fixture("generic_name.html"), PAGE_URL)
        assert "2-3 Minuten" in r["recipeInstructions"][0]["text"]


class TestNoSubheadingClass:
    def test_h2_matched_by_recipe_name(self, load_fixture):
        # 3 of 41 sampled pages have recipe h2s without the article__subheading class
        (r,) = zi.extract_recipes(load_fixture("no_subheading.html"), PAGE_URL)
        assert r["name"] == "Halloumi mit geschmorten Aprikosen"
        assert len(r["recipeInstructions"]) == 4
        assert r["recipeInstructions"][0]["text"].startswith("Zunächst die Nüsse")


class TestNormalizeDashes:
    @pytest.mark.parametrize("dash", ["‐", "‑", "‒", "–", "—", "−"])
    def test_all_unicode_dashes(self, dash):
        assert zi.normalize_dashes(f"2 {dash} 3 Minuten rösten") == "2-3 Minuten rösten"

    @pytest.mark.parametrize("unit", ["Min", "Minuten", "Sek", "Sekunden", "Std", "Stunden", "h", "min"])
    def test_all_duration_units(self, unit):
        assert zi.normalize_dashes(f"10–12 {unit}") == f"10-12 {unit}"

    def test_no_space_variant(self):
        assert zi.normalize_dashes("2–3 Minuten") == "2-3 Minuten"

    def test_non_duration_range_unchanged(self):
        assert zi.normalize_dashes("die Jahre 1990–2000 waren") == "die Jahre 1990–2000 waren"

    def test_ascii_dash_unchanged(self):
        assert zi.normalize_dashes("2-3 Minuten") == "2-3 Minuten"


class TestMalformedJsonLd:
    # seen live on zeit-magazin/2026/26 (quiche-fenchel-edamame): ZEIT's template emits
    # a stray leading comma when the first ingredient slot is empty
    def test_leading_comma_in_array_is_repaired(self, load_fixture):
        html = load_fixture("halloumi.html").replace('"recipeIngredient": ["', '"recipeIngredient": [,"')
        (r,) = zi.extract_recipes(html, PAGE_URL)
        assert r["name"] == "Halloumi mit geschmorten Aprikosen"
        assert len(r["recipeIngredient"]) == 12
        assert "" not in r["recipeIngredient"]

    def test_trailing_comma_in_array_is_repaired(self, load_fixture):
        html = load_fixture("halloumi.html").replace('"recipeYield"', '"dummy": [1,], "recipeYield"')
        (r,) = zi.extract_recipes(html, PAGE_URL)
        assert len(r["recipeIngredient"]) == 12


class TestCreditParagraph:
    @pytest.mark.parametrize("text,expected", [
        ("Übersetzung: Anna Schmidt", True),
        ("© 2026 Guardian News & Media Ltd.", True),
        ("Das Rezept stammt von Guardian News & Media.", True),
        ("Die Aprikosen halbieren und entsteinen.", False),
    ])
    def test_detection(self, text, expected):
        assert zi.is_credit_paragraph(text) is expected


class TestParseServings:
    def test_portionen(self):
        assert zi.parse_servings("4 Portionen") == (4, "Portionen")

    def test_personen(self):
        assert zi.parse_servings("Für 6 Personen") == (6, "Personen")

    def test_empty(self):
        assert zi.parse_servings("") == (None, "")

    def test_none(self):
        assert zi.parse_servings(None) == (None, "")
