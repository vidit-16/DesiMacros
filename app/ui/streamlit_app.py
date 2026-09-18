"""
DesiMacros — Streamlit Frontend
Run with: streamlit run app/ui/streamlit_app.py
"""

import html
import os
import uuid
from pathlib import Path

import streamlit as st
import httpx
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date

# How each nutrition source is shown to the user.
SOURCE_LABELS = {
    "ifct": "Indian food table",
    "usda": "USDA database",
    "estimate": "estimate (not in either database)",
    "not_found": "not found",
}

# Same container or bare metal -> localhost:8000; docker-compose -> http://api:8000
API_BASE = (os.getenv("API_BASE_URL") or "http://localhost:8000").rstrip("/")

# Set DEMO_MODE=1 on a public deployment where storage is ephemeral.
DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in {"1", "true", "yes"}

def _visitor_token() -> str:
    """
    Keep each visitor's log separate. The token lives in the URL because
    Streamlit's session state is dropped on refresh - bookmarking the link is
    what carries your history forward.
    """
    token = st.query_params.get("u")
    if not token:
        token = uuid.uuid4().hex[:16]
        st.query_params["u"] = token
    return token


USER_TOKEN = _visitor_token()
HEADERS = {"X-User-Token": USER_TOKEN}

st.set_page_config(
    page_title="DesiMacros",
    page_icon=str(Path(__file__).resolve().parent / "favicon.png"),
    layout="wide",
    initial_sidebar_state="expanded",
)

# One accent (the app's green), a white canvas and hairline borders. The macro
# colours below are the chart palette, reused on the metric cards so a colour
# means the same thing in both places.
ACCENT = "#2f5d50"

st.markdown(f"""
<style>
:root {{
  --accent: {ACCENT};
  --accent-tint: #eaf1ee;
  --ink: #14181f;
  --muted: #5b6472;
  --surface: #f7f8f9;
  --hairline: #e5e7eb;
}}

/* Streamlit's own red-to-yellow bar sits above every page; make it ours. */
[data-testid="stDecoration"] {{
  background: linear-gradient(90deg, var(--accent), #4b8a76);
}}

.stMain h1 {{
  padding-bottom: 0.3rem;
  border-bottom: 3px solid var(--accent);
  display: inline-block;
}}

/* Tables: an accent header and hairline rows, so a table reads as a unit. */
[data-testid="stTable"] table {{
  border-collapse: separate;
  border-spacing: 0;
  border: 1px solid var(--hairline);
  border-radius: 10px;
  overflow: hidden;
}}
[data-testid="stTable"] thead th {{
  background: var(--surface);
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border-bottom: 2px solid var(--accent);
}}
[data-testid="stTable"] tbody tr:nth-child(even) {{ background: #fbfcfd; }}
[data-testid="stTable"] tbody td {{ border-bottom: 1px solid var(--hairline); }}
[data-testid="stTable"] tbody tr:last-child td {{ border-bottom: none; }}

/* The day's headline figures, one card per macronutrient. */
.macro-card {{
    background: var(--surface);
    border: 1px solid var(--hairline);
    border-left: 4px solid var(--accent);
    border-radius: 10px;
    padding: 16px;
    text-align: center;
}}
.macro-number {{ font-size: 28px; font-weight: 700; color: var(--ink); }}
.macro-label  {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}

.chat-msg-user {{ background: var(--accent-tint); border-radius:10px; padding:10px 14px; margin:6px 0; color: var(--ink) !important; }}
.chat-msg-bot  {{ background:#ffffff; border:1px solid var(--hairline); border-left:3px solid var(--accent); border-radius:10px; padding:10px 14px; margin:6px 0; color: var(--ink) !important; }}
.chat-role {{ font-size:12px; font-weight:600; color: var(--muted); margin-bottom:4px; letter-spacing:0.03em; }}
</style>
""", unsafe_allow_html=True)


