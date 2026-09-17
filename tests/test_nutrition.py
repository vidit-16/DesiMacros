"""Nutrition layer.

Offline: only the local IFCT table and pure functions, no network.

These exist because a corrupted regex once disabled portion matching entirely
without failing anything — every lookup silently fell back to a 100g default.
"""

from __future__ import annotations

import pytest

from app.services.nutrition import (
    _is_plausible_match,
    _piece_weight,
    _usda_nutrients,
    lookup_nutrition,
    search_ifct,
    unit_to_grams,
)

# ── portion weights ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "food,grams",
    [
        ("roti", 40),
        ("dosa", 100),
        ("papad", 13),
        ("tandoori roti", 40),       # matches inside a longer name
        ("boiled eggs", 55),         # tolerates a plural
        ("2 rotis", 40),             # plural inside a longer string
        ("aloo paratha", 100),       # longest key wins over "paratha"
        ("something unheard of", None),
    ],
)
def test_piece_weight(food, grams):
    assert _piece_weight(food) == grams


def test_piece_weight_of_empty_name_is_unknown():
    assert _piece_weight("") is None
    assert _piece_weight(None) is None


# ── unit conversion ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "unit,quantity,food,grams",
    [
        ("piece", 2, "roti", 80),
        ("katori", 1, "dal tadka", 150),   # dal weighs about what water does
        ("katori", 1, "rice", 98.75),      # cooked rice does not: 158 g per cup
        ("cup", 1, "cornflakes", 28),
        ("tbsp", 1, "ghee", 12.8125),
        ("glass", 1, "milk", 250),
        ("can", 1, "coke", 330),
        ("serving", 1, "curd", 150),       # a serving is a katori, not 100 g
        ("serving", 1, "roti", 40),        # unless the food has a piece weight
        ("plate", 1, "biryani", 300),      # a plate is a plate, not 100g
        ("g", 150, "chicken", 150),
        ("blorp", 1, "mystery", 100),      # unknown unit falls back to 100g
        ("roti", 3, "", 120),              # the unit itself names the food
        ("idlis", 3, "", 120),             # ...in the plural, which no unit table lists
    ],
)
def test_unit_to_grams(unit, quantity, food, grams):
    assert unit_to_grams(unit, quantity, food) == grams


# ── IFCT lookup ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query,expected",
    [
        ("dahi", "curd"),                        # alias
        ("maggi noodles", "maggi"),              # stored name inside the query
        ("roti", "roti"),                        # not paratha
        ("masala dosa", "masala dosa"),          # its own dish, not "dosa"
        ("paneer", "paneer"),                    # not palak paneer
        ("sandwich", "veg sandwich"),
        ("paneer sandwich", "paneer sandwich"),
    ],
)
def test_search_ifct(query, expected):
    assert search_ifct(query).food_name == expected


def test_unknown_food_is_not_found():
    assert search_ifct("unknown food xyz") is None


def test_blank_query_is_not_found():
    assert search_ifct("") is None
    assert search_ifct(None) is None


# ── lookup order: the regression this suite was extended for ─────────────────
#
# Aliases used to be consulted *after* substring matching against dish names,
# so a curated alias lost to any longer dish name containing the same word.
# "rice" resolved to jeera rice and "paratha" to aloo paratha — wrong dish and
# wrong macros on two of the most commonly logged foods.


@pytest.mark.parametrize(
    "query,expected",
    [
        ("rice", "basmati rice"),      # not jeera rice
        ("chawal", "basmati rice"),
        ("paratha", "plain paratha"),  # not aloo paratha
        ("dal", "dal tadka"),          # not chana dal or moong dal
        ("egg", "boiled egg"),
        ("chai", "tea with milk"),
        ("yogurt", "curd"),
        ("oatmeal", "cooked oats"),    # oatmeal is eaten cooked, not as dry oats
        ("oats", "oats"),
        ("bread slice", "white bread"),
    ],
)
def test_curated_aliases_beat_substring_matches(query, expected):
    assert search_ifct(query).food_name == expected


def test_exact_names_still_win_over_their_own_aliases():
    for name in ("jeera rice", "aloo paratha", "chana dal", "basmati rice"):
        assert search_ifct(name).food_name == name


def test_lookup_does_not_depend_on_tie_break_luck():
    """Three dal dishes tie at nine characters; the alias decides, not rowid."""
    assert search_ifct("dal").food_name == "dal tadka"


# ── scaling ──────────────────────────────────────────────────────────────────


