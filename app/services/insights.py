"""
Insights Engine — Phase 5
--------------------------
Two layers:
1. Rule-based alerts  — instant, no API call, flags daily issues
2. LLM weekly summary — Groq generates plain-English feedback from 7-day data
"""

import json
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
        alerts.append(f"⚠️ You've only had {cal:.0f} kcal today — that's less than half your goal. Don't skip meals.")
    elif cal < cal_goal * 0.75:
        alerts.append(f"📉 You're at {cal:.0f} kcal — still {cal_goal - cal:.0f} kcal to go for today.")
    elif cal > cal_goal * 1.2:
        alerts.append(f"📈 You've exceeded your calorie goal by {cal - cal_goal:.0f} kcal today.")

    # Protein alerts
    if protein < protein_goal * 0.5:
        alerts.append(f"🥩 Protein is very low ({protein:.0f}g). Try adding dal, paneer, eggs, or curd to your next meal.")
    elif protein < protein_goal * 0.75:
        alerts.append(f"💪 Protein at {protein:.0f}g — {protein_goal - protein:.0f}g short of your goal. A katori of dal or an egg would help.")

    # Carb alerts
    if carbs > carbs_goal * 1.3:
        alerts.append(f"🍚 Carbs are high today ({carbs:.0f}g vs {carbs_goal:.0f}g goal) — mostly from rice or roti, likely.")

    # Fat alerts
    if fat > fat_goal * 1.3:
        alerts.append(f"🧈 Fat intake is high ({fat:.0f}g vs {fat_goal:.0f}g goal). Watch out for heavy sabzis or fried stuff.")

    # Positive reinforcement
    if not alerts:
        if protein >= protein_goal * 0.9:
            alerts.append(f"✅ Great protein day — {protein:.0f}g, almost at goal!")
        else:
            alerts.append("✅ Looking balanced today, keep it up!")

    return alerts


# ── LLM Weekly Summary ────────────────────────────────────────────────────────

WEEKLY_SYSTEM_PROMPT = """You are a nutrition coach who specialises in Indian diets.
You will receive 7 days of macro data for a user and their daily goals.
Write a short, conversational weekly summary (4-6 sentences max).

Rules:
- Be specific — mention actual numbers and patterns you see
- Reference Indian foods naturally (dal, paneer, roti, rice, etc.)
- Give 1-2 actionable suggestions, not a lecture
- Be encouraging but honest
- No bullet points, no headers — just natural flowing text like a WhatsApp message from a coach
- Keep it under 100 words
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
            return "Set GROQ_API_KEY in your .env to get an AI weekly summary."
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
        return f"Could not generate summary: {str(e)}"


# ── Pattern detection ─────────────────────────────────────────────────────────

def detect_patterns(weekly_data: list[dict], goals: dict) -> list[str]:
    """
    Detect multi-day patterns from weekly data.
    Returns a list of pattern strings to show in the UI.
    """
    patterns = []
    logged_days = [d for d in weekly_data if d.get("logged")]

    if len(logged_days) < 3:
        return ["Log at least 3 days to see patterns."]

    avg_cal     = sum(d["totals"].get("calories", 0) for d in logged_days) / len(logged_days)
    avg_protein = sum(d["totals"].get("protein", 0) for d in logged_days) / len(logged_days)
    avg_carbs   = sum(d["totals"].get("carbs", 0) for d in logged_days) / len(logged_days)

    cal_goal     = goals.get("calories", 2200)
    protein_goal = goals.get("protein", 100)

    # Low protein pattern
    low_protein_days = [d for d in logged_days if d["totals"].get("protein", 0) < protein_goal * 0.6]
    if len(low_protein_days) >= 3:
        patterns.append(f"🔴 Low protein on {len(low_protein_days)} of {len(logged_days)} logged days (avg {avg_protein:.0f}g vs {protein_goal:.0f}g goal). Add a protein source to every meal.")

    # Consistent under-eating
    low_cal_days = [d for d in logged_days if d["totals"].get("calories", 0) < cal_goal * 0.7]
    if len(low_cal_days) >= 3:
        patterns.append(f"⚠️ Under-eating on {len(low_cal_days)} days — you might be skipping lunch on busy college days.")

    # Good consistency
    if len(logged_days) >= 6:
        patterns.append(f"🟢 Great consistency — logged {len(logged_days)}/7 days this week!")
    elif len(logged_days) >= 4:
        patterns.append(f"🟡 Logged {len(logged_days)}/7 days — try to log every day for better insights.")
    else:
        patterns.append(f"🔴 Only {len(logged_days)}/7 days logged — more data = better insights.")

    # Avg calorie summary
    diff = avg_cal - cal_goal
    if abs(diff) < 150:
        patterns.append(f"✅ Average daily calories ({avg_cal:.0f} kcal) is right on target.")
    elif diff < 0:
        patterns.append(f"📉 Averaging {avg_cal:.0f} kcal/day — {abs(diff):.0f} below your goal on average.")
    else:
        patterns.append(f"📈 Averaging {avg_cal:.0f} kcal/day — {diff:.0f} above your goal on average.")

    return patterns