# ── Load profile for sidebar ──────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_profile(token: str):
    # token is part of the cache key - without it one visitor's profile would
    # be served to the next, since cache_data is shared across sessions.
    try:
        r = httpx.get(f"{API_BASE}/api/profile", timeout=5, headers={"X-User-Token": token})
        return r.json()
    except Exception:
        return {"calorie_goal": 2200, "protein_goal": 100, "carbs_goal": 280, "fat_goal": 70, "name": ""}

@st.cache_data(ttl=300)
def load_storage():
    """Returns "sqlite" when logs are lost on restart, "postgres" when they persist."""
    try:
        return httpx.get(f"{API_BASE}/health", timeout=5).json().get("storage", "sqlite")
    except Exception:
        return "sqlite"


profile = load_profile(USER_TOKEN)

# The API stores a placeholder name until the user sets one.
PLACEHOLDER_NAMES = {"", "you", "user"}


def display_name(p: dict) -> str:
    name = (p.get("name") or "").strip()
    return "" if name.lower() in PLACEHOLDER_NAMES else name


with st.sidebar:
    st.title("DesiMacros")
    st.caption("Calorie and macro tracking for Indian meals.")
    st.divider()
    page = st.radio("Section", ["Log a meal", "Daily summary", "History", "Weekly trends", "Profile and goals"])
    st.divider()
    st.caption(f"Daily targets for {display_name(profile)}" if display_name(profile) else "Daily targets")
    st.metric("Calories", f"{profile.get('calorie_goal', 2200):.0f} kcal")
    st.metric("Protein", f"{profile.get('protein_goal', 100):.0f} g")
    st.metric("Carbohydrates", f"{profile.get('carbs_goal', 280):.0f} g")
    st.metric("Fat", f"{profile.get('fat_goal', 70):.0f} g")
    st.divider()
    if DEMO_MODE and load_storage() == "sqlite":
        st.caption(
            "Your log is private to this link. Bookmark the page to return to it. "
            "This demo clears all logs when the app restarts."
        )
    else:
        st.caption("Your log is private to this link. Bookmark the page to return to it.")
    st.caption("All figures are estimates and are not medical or dietary advice.")


# ── Helpers ───────────────────────────────────────────────────────────────────
def post_log(text, log_date, use_typical_portions=False):
    try:
        payload = {"text": text, "log_date": log_date, "use_typical_portions": use_typical_portions}
        r = httpx.post(f"{API_BASE}/api/log", json=payload, timeout=30, headers=HEADERS)
        return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as e:
        return {"error": str(e)}

def get_summary(log_date):
    try:
        r = httpx.get(f"{API_BASE}/api/summary", params={"log_date": log_date}, timeout=10, headers=HEADERS)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_history():
    try:
        r = httpx.get(f"{API_BASE}/api/history", timeout=10, headers=HEADERS)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_weekly():
    try:
        r = httpx.get(f"{API_BASE}/api/weekly", timeout=10, headers=HEADERS)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def delete_entry(entry_id):
    try:
        r = httpx.delete(f"{API_BASE}/api/entry/{entry_id}", timeout=10, headers=HEADERS)
        return (True, "") if r.status_code == 200 else (False, r.text)
    except Exception as e:
        return False, str(e)

def macro_card(label, value, unit="g", color="#2f5d50"):
    st.markdown(f"""
    <div class="macro-card" style="border-left-color:{color}">
        <div class="macro-number">{value}<span style="font-size:14px"> {unit}</span></div>
        <div class="macro-label">{label}</div>
    </div>""", unsafe_allow_html=True)


# ── Pages ─────────────────────────────────────────────────────────────────────