def test_macros_scale_with_portion_size(no_usda):
    result = lookup_nutrition("roti", 2, "piece")
    assert result["source"] == "ifct"
    assert result["grams"] == 80
    assert result["calories"] == pytest.approx(211.2, abs=0.5)


def test_plain_rice_uses_basmati_values(no_usda):
    """The bug in user-visible terms: a katori of rice, not of jeera rice."""
    result = lookup_nutrition("rice", 1, "katori")
    assert result["food_name"] == "basmati rice"
    assert result["grams"] == pytest.approx(98.75)
    assert result["calories"] == pytest.approx(128.4, abs=0.5)
    assert result["fat"] == pytest.approx(0.2, abs=0.1)


def test_one_idli_is_about_58_kcal(no_usda):
    """The table stored one idli's energy as the per-100 g value."""
    result = lookup_nutrition("idli", 1, "piece")
    assert result["grams"] == 40
    assert result["calories"] == pytest.approx(58, abs=1)


def test_oats_made_with_water_are_not_dry_oats(no_usda):
    cooked = lookup_nutrition("cooked oats", 1, "cup")
    dry = lookup_nutrition("oats", 1, "cup")
    assert cooked["calories"] == pytest.approx(166, abs=2)    # 234 g at 71 kcal/100 g
    assert dry["calories"] == pytest.approx(315, abs=2)       # 81 g of dry oats


def test_unknown_food_returns_zeros_and_a_flag(no_usda):
    result = lookup_nutrition("unknown food xyz", 1, "piece")
    assert result["source"] == "not_found"
    for macro in ("calories", "protein", "carbs", "fat", "fiber"):
        assert result[macro] == 0.0
    assert result["grams"] == 100      # still reports the portion it assumed


def test_the_reported_failure_end_to_end(no_usda):
    result = lookup_nutrition("paneer sandwich", 3.5, "piece")
    assert result["source"] == "ifct"
    assert result["food_name"] == "paneer sandwich"
    assert result["grams"] == 490.0


# ── USDA relevance guard ─────────────────────────────────────────────────────
#
# USDA answers every query with something, so unrelated matches must be refused.


@pytest.mark.parametrize(
    "description,query,plausible",
    [
        ("Oats (Includes foods for USDA's Food Distribution Program)", "unknown food xyz", False),
        ("Soybean curd", "curd", True),
        ("CHICKEN BREAST", "chicken breast", True),
        ("Almonds, raw", "almond", True),
        ("Palak Paneer", "paneer sandwich", False),   # logged 3.5 sandwiches as palak paneer
        ("Chicken Biryani", "mutton biryani", False),
    ],
)
def test_is_plausible_match(description, query, plausible):
    assert _is_plausible_match(description, query) is plausible


def test_empty_query_is_never_plausible():
    assert _is_plausible_match("Anything At All", "") is False


# ── USDA energy units ────────────────────────────────────────────────────────


def test_energy_is_read_in_kcal_not_kilojoules():
    """FDC reports Energy twice. Taking the kJ row inflates calories 4.184x."""
    nutrients = _usda_nutrients(
        [
            {"nutrientName": "Energy", "unitName": "KCAL", "value": 130},
            {"nutrientName": "Energy", "unitName": "kJ", "value": 544},
            {"nutrientName": "Protein", "unitName": "G", "value": 2.7},
        ]
    )
    assert nutrients["Energy"] == 130
    assert nutrients["Protein"] == 2.7


def test_kilojoule_only_energy_is_dropped_rather_than_misread():
    nutrients = _usda_nutrients([{"nutrientName": "Energy", "unitName": "kJ", "value": 544}])
    assert "Energy" not in nutrients


def test_nutrient_entries_without_a_name_are_skipped():
    assert _usda_nutrients([{"unitName": "KCAL", "value": 99}]) == {}


def test_usda_lookup_is_skipped_without_a_key(monkeypatch):
    from app.services import nutrition

    monkeypatch.setattr(nutrition.settings, "usda_api_key", "")
    assert nutrition.search_usda("anything") is None


# ── every macro, not just calories ───────────────────────────────────────────
#
# Earlier tests checked calories and fat only, so a wrong column or a wrong
# operator on protein, carbs or fiber passed unnoticed.


def test_ifct_row_maps_to_the_right_fields():
    roti = search_ifct("roti")
    assert (roti.calories, roti.protein, roti.carbs, roti.fat, roti.fiber) == (264, 8.1, 51.7, 3.7, 3.4)
    assert roti.source == "ifct"


