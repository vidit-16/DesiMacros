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
        ("katori", 1, "dal tadka", 150),
        ("plate", 1, "biryani", 300),      # a plate is a plate, not 100g
        ("g", 150, "chicken", 150),
        ("blorp", 1, "mystery", 100),      # unknown unit falls back to 100g
        ("roti", 3, "", 120),              # the unit itself names the food
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
        ("oatmeal", "oats"),
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
    assert result["grams"] == 150
    assert result["calories"] == pytest.approx(195.0, abs=0.5)
    assert result["fat"] == pytest.approx(0.3, abs=0.1)


def test_unknown_food_returns_zeros_and_a_flag(no_usda):
    result = lookup_nutrition("unknown food xyz", 1, "piece")
    assert result["source"] == "not_found"
    assert result["calories"] == 0.0
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
