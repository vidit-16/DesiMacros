"""Model estimates for foods neither database knows.

All offline: the model call is replaced, so these test the plumbing, the
plausibility checks and the cache, not the model's nutrition knowledge (that is
what evaluation/benchmark.py measures).
"""

from __future__ import annotations

import pytest

from app.services import food_estimator, nutrition
from app.services.food_estimator import FoodEstimate, validate

SAMOSA = {"recognised": True, "calories": 262, "protein": 4.5, "carbs": 28, "fat": 15,
          "fiber": 2, "grams_per_piece": 90, "grams_per_cup": None}


# ── plausibility ─────────────────────────────────────────────────────────────


def test_consistent_estimate_is_accepted():
    estimate = validate(SAMOSA)
    assert estimate.calories == 262
    assert estimate.grams_per_piece == 90
    assert estimate.grams_per_cup is None


@pytest.mark.parametrize(
    "change",
    [
        {"recognised": False},                       # not a food
        {"calories": 0},                             # nothing to log
        {"calories": 1200},                          # denser than pure fat
        {"protein": -1},
        {"protein": 60, "carbs": 40, "fat": 20},     # more than 100 g per 100 g
        {"calories": 600},                           # disagrees with its own macros
        {"calories": "lots"},
    ],
)
def test_implausible_estimates_are_rejected(change):
    assert validate({**SAMOSA, **change}) is None


def _consistent(calories: float, protein: float = 0, carbs: float = 0, fat: float = 0) -> dict:
    return {"recognised": True, "calories": calories, "protein": protein, "carbs": carbs,
            "fat": fat, "fiber": 0}


def test_energy_limit_is_inclusive():
    """Pure fat is 900 kcal per 100 g: allowed. Anything above it is not."""
    assert validate(_consistent(900, fat=100)) is not None
    assert validate(_consistent(900.5, fat=100)) is None


def test_macro_total_limit_is_inclusive():
    assert validate(_consistent(400, protein=50, carbs=50)) is not None
    assert validate(_consistent(404, protein=50, carbs=50.5, fat=0.01)) is None


def test_energy_must_roughly_match_its_macros():
    # 100 g carbs is 400 kcal; the allowed gap is 35% of stated energy plus 10.
    assert validate(_consistent(300, carbs=75)) is not None       # exact
    assert validate(_consistent(200, carbs=67.5)) is not None     # gap 70 <= 80
    assert validate(_consistent(200, carbs=70.1)) is None         # gap 80.4 > 80
    assert validate(_consistent(600, carbs=100)) is not None      # gap 200 <= 220
    assert validate(_consistent(200, carbs=30.9)) is not None     # gap 76.4 <= 80, under
    assert validate(_consistent(200, carbs=29.9)) is None         # gap 80.4 > 80, under


def test_missing_nutrients_count_as_zero():
    estimate = validate({"recognised": True, "calories": 40, "carbs": 10})
    assert estimate.protein == 0 and estimate.fat == 0 and estimate.fiber == 0


def test_portion_weights_are_kept_when_positive():
    estimate = validate({**SAMOSA, "grams_per_piece": 0.5, "grams_per_cup": 1})
    assert estimate.grams_per_piece == 0.5
    assert estimate.grams_per_cup == 1


def test_non_positive_portion_weights_are_ignored():
    estimate = validate({**SAMOSA, "grams_per_piece": 0, "grams_per_cup": "n/a"})
    assert estimate.grams_per_piece is None
    assert estimate.grams_per_cup is None


# ── cache ────────────────────────────────────────────────────────────────────


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(food_estimator.get_settings(), "groq_api_key", "test-key")


def test_estimates_are_cached_per_food(tmp_path, monkeypatch, with_key):
    calls = []

    def ask(food_name):
        calls.append(food_name)
        return SAMOSA

    monkeypatch.setattr(food_estimator, "_ask_model", ask)
    db = str(tmp_path / "ifct.db")
    first = food_estimator.estimate_food("Samosa", db)
    second = food_estimator.estimate_food("samosa ", db)
    assert first == second
    assert calls == ["samosa"]      # asked once, under the normalised name


def test_failed_calls_are_not_cached(tmp_path, monkeypatch, with_key):
    def down(food_name):
        raise RuntimeError("outage")

    monkeypatch.setattr(food_estimator, "_ask_model", down)
    db = str(tmp_path / "ifct.db")
    assert food_estimator.estimate_food("samosa", db) is None

    monkeypatch.setattr(food_estimator, "_ask_model", lambda name: SAMOSA)
    assert food_estimator.estimate_food("samosa", db).calories == 262


def test_no_key_means_no_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(food_estimator.get_settings(), "groq_api_key", "")
    monkeypatch.setattr(food_estimator, "_ask_model", lambda name: pytest.fail("called the model"))
    assert food_estimator.estimate_food("samosa", str(tmp_path / "ifct.db")) is None


# ── lookup integration ───────────────────────────────────────────────────────


@pytest.fixture
def estimates(monkeypatch):
    """Route lookups past IFCT and USDA to a fixed table of estimates."""
    table = {}
    monkeypatch.setattr(nutrition, "search_usda", lambda name: None)
    monkeypatch.setattr(nutrition, "estimate_food", lambda name: table.get(name))
    return table


def test_unknown_food_is_estimated_not_zero(estimates):
    estimates["vada pav"] = FoodEstimate(210, 5, 30, 7, 2, grams_per_piece=150, grams_per_cup=None)
    result = nutrition.lookup_nutrition("vada pav", 2, "piece")
    assert result["source"] == "estimate"
    assert result["grams"] == 300
    assert result["calories"] == pytest.approx(630)


def test_estimate_portion_beats_a_partial_name_match(estimates):
    """"vada pav" contains "vada"; a 45 g vada is not a vada pav."""
    estimates["vada pav"] = FoodEstimate(210, 5, 30, 7, 2, grams_per_piece=150, grams_per_cup=None)
    assert nutrition.lookup_nutrition("vada pav", 1, "piece")["grams"] == 150


def test_estimate_volume_uses_its_own_density(estimates):
    estimates["misal"] = FoodEstimate(120, 6, 14, 4, 4, grams_per_piece=None, grams_per_cup=220)
    assert nutrition.lookup_nutrition("misal", 1, "katori")["grams"] == pytest.approx(137.5)


def test_foods_the_model_cannot_estimate_stay_flagged(estimates):
    result = nutrition.lookup_nutrition("asdfgh", 1, "piece")
    assert result["source"] == "not_found"
    assert result["calories"] == 0.0


def test_estimates_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(nutrition, "ESTIMATES_ENABLED", False)
    assert nutrition.estimate_food("samosa") is None