def test_every_macro_scales_with_quantity(no_usda):
    result = lookup_nutrition("roti", 3, "piece")     # 120 g, so a scale of 1.2
    assert result["grams"] == 120
    assert result["calories"] == 316.8
    assert result["protein"] == 9.7                  # 9.72, rounded to one place
    assert result["carbs"] == 62.0                   # 62.04
    assert result["fat"] == 4.4                      # 4.44
    assert result["fiber"] == 4.1                    # 4.08


def test_calories_are_rounded_to_one_decimal(no_usda):
    assert lookup_nutrition("apple", 1, "piece")["calories"] == 94.6   # 182 g x 0.52 = 94.64


# ── portion edge cases ───────────────────────────────────────────────────────


def test_exact_table_weights_beat_an_estimate():
    assert unit_to_grams("piece", 1, "roti", piece_grams=99) == 40
    assert unit_to_grams("katori", 1, "rice", cup_grams=240) == pytest.approx(98.75)


def test_estimate_weights_fill_gaps_the_tables_leave():
    assert unit_to_grams("blorp", 2, "mystery", piece_grams=50) == 100
    assert unit_to_grams("katori", 2, "mystery", cup_grams=120) == 150


def test_a_slice_of_bread_is_30_grams():
    assert unit_to_grams("slices", 2, "white bread") == 60
    assert unit_to_grams("slice", 2, "bread", piece_grams=50) == 60


def test_a_slice_of_an_unlisted_food_uses_its_estimated_piece():
    """"2 slices of cheese pizza" used to be 60 g, the weight of two bread slices."""
    assert unit_to_grams("slices", 2, "cheese pizza", piece_grams=110) == 220
    assert unit_to_grams("slice", 1, "cheese pizza") == 30   # no estimate: bread default


# ── USDA word matching ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "description,query,plausible",
    [
        ("Egg, whole, boiled", "an egg", True),       # two-letter words are ignored
        ("Peas, green", "pea", True),                 # four-letter plural is stripped
        ("Dal, rice", "dal with rice", True),         # stop words are ignored
    ],
)
def test_word_matching_edges(description, query, plausible):
    assert _is_plausible_match(description, query) is plausible


# ── USDA request, with the network faked ─────────────────────────────────────


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def usda(monkeypatch):
    from app.services import nutrition

    calls = []

    def install(body=None, error=None):
        def fake_get(url, params, timeout):
            calls.append({"url": url, "params": params, "timeout": timeout})
            if error:
                raise error
            return _FakeResponse(body)

        monkeypatch.setattr(nutrition.settings, "usda_api_key", "test-key")
        monkeypatch.setattr(nutrition.httpx, "get", fake_get)
        return calls

    return install


CHICKPEAS = {
    "description": "Chickpeas, boiled",
    "foodNutrients": [
        {"nutrientName": "Energy", "unitName": "KCAL", "value": 164},
        {"nutrientName": "Protein", "unitName": "G", "value": 8.9},
        {"nutrientName": "Carbohydrate, by difference", "unitName": "G", "value": 27.4},
        {"nutrientName": "Total lipid (fat)", "unitName": "G", "value": 2.6},
    ],
}


def test_usda_result_is_read_into_per_100g_values(usda):
    from app.services import nutrition

    calls = usda({"foods": [CHICKPEAS, {"description": "Something else"}]})
    result = nutrition.search_usda("chickpeas")

    assert (result.food_name, result.source) == ("Chickpeas, boiled", "usda")
    assert (result.calories, result.protein, result.carbs, result.fat) == (164, 8.9, 27.4, 2.6)
    assert result.fiber == 0          # absent from the response, not an error
    assert calls[0]["params"]["query"] == "chickpeas"
    assert calls[0]["params"]["pageSize"] == 1
    assert calls[0]["timeout"] == 5.0


def test_missing_nutrients_default_to_zero(usda):
    from app.services import nutrition

    usda({"foods": [{"description": "Chickpeas", "foodNutrients": []}]})
    result = nutrition.search_usda("chickpeas")
    assert (result.calories, result.protein, result.carbs, result.fat, result.fiber) == (0, 0, 0, 0, 0)


def test_nutrient_without_a_value_counts_as_zero():
    assert _usda_nutrients([{"nutrientName": "Protein", "unitName": "G"}]) == {"Protein": 0}


@pytest.mark.parametrize("body", [{"foods": []}, {}, {"foods": [{"description": "Palak Paneer"}]}])
def test_usda_misses_return_none(usda, body):
    from app.services import nutrition

    usda(body)
    assert nutrition.search_usda("paneer sandwich") is None


def test_usda_outage_returns_none(usda):
    from app.services import nutrition

    usda(error=RuntimeError("network down"))
    assert nutrition.search_usda("chickpeas") is None
