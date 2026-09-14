"""TDEE and macro goals.

Pure arithmetic with no dependencies, and previously untested. The values here
are computed from the Mifflin-St Jeor equation by hand, so a change to the
formula or the constants shows up as a failure rather than as quietly different
targets for every user.
"""

from __future__ import annotations

import pytest

from app.services.tdee import (
    ACTIVITY_MULTIPLIERS,
    GOAL_ADJUSTMENTS,
    calculate_bmr,
    calculate_goals,
    calculate_tdee,
)

# 70kg, 175cm, 22yo male: 10*70 + 6.25*175 - 5*22 + 5 = 1688.75
MALE = dict(weight_kg=70, height_cm=175, age=22, gender="male")
# 55kg, 160cm, 22yo female: 10*55 + 6.25*160 - 5*22 - 161 = 1279.0
FEMALE = dict(weight_kg=55, height_cm=160, age=22, gender="female")


# ── BMR ──────────────────────────────────────────────────────────────────────


def test_bmr_male():
    assert calculate_bmr(**MALE) == pytest.approx(1688.75)


def test_bmr_female():
    assert calculate_bmr(**FEMALE) == pytest.approx(1279.0)


def test_the_gender_term_is_the_only_difference():
    """Mifflin-St Jeor differs by a constant: +5 against -161."""
    shared = dict(weight_kg=70, height_cm=175, age=22)
    male = calculate_bmr(**shared, gender="male")
    female = calculate_bmr(**shared, gender="female")
    assert male - female == pytest.approx(166)


def test_an_unrecognised_gender_uses_the_female_constant():
    """Documents current behaviour: anything not "male" takes the else branch."""
    shared = dict(weight_kg=70, height_cm=175, age=22)
    assert calculate_bmr(**shared, gender="nonbinary") == calculate_bmr(**shared, gender="female")


@pytest.mark.parametrize(
    "field,delta,expected",
    [("weight_kg", 1, 10), ("height_cm", 1, 6.25), ("age", 1, -5)],
)
def test_each_term_moves_bmr_by_its_coefficient(field, delta, expected):
    base = calculate_bmr(**MALE)
    moved = calculate_bmr(**{**MALE, field: MALE[field] + delta})
    assert moved - base == pytest.approx(expected)


# ── TDEE ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("level,multiplier", sorted(ACTIVITY_MULTIPLIERS.items()))
def test_tdee_applies_the_activity_multiplier(level, multiplier):
    assert calculate_tdee(**MALE, activity_level=level) == round(1688.75 * multiplier)


def test_multipliers_increase_with_activity():
    order = ["sedentary", "light", "moderate", "active", "very_active"]
    values = [ACTIVITY_MULTIPLIERS[level] for level in order]
    assert values == sorted(values)


def test_an_unknown_activity_level_falls_back_to_moderate():
    assert calculate_tdee(**MALE, activity_level="teleporting") == calculate_tdee(
        **MALE, activity_level="moderate"
    )


# ── goals ────────────────────────────────────────────────────────────────────


def test_goal_adjustment_shifts_the_calorie_target():
    maintain = calculate_goals(**MALE, activity_level="moderate", goal_type="maintain")
    cut = calculate_goals(**MALE, activity_level="moderate", goal_type="cut")
    assert cut["target_calories"] == maintain["target_calories"] - 500


@pytest.mark.parametrize("goal,adjustment", sorted(GOAL_ADJUSTMENTS.items()))
def test_every_goal_matches_its_declared_adjustment(goal, adjustment):
    result = calculate_goals(**MALE, activity_level="moderate", goal_type=goal)
    assert result["target_calories"] == result["tdee"] + adjustment
    assert result["adjustment"] == adjustment


def test_an_unknown_goal_is_treated_as_maintenance():
    result = calculate_goals(**MALE, activity_level="moderate", goal_type="vibes")
    assert result["target_calories"] == result["tdee"]


def test_protein_scales_with_bodyweight():
    result = calculate_goals(**MALE, activity_level="moderate", goal_type="maintain")
    assert result["protein_g"] == round(70 * 1.8)


def test_fat_is_a_quarter_of_calories():
    result = calculate_goals(**MALE, activity_level="moderate", goal_type="maintain")
    assert result["fat_g"] == round(result["target_calories"] * 0.25 / 9)


def test_macros_add_back_up_to_the_calorie_target():
    """Carbs are the remainder, so the split has to reconcile within rounding."""
    for goal in GOAL_ADJUSTMENTS:
        result = calculate_goals(**MALE, activity_level="moderate", goal_type=goal)
        total = result["protein_g"] * 4 + result["carbs_g"] * 4 + result["fat_g"] * 9
        assert total == pytest.approx(result["target_calories"], abs=4)


def test_bmi_is_reported_from_height_and_weight():
    result = calculate_goals(**MALE, activity_level="moderate", goal_type="maintain")
    assert result["bmi"] == pytest.approx(70 / 1.75**2, abs=0.05)


def test_reported_bmr_matches_the_bmr_function():
    """calculate_goals derives bmr by dividing back out of a rounded tdee."""
    result = calculate_goals(**MALE, activity_level="moderate", goal_type="maintain")
    assert result["bmr"] == pytest.approx(calculate_bmr(**MALE), abs=1)


def test_the_result_carries_every_field_the_api_returns():
    result = calculate_goals(**FEMALE, activity_level="light", goal_type="mild_cut")
    assert set(result) == {
        "bmr", "tdee", "target_calories", "protein_g",
        "carbs_g", "fat_g", "bmi", "adjustment", "goal_type",
    }
    assert result["goal_type"] == "mild_cut"


def test_labels_cover_every_option():
    """A goal or activity level with no label would render blank in the UI."""
    from app.services.tdee import ACTIVITY_LABELS, GOAL_LABELS

    assert set(ACTIVITY_LABELS) == set(ACTIVITY_MULTIPLIERS)
    assert set(GOAL_LABELS) == set(GOAL_ADJUSTMENTS)
