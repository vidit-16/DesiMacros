"""
Insights Engine — Phase 5
--------------------------
Two layers:
1. Rule-based alerts  — instant, no API call, flags daily issues
2. LLM weekly summary — Groq generates plain-English feedback from 7-day data
"""

from groq import Groq

from app.core.config import get_settings

settings = get_settings()


# ── Rule-based alerts ─────────────────────────────────────────────────────────

def get_daily_alerts(totals: dict, goals: dict) -> list[str]:
    """
    Compare today's totals against goals and return plain-English alerts.
    Called instantly — no API needed.
    """
    alerts = []

    cal       = totals.get("calories", 0)
    protein   = totals.get("protein", 0)
    carbs     = totals.get("carbs", 0)
    fat       = totals.get("fat", 0)

    cal_goal     = goals.get("calorie_goal", 2200)
    protein_goal = goals.get("protein_goal", 100)
    carbs_goal   = goals.get("carbs_goal", 280)
    fat_goal     = goals.get("fat_goal", 70)

    # Calorie alerts
    if cal < cal_goal * 0.5:
        alerts.append(f"Calories are at {cal:.0f} kcal, less than half of your {cal_goal:.0f} kcal target.")
    elif cal < cal_goal * 0.75:
        alerts.append(f"Calories are at {cal:.0f} kcal, {cal_goal - cal:.0f} kcal below your target.")
    elif cal > cal_goal * 1.2:
        alerts.append(f"Calories are {cal - cal_goal:.0f} kcal above your target.")

    # Protein alerts
    if protein < protein_goal * 0.5:
        alerts.append(f"Protein is low at {protein:.0f} g. Dal, paneer, eggs or curd are good sources for the next meal.")
    elif protein < protein_goal * 0.75:
        alerts.append(f"Protein is at {protein:.0f} g, {protein_goal - protein:.0f} g below your target. A katori of dal or an egg would close part of the gap.")

    # Carb alerts
    if carbs > carbs_goal * 1.3:
        alerts.append(f"Carbohydrates are high at {carbs:.0f} g against a {carbs_goal:.0f} g target.")

    # Fat alerts
    if fat > fat_goal * 1.3:
        alerts.append(f"Fat is high at {fat:.0f} g against a {fat_goal:.0f} g target. Fried food and oil-heavy dishes are the usual sources.")

    # Positive reinforcement
    if not alerts:
        if protein >= protein_goal * 0.9:
            alerts.append(f"Protein is at {protein:.0f} g, within 10% of your target.")
        else:
            alerts.append("Calories and macronutrients are within the expected range for today.")

    return alerts


# ── LLM Weekly Summary ────────────────────────────────────────────────────────

WEEKLY_SYSTEM_PROMPT = """You are a nutrition assistant familiar with Indian diets.
You will receive 7 days of calorie and macronutrient data for a user, and their daily targets.
Write a short weekly summary of 4 to 6 sentences.

Rules:
- Be specific: cite the actual numbers and patterns in the data
- Refer to Indian foods where relevant (dal, paneer, roti, rice)
- Give one or two practical suggestions
- Use a clear, professional tone that anyone can follow
- Plain prose only: no bullet points, headings or emojis
- Keep it under 100 words
- Do not give medical advice
"""

WEEKLY_USER_PROMPT = """Here is the user's weekly data:

Goals: {goals}

Daily breakdown:
{daily_data}

Write a short weekly nutrition summary for this person."""


def get_weekly_summary(weekly_data: list[dict], goals: dict) -> str:
    """
    Generate a plain-English weekly summary using Groq.
    
    Args:
        weekly_data: list of dicts with date + totals for each of last 7 days
        goals: user's daily macro goals
    
    Returns:
        A conversational paragraph summary
    """
    # Format the data cleanly for the prompt
    daily_lines = []
    for day in weekly_data:
        if not day.get("logged"):
            daily_lines.append(f"{day['date']}: not logged")
            continue
        t = day["totals"]
        daily_lines.append(
            f"{day['date']}: {t.get('calories', 0):.0f} kcal | "
            f"P: {t.get('protein', 0):.0f}g | "
            f"C: {t.get('carbs', 0):.0f}g | "
            f"F: {t.get('fat', 0):.0f}g"
        )

    goals_str = (
        f"Calories: {goals.get('calories', 2200)} kcal | "
        f"Protein: {goals.get('protein', 100)}g | "
        f"Carbs: {goals.get('carbs', 280)}g | "
        f"Fat: {goals.get('fat', 70)}g"
    )

    prompt = WEEKLY_USER_PROMPT.format(
        goals=goals_str,
        daily_data="\n".join(daily_lines),
    )

    try:
        if not settings.groq_api_key:
            return "Weekly summaries are not available because the language model service is not configured."
        client = Groq(api_key=settings.groq_api_key)
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": WEEKLY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=1200,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"The weekly summary could not be generated: {str(e)}"


# ── Pattern detection ─────────────────────────────────────────────────────────

def detect_patterns(weekly_data: list[dict], goals: dict) -> list[str]:
    """
    Detect multi-day patterns from weekly data.
    Returns a list of pattern strings to show in the UI.
    """
    patterns = []
    logged_days = [d for d in weekly_data if d.get("logged")]

    if len(logged_days) < 3:
        return ["Patterns appear once meals have been logged on at least 3 days."]

    avg_cal     = sum(d["totals"].get("calories", 0) for d in logged_days) / len(logged_days)
    avg_protein = sum(d["totals"].get("protein", 0) for d in logged_days) / len(logged_days)

    cal_goal     = goals.get("calories", 2200)
    protein_goal = goals.get("protein", 100)

    # Low protein pattern
    low_protein_days = [d for d in logged_days if d["totals"].get("protein", 0) < protein_goal * 0.6]
    if len(low_protein_days) >= 3:
        patterns.append(f"Protein was low on {len(low_protein_days)} of {len(logged_days)} logged days, averaging {avg_protein:.0f} g against a {protein_goal:.0f} g target. Including a protein source in each meal would help.")

    # Consistent under-eating
    low_cal_days = [d for d in logged_days if d["totals"].get("calories", 0) < cal_goal * 0.7]
    if len(low_cal_days) >= 3:
        patterns.append(f"Calories were well below target on {len(low_cal_days)} days. Check whether any meals went unlogged or were skipped.")

    # Good consistency
    if len(logged_days) >= 6:
        patterns.append(f"Meals were logged on {len(logged_days)} of 7 days this week.")
    elif len(logged_days) >= 4:
        patterns.append(f"Meals were logged on {len(logged_days)} of 7 days. Logging every day gives more reliable trends.")
    else:
        patterns.append(f"Meals were logged on only {len(logged_days)} of 7 days, so these trends are less reliable.")

    # Avg calorie summary
    diff = avg_cal - cal_goal
    if abs(diff) < 150:
        patterns.append(f"Average daily calories were {avg_cal:.0f} kcal, within 150 kcal of your target.")
    elif diff < 0:
        patterns.append(f"Average daily calories were {avg_cal:.0f} kcal, {abs(diff):.0f} kcal below your target.")
    else:
        patterns.append(f"Average daily calories were {avg_cal:.0f} kcal, {diff:.0f} kcal above your target.")

    return patterns