if page == "Log a meal":
    st.title("Log a meal")
    st.caption(
        "Describe what you ate in your own words, with amounts where you can (for example, 2 rotis "
        "or 1 katori of dal). If an amount is missing you will be asked for it, or can use a typical portion."
    )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    log_date = st.date_input("Date", value=date.today(), max_value=date.today())

    st.markdown("**Examples**")
    examples = [
        "A katori of poha and a cup of chai for breakfast",
        "2 rotis with 1 katori dal tadka and a katori of dahi for lunch",
        "1 katori rajma with 1 katori rice for dinner",
        "Dal, mixed vegetable sabzi and 3 rotis",
    ]
    cols = st.columns(2)
    for i, ex in enumerate(examples):
        if cols[i % 2].button(ex, key=f"ex_{i}", use_container_width=True):
            st.session_state.prefill = ex

    prefill = st.session_state.pop("prefill", "")
    user_input = st.chat_input("Describe your meal") or prefill
    use_typical = False

    # Set by the "Log with typical portions" button on the previous run.
    if st.session_state.pop("log_typical", False):
        user_input, use_typical = st.session_state.get("pending_meal", ""), True

    if user_input:
        st.session_state.pop("pending_meal", None)
        if not use_typical:
            st.session_state.chat_history.append({"role": "user", "text": user_input})
        with st.spinner("Estimating nutrition"):
            result = post_log(user_input, str(log_date), use_typical_portions=use_typical)

        if "error" not in result and result.get("logged") is False:
            names = result.get("needs_quantities", [])
            foods = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            st.session_state.pending_meal = user_input
            st.session_state.chat_history.append({"role": "bot", "text": (
                f"How much {foods} did you have? Describe the meal again with amounts, for example "
                "\"1 katori of dal\" or \"2 rotis\", or log it with typical portions."
            )})
            st.rerun()
        elif "error" in result:
            st.session_state.chat_history.append({"role": "bot", "text": f"The meal could not be logged: {result['error']}"})
        else:
            entries = result.get("entries", [])
            totals = result.get("daily_totals", {})
            confidence = result.get("parse_confidence", "")
            clarification = result.get("clarification_needed", "")

            lines = [f"Logged {len(entries)} item{'' if len(entries) == 1 else 's'}.\n"]
            missing = []
            for e in entries:
                if e.get("source") == "not_found":
                    missing.append(e["food_name"])
                    lines.append(f"- **{e['food_name']}** ({e['quantity']} {e['unit']}): not found in the nutrition database, so not counted")
                    continue
                lines.append(f"- **{e['food_name']}** ({e['quantity']} {e['unit']}): {e['calories']} kcal, protein {e['protein']} g, carbohydrates {e['carbs']} g, fat {e['fat']} g"
                             + (" (estimated: this food is not in the nutrition database)" if e.get("source") == "estimate" else ""))
            lines.append(f"\n**Total for the day:** {totals.get('calories', 0)} kcal, protein {totals.get('protein', 0)} g ({totals.get('protein_pct', 0)}% of target)")
            if clarification:
                lines.append(f"\nNote: {clarification}")
            if confidence == "low":
                lines.append("\nSome portions were unclear, so this estimate is less reliable. Logging again with quantities will improve it.")
            if missing:
                lines.append(f"\nNot found: {', '.join(missing)}. Try a more common name, or log the main ingredients separately.")

            st.session_state.chat_history.append({"role": "bot", "text": "\n".join(lines)})
            st.cache_data.clear()

    if st.session_state.get("pending_meal"):
        c1, c2 = st.columns(2)
        if c1.button("Log with typical portions", use_container_width=True):
            st.session_state.log_typical = True
            st.rerun()
        if c2.button("Cancel", use_container_width=True):
            st.session_state.pop("pending_meal", None)
            st.rerun()

    for msg in reversed(st.session_state.chat_history):
        if msg["role"] == "user":
            st.markdown(
                f'<div class="chat-msg-user"><div class="chat-role">You</div>{html.escape(msg["text"])}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="chat-role">DesiMacros</div>', unsafe_allow_html=True)
            st.markdown(msg["text"])


