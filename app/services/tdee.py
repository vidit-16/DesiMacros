"""
TDEE Calculator
---------------
Mifflin-St Jeor equation (most accurate for general population).
Calculates BMR → TDEE → adjusted calories based on goal.
Also calculates macro split.
"""

ACTIVITY_MULTIPLIERS = {
    "sedentary":   1.2,   # desk job, no exercise
    "light":       1.375, # light exercise 1-3 days/week
    "moderate":    1.55,  # moderate exercise 3-5 days/week (typical college student)
    "active":      1.725, # hard exercise 6-7 days/week
    "very_active": 1.9,   # physical job + hard training
}

ACTIVITY_LABELS = {
    "sedentary":   "Sedentary (no exercise)",
    "light":       "Light (1-3 days/week)",
    "moderate":    "Moderate (3-5 days/week)",
    "active":      "Active (6-7 days/week)",
    "very_active": "Very active (athlete/physical job)",
}

GOAL_ADJUSTMENTS = {
    "cut":      -500,   # 0.5kg/week loss
    "mild_cut": -250,   # 0.25kg/week loss
    "maintain": 0,
    "mild_bulk": 250,   # 0.25kg/week gain
    "bulk":     500,    # 0.5kg/week gain
}

GOAL_LABELS = {
    "cut":       "Cut (lose ~0.5kg/week)",
    "mild_cut":  "Mild cut (lose ~0.25kg/week)",
    "maintain":  "Maintain weight",
    "mild_bulk": "Mild bulk (gain ~0.25kg/week)",
    "bulk":      "Bulk (gain ~0.5kg/week)",
}


def calculate_bmr(weight_kg: float, height_cm: float, age: int, gender: str) -> float:
    """Mifflin-St Jeor BMR formula."""
    if gender == "male":
        return 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        return 10 * weight_kg + 6.25 * height_cm - 5 * age - 161


def calculate_tdee(weight_kg: float, height_cm: float, age: int,
                   gender: str, activity_level: str) -> float:
    bmr = calculate_bmr(weight_kg, height_cm, age, gender)
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    return round(bmr * multiplier)


def calculate_goals(weight_kg: float, height_cm: float, age: int,
                    gender: str, activity_level: str, goal_type: str) -> dict:
    """
    Full macro calculation from physical stats.
    Returns calorie, protein, carb, fat goals.
    """
    tdee = calculate_tdee(weight_kg, height_cm, age, gender, activity_level)
    adjustment = GOAL_ADJUSTMENTS.get(goal_type, 0)
    target_calories = round(tdee + adjustment)

    # Protein: 1.8g per kg bodyweight (standard for active individuals)
    protein_g = round(weight_kg * 1.8)

    # Fat: 25% of total calories
    fat_g = round((target_calories * 0.25) / 9)

    # Carbs: remaining calories
    protein_cals = protein_g * 4
    fat_cals = fat_g * 9
    carb_cals = target_calories - protein_cals - fat_cals
    carbs_g = round(carb_cals / 4)

    bmi = round(weight_kg / ((height_cm / 100) ** 2), 1)

    return {
        "bmr": round(tdee / ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)),
        "tdee": tdee,
        "target_calories": target_calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "bmi": bmi,
        "adjustment": adjustment,
        "goal_type": goal_type,
    }