elif page == "Daily summary":
    st.title("Daily summary")
    selected_date = st.date_input("Date", value=date.today(), max_value=date.today())
    data = get_summary(str(selected_date))

    if "error" in data:
        st.error(data["error"])
    else:
        totals = data.get("totals", {})
        entries = data.get("entries", [])

        if not entries:
            st.info("Nothing has been logged for this day.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1: macro_card("Calories", totals.get("calories", 0), "kcal", "#2f5d50")
            with c2: macro_card("Protein", totals.get("protein", 0), "g", "#3b7a9e")
            with c3: macro_card("Carbohydrates", totals.get("carbs", 0), "g", "#8a9a5b")
            with c4: macro_card("Fat", totals.get("fat", 0), "g", "#c08a3e")

            st.divider()

            cal_pct = min(totals.get("calorie_pct", 0), 100)
            st.subheader("Calories against target")
            st.progress(cal_pct / 100, text=f"{cal_pct}% of daily target, {totals.get('calories_remaining', 0)} kcal remaining")

            st.subheader("Calories by macronutrient")
            fig = go.Figure(data=[go.Pie(
                labels=["Protein", "Carbohydrates", "Fat"],
                values=[totals.get("protein", 0) * 4, totals.get("carbs", 0) * 4, totals.get("fat", 0) * 9],
                hole=0.5,
                marker_colors=["#3b7a9e", "#8a9a5b", "#c08a3e"],
            )])
            fig.update_layout(margin=dict(t=0, b=0), height=280)
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Logged items")
            st.caption("To correct an item, delete it and log the meal again.")
            for e in entries:
                c1, c2, c3 = st.columns([4, 4, 1])
                c1.markdown(f"**{e['food_name']}** — {e['quantity']} {e['unit']}")
                c2.caption(
                    f"{e['calories']} kcal · protein {e['protein']} g · carbohydrates {e['carbs']} g · fat {e['fat']} g"
                    f" · source: {SOURCE_LABELS.get(e.get('source'), e.get('source', 'unknown'))}"
                )
                if c3.button("Delete", key=f"del_{e['id']}", help="Delete this item"):
                    ok, err = delete_entry(e["id"])
                    if ok:
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"The item could not be deleted: {err}")

            st.divider()
            st.subheader("Observations")
            try:
                alerts_data = httpx.get(f"{API_BASE}/api/alerts", params={"log_date": str(selected_date)}, timeout=10, headers=HEADERS).json()
                for alert in alerts_data.get("alerts", []):
                    st.markdown(alert)
            except Exception as e:
                st.warning(f"Observations could not be loaded: {e}")


elif page == "History":
    st.title("History")
    data = get_history()

    if "error" in data:
        st.error(data["error"])
    else:
        history = data.get("history", [])
        if not history:
            st.info("No meals have been logged yet.")
        else:
            for day in history:
                with st.expander(f"{day['date']}: {day['totals'].get('calories', 0)} kcal"):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Protein", f"{day['totals'].get('protein', 0)} g")
                    c2.metric("Carbohydrates", f"{day['totals'].get('carbs', 0)} g")
                    c3.metric("Fat", f"{day['totals'].get('fat', 0)} g")
                    if day["entries"]:
                        df = pd.DataFrame(day["entries"])
                        table = df[["food_name", "quantity", "unit", "calories"]].rename(
                            columns={"food_name": "Food", "quantity": "Quantity",
                                     "unit": "Unit", "calories": "Calories (kcal)"}
                        )
                        # st.table prints raw floats ("2.0000"), so format first.
                        table["Quantity"] = table["Quantity"].map(lambda v: f"{v:g}")
                        table["Calories (kcal)"] = table["Calories (kcal)"].map(lambda v: f"{v:.1f}")
                        table.index = range(1, len(table) + 1)
                        st.table(table)


elif page == "Weekly trends":
    st.title("Weekly trends")
    data = get_weekly()

    if "error" in data:
        st.error(data["error"])
    else:
        weekly = data.get("weekly", [])
        goals = data.get("goals", {})

        if not any(d["logged"] for d in weekly):
            st.info("Weekly trends appear once meals have been logged on at least one day this week.")
        else:
            df = pd.DataFrame([{
                "Date": d["date"],
                "Calories": d["totals"].get("calories", 0),
                "Protein": d["totals"].get("protein", 0),
                "Carbs": d["totals"].get("carbs", 0),
                "Fat": d["totals"].get("fat", 0),
                "Logged": d["logged"],
            } for d in weekly])

            fig = px.bar(df, x="Date", y="Calories", color="Logged",
                         color_discrete_map={True: "#2f5d50", False: "#dee2e6"},
                         title="Daily calories against target")
            fig.add_hline(y=goals.get("calories", 2000), line_dash="dash",
                          annotation_text="Calorie target", line_color="#6c757d")
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

            fig2 = px.line(df, x="Date", y="Protein", markers=True,
                           title="Daily protein (g)", color_discrete_sequence=["#3b7a9e"])
            fig2.add_hline(y=goals.get("protein", 80), line_dash="dash",
                           annotation_text="Protein target", line_color="#6c757d")
            st.plotly_chart(fig2, use_container_width=True)

            logged_days = df[df["Logged"]]
            if not logged_days.empty:
                st.subheader("This week")
                c1, c2, c3 = st.columns(3)
                c1.metric("Average daily calories", f"{logged_days['Calories'].mean():.0f} kcal")
                c2.metric("Average daily protein", f"{logged_days['Protein'].mean():.0f} g")
                c3.metric("Days logged", f"{len(logged_days)} of 7")

            st.divider()
            st.subheader("Patterns")
            try:
                patterns_data = httpx.get(f"{API_BASE}/api/patterns", timeout=10, headers=HEADERS).json()
                for p in patterns_data.get("patterns", []):
                    st.markdown(p)
            except Exception as e:
                st.warning(f"Patterns could not be loaded: {e}")

            st.divider()
            st.subheader("Weekly summary")
            st.caption("Written by a language model from this week's logs. Treat it as general guidance.")
            if st.button("Write weekly summary", type="primary"):
                with st.spinner("Summarising the week"):
                    try:
                        summary_data = httpx.get(f"{API_BASE}/api/weekly-summary", timeout=30, headers=HEADERS).json()
                        st.info(summary_data.get("summary", "No summary available."))
                        st.caption(f"Based on {summary_data.get('days_logged', 0)} logged days this week.")
                    except Exception as e:
                        st.error(f"The summary could not be generated: {e}")


elif page == "Profile and goals":
    st.title("Profile and goals")
    st.caption("Enter your details to calculate daily calorie and macronutrient targets.")

    profile = load_profile(USER_TOKEN)

    with st.form("profile_form"):
        st.subheader("Personal details")
        c1, c2 = st.columns(2)
        name      = c1.text_input("Name (optional)", value=display_name(profile))
        gender    = c2.selectbox("Sex (used in the energy equation)", ["male", "female"],
                                  format_func=str.capitalize,
                                  index=0 if profile.get("gender", "male") == "male" else 1)

        c1, c2, c3 = st.columns(3)
        age       = c1.number_input("Age", min_value=10, max_value=80, value=int(profile.get("age", 20)))
        height_cm = c2.number_input("Height (cm)", min_value=100.0, max_value=250.0,
                                     value=float(profile.get("height_cm", 170.0)), step=0.5)
        weight_kg = c3.number_input("Weight (kg)", min_value=30.0, max_value=200.0,
                                     value=float(profile.get("weight_kg", 65.0)), step=0.5)

        st.subheader("Activity and goal")
        activity_options = {
            "sedentary":   "Sedentary: little or no exercise",
            "light":       "Lightly active: exercise 1 to 3 days a week",
            "moderate":    "Moderately active: exercise 3 to 5 days a week",
            "active":      "Active: exercise 6 to 7 days a week",
            "very_active": "Very active: athletic training or a physical job",
        }
        activity_keys = list(activity_options.keys())
        current_activity = profile.get("activity_level", "moderate")
        activity_level = st.selectbox(
            "Activity level",
            options=activity_keys,
            format_func=lambda x: activity_options[x],
            index=activity_keys.index(current_activity) if current_activity in activity_keys else 2,
        )

        goal_options = {
            "cut":       "Lose about 0.5 kg per week",
            "mild_cut":  "Lose about 0.25 kg per week",
            "maintain":  "Maintain current weight",
            "mild_bulk": "Gain about 0.25 kg per week",
            "bulk":      "Gain about 0.5 kg per week",
        }
        goal_keys = list(goal_options.keys())
        current_goal = profile.get("goal_type", "maintain")
        goal_type = st.selectbox(
            "Goal",
            options=goal_keys,
            format_func=lambda x: goal_options[x],
            index=goal_keys.index(current_goal) if current_goal in goal_keys else 2,
        )

        submitted = st.form_submit_button("Calculate and save targets", type="primary", use_container_width=True)

    if submitted:
        with st.spinner("Calculating targets"):
            try:
                r = httpx.post(f"{API_BASE}/api/profile", json={
                    "name": name, "age": age, "gender": gender,
                    "height_cm": height_cm, "weight_kg": weight_kg,
                    "activity_level": activity_level, "goal_type": goal_type,
                }, timeout=10, headers=HEADERS)
                result = r.json()
                goals = result.get("goals", {})

                st.success("Targets saved.")
                st.divider()
                st.subheader("Daily targets")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Calories", f"{goals.get('target_calories', 0):.0f} kcal")
                c2.metric("Protein", f"{goals.get('protein_g', 0):.0f} g")
                c3.metric("Carbohydrates", f"{goals.get('carbs_g', 0):.0f} g")
                c4.metric("Fat", f"{goals.get('fat_g', 0):.0f} g")

                st.divider()
                st.subheader("How the targets are calculated")
                c1, c2, c3 = st.columns(3)
                c1.metric("BMR", f"{goals.get('bmr', 0):.0f} kcal", help="Basal metabolic rate: energy used at complete rest")
                c2.metric("TDEE", f"{goals.get('tdee', 0):.0f} kcal", help="Total daily energy expenditure: maintenance calories at your activity level")
                adj = goals.get('adjustment', 0)
                c3.metric("Goal adjustment", f"{'+' if adj >= 0 else ''}{adj} kcal")

                bmi = goals.get("bmi", 0)
                bmi_label = "Underweight" if bmi < 18.5 else "Normal" if bmi < 25 else "Overweight" if bmi < 30 else "Obese"
                st.info(f"BMI: **{bmi}** ({bmi_label})")
                st.caption(
                    "These targets come from the Mifflin-St Jeor equation applied to the "
                    "numbers above - a population-level estimate, not advice for you "
                    "specifically. Check with a doctor or a registered dietitian before "
                    "acting on them, especially if you are managing a health condition, "
                    "pregnant, or under 18."
                )

                st.cache_data.clear()
                st.session_state["profile_saved"] = True

            except Exception as e:
                st.error(f"The profile could not be saved: {e}")

    else:
        # Show current calculated values without saving
        st.divider()
        st.subheader("Current targets")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Calories", f"{profile.get('calorie_goal', 0):.0f} kcal")
        c2.metric("Protein", f"{profile.get('protein_goal', 0):.0f} g")
        c3.metric("Carbohydrates", f"{profile.get('carbs_goal', 0):.0f} g")
        c4.metric("Fat", f"{profile.get('fat_goal', 0):.0f} g")