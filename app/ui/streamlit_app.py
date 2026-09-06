"""
DesiMacros — Streamlit Frontend
Run with: streamlit run app/ui/streamlit_app.py
"""

import os
import uuid

import streamlit as st
import httpx
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date

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
    page_icon="🥗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.macro-card {
    background: #f8f9fa;
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    border-left: 4px solid #ff6b35;
}
.macro-number { font-size: 28px; font-weight: 700; color: #1a1a2e; }
.macro-label  { font-size: 12px; color: #6c757d; text-transform: uppercase; }
.chat-msg-user { background:#e8f4fd; border-radius:12px; padding:10px 14px; margin:6px 0; color:#1a1a1a !important; }
.chat-msg-bot  { background:#f0f8f0; border-radius:12px; padding:10px 14px; margin:6px 0; color:#1a1a1a !important; }
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
        return {"calorie_goal": 2200, "protein_goal": 100, "carbs_goal": 280, "fat_goal": 70, "name": "User"}

profile = load_profile(USER_TOKEN)

with st.sidebar:
    st.title("🥗 DesiMacros")
    st.caption("Talk to log. No forms, no fuss.")
    st.divider()
    page = st.radio("Navigate", ["📝 Log Meal", "📊 Today's Summary", "📅 History", "📈 Weekly Insights", "⚙️ Profile & Goals"])
    st.divider()
    st.caption(f"Goals for {profile.get('name', 'You')}")
    st.metric("Calories", f"{profile.get('calorie_goal', 2200):.0f} kcal")
    st.metric("Protein", f"{profile.get('protein_goal', 100):.0f}g")
    st.metric("Carbs", f"{profile.get('carbs_goal', 280):.0f}g")
    st.metric("Fat", f"{profile.get('fat_goal', 70):.0f}g")
    st.divider()
    if DEMO_MODE:
        st.caption(
            "This log belongs to your link alone - bookmark the URL to come back to it. "
            "Demo data is cleared whenever the app restarts."
        )
    else:
        st.caption("This log belongs to your link alone - bookmark the URL to come back to it.")


# ── Helpers ───────────────────────────────────────────────────────────────────
def post_log(text, log_date):
    try:
        r = httpx.post(f"{API_BASE}/api/log", json={"text": text, "log_date": log_date}, timeout=30, headers=HEADERS)
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

def macro_card(label, value, unit="g", color="#ff6b35"):
    st.markdown(f"""
    <div class="macro-card" style="border-left-color:{color}">
        <div class="macro-number">{value}<span style="font-size:14px">{unit}</span></div>
        <div class="macro-label">{label}</div>
    </div>""", unsafe_allow_html=True)


# ── Pages ─────────────────────────────────────────────────────────────────────

if "📝 Log Meal" in page:
    st.title("📝 Log Your Meal")
    st.caption("Just describe what you ate — no measurements needed if you don't have them.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    log_date = st.date_input("Date", value=date.today(), max_value=date.today())

    st.markdown("**Try these:**")
    examples = [
        "Had poha for breakfast and chai",
        "2 rotis with dal tadka and a katori of dahi for lunch",
        "Rajma chawal for dinner with some salad",
        "Mess food — dal, sabzi, 3 rotis",
    ]
    cols = st.columns(2)
    for i, ex in enumerate(examples):
        if cols[i % 2].button(ex, key=f"ex_{i}", use_container_width=True):
            st.session_state.prefill = ex

    prefill = st.session_state.pop("prefill", "")
    user_input = st.chat_input("What did you eat?") or prefill

    if user_input:
        st.session_state.chat_history.append({"role": "user", "text": user_input})
        with st.spinner("Parsing your meal..."):
            result = post_log(user_input, str(log_date))

        if "error" in result:
            st.session_state.chat_history.append({"role": "bot", "text": f"❌ Error: {result['error']}"})
        else:
            entries = result.get("entries", [])
            totals = result.get("daily_totals", {})
            confidence = result.get("parse_confidence", "")
            clarification = result.get("clarification_needed", "")

            lines = [f"✅ Logged {len(entries)} item(s)!\n"]
            missing = []
            for e in entries:
                if e.get("source") == "not_found":
                    missing.append(e["food_name"])
                    lines.append(f"• **{e['food_name']}** ({e['quantity']} {e['unit']}) — ❓ not in our database, counted as 0 kcal")
                    continue
                lines.append(f"• **{e['food_name']}** ({e['quantity']} {e['unit']}) — {e['calories']} kcal | P: {e['protein']}g | C: {e['carbs']}g | F: {e['fat']}g")
            lines.append(f"\n📊 **Today so far:** {totals.get('calories', 0)} kcal | Protein: {totals.get('protein', 0)}g ({totals.get('protein_pct', 0)}% of goal)")
            if clarification:
                lines.append(f"\n⚠️ *{clarification}*")
            if confidence == "low":
                lines.append("\n💡 *Confidence was low — you can re-log with more detail.*")
            if missing:
                lines.append(f"\n❓ *Couldn't find {', '.join(missing)}. Try a more common name, or log the main ingredients separately.*")

            st.session_state.chat_history.append({"role": "bot", "text": "\n".join(lines)})
            st.cache_data.clear()

    for msg in reversed(st.session_state.chat_history):
        if msg["role"] == "user":
            st.markdown(f'<div class="chat-msg-user">🧑 {msg["text"]}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat-msg-bot">🥗 {msg["text"]}</div>', unsafe_allow_html=True)


elif "📊 Today" in page:
    st.title("📊 Today's Summary")
    selected_date = st.date_input("Date", value=date.today(), max_value=date.today())
    data = get_summary(str(selected_date))

    if "error" in data:
        st.error(data["error"])
    else:
        totals = data.get("totals", {})
        entries = data.get("entries", [])

        if not entries:
            st.info("Nothing logged yet for this day. Go log a meal! 🍽️")
        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1: macro_card("Calories", totals.get("calories", 0), "kcal", "#ff6b35")
            with c2: macro_card("Protein", totals.get("protein", 0), "g", "#4ecdc4")
            with c3: macro_card("Carbs", totals.get("carbs", 0), "g", "#45b7d1")
            with c4: macro_card("Fat", totals.get("fat", 0), "g", "#f7dc6f")

            st.divider()

            cal_pct = min(totals.get("calorie_pct", 0), 100)
            st.subheader("Calorie Goal Progress")
            st.progress(cal_pct / 100, text=f"{cal_pct}% of daily goal ({totals.get('calories_remaining', 0)} kcal remaining)")

            st.subheader("Macro Split")
            fig = go.Figure(data=[go.Pie(
                labels=["Protein", "Carbs", "Fat"],
                values=[totals.get("protein", 0) * 4, totals.get("carbs", 0) * 4, totals.get("fat", 0) * 9],
                hole=0.5,
                marker_colors=["#4ecdc4", "#45b7d1", "#f7dc6f"],
            )])
            fig.update_layout(margin=dict(t=0, b=0), height=280)
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("What you ate")
            st.caption("Logged something wrong? Delete the entry and log it again.")
            for e in entries:
                c1, c2, c3 = st.columns([4, 4, 1])
                c1.markdown(f"**{e['food_name']}** — {e['quantity']} {e['unit']}")
                c2.caption(
                    f"{e['calories']} kcal · P {e['protein']}g · C {e['carbs']}g · F {e['fat']}g"
                    f"  ·  _{e.get('source', 'unknown')}_"
                )
                if c3.button("🗑️", key=f"del_{e['id']}", help="Delete this entry"):
                    ok, err = delete_entry(e["id"])
                    if ok:
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"Could not delete: {err}")

            st.divider()
            st.subheader("💡 Today's Alerts")
            try:
                alerts_data = httpx.get(f"{API_BASE}/api/alerts", params={"log_date": str(selected_date)}, timeout=10, headers=HEADERS).json()
                for alert in alerts_data.get("alerts", []):
                    st.markdown(alert)
            except Exception as e:
                st.warning(f"Could not load alerts: {e}")


elif "📅 History" in page:
    st.title("📅 Meal History")
    data = get_history()

    if "error" in data:
        st.error(data["error"])
    else:
        history = data.get("history", [])
        if not history:
            st.info("No history yet. Start logging meals!")
        else:
            for day in history:
                with st.expander(f"📅 {day['date']} — {day['totals'].get('calories', 0)} kcal"):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Protein", f"{day['totals'].get('protein', 0)}g")
                    c2.metric("Carbs", f"{day['totals'].get('carbs', 0)}g")
                    c3.metric("Fat", f"{day['totals'].get('fat', 0)}g")
                    if day["entries"]:
                        df = pd.DataFrame(day["entries"])
                        st.dataframe(df[["food_name", "quantity", "unit", "calories"]], use_container_width=True, hide_index=True)


elif "📈 Weekly" in page:
    st.title("📈 Weekly Insights")
    data = get_weekly()

    if "error" in data:
        st.error(data["error"])
    else:
        weekly = data.get("weekly", [])
        goals = data.get("goals", {})

        if not any(d["logged"] for d in weekly):
            st.info("Log meals for a few days and your weekly trends will appear here!")
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
                         color_discrete_map={True: "#ff6b35", False: "#dee2e6"},
                         title="Daily Calories vs Goal")
            fig.add_hline(y=goals.get("calories", 2000), line_dash="dash",
                          annotation_text="Calorie Goal", line_color="red")
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

            fig2 = px.line(df, x="Date", y="Protein", markers=True,
                           title="Daily Protein (g)", color_discrete_sequence=["#4ecdc4"])
            fig2.add_hline(y=goals.get("protein", 80), line_dash="dash",
                           annotation_text="Protein Goal", line_color="#4ecdc4")
            st.plotly_chart(fig2, use_container_width=True)

            logged_days = df[df["Logged"]]
            if not logged_days.empty:
                st.subheader("Week at a Glance")
                c1, c2, c3 = st.columns(3)
                c1.metric("Avg Daily Calories", f"{logged_days['Calories'].mean():.0f} kcal")
                c2.metric("Avg Protein", f"{logged_days['Protein'].mean():.0f}g")
                c3.metric("Days Logged", f"{len(logged_days)}/7")

            st.divider()
            st.subheader("🔍 Patterns")
            try:
                patterns_data = httpx.get(f"{API_BASE}/api/patterns", timeout=10, headers=HEADERS).json()
                for p in patterns_data.get("patterns", []):
                    st.markdown(p)
            except Exception as e:
                st.warning(f"Could not load patterns: {e}")

            st.divider()
            st.subheader("🤖 Coach Summary")
            st.caption("AI-generated weekly feedback based on your logs")
            if st.button("Generate Weekly Summary", type="primary"):
                with st.spinner("Analysing your week..."):
                    try:
                        summary_data = httpx.get(f"{API_BASE}/api/weekly-summary", timeout=30, headers=HEADERS).json()
                        st.info(summary_data.get("summary", "No summary available."))
                        st.caption(f"Based on {summary_data.get('days_logged', 0)} logged days this week.")
                    except Exception as e:
                        st.error(f"Could not generate summary: {e}")


elif "⚙️ Profile" in page:
    st.title("⚙️ Profile & Goals")
    st.caption("Enter your stats and we'll calculate your exact calorie and macro targets.")

    profile = load_profile(USER_TOKEN)

    with st.form("profile_form"):
        st.subheader("Personal Info")
        c1, c2 = st.columns(2)
        name      = c1.text_input("Name", value=profile.get("name", "Vidit"))
        gender    = c2.selectbox("Gender", ["male", "female"],
                                  index=0 if profile.get("gender", "male") == "male" else 1)

        c1, c2, c3 = st.columns(3)
        age       = c1.number_input("Age", min_value=10, max_value=80, value=int(profile.get("age", 20)))
        height_cm = c2.number_input("Height (cm)", min_value=100.0, max_value=250.0,
                                     value=float(profile.get("height_cm", 170.0)), step=0.5)
        weight_kg = c3.number_input("Weight (kg)", min_value=30.0, max_value=200.0,
                                     value=float(profile.get("weight_kg", 65.0)), step=0.5)

        st.subheader("Activity & Goal")
        activity_options = {
            "sedentary":   "🪑 Sedentary (no exercise, desk job)",
            "light":       "🚶 Light (1-3 days/week)",
            "moderate":    "🏃 Moderate (3-5 days/week) — typical college student",
            "active":      "💪 Active (6-7 days/week)",
            "very_active": "🏋️ Very active (athlete / physical job)",
        }
        activity_keys = list(activity_options.keys())
        current_activity = profile.get("activity_level", "moderate")
        activity_level = st.selectbox(
            "Activity Level",
            options=activity_keys,
            format_func=lambda x: activity_options[x],
            index=activity_keys.index(current_activity) if current_activity in activity_keys else 2,
        )

        goal_options = {
            "cut":       "📉 Cut — lose ~0.5kg/week",
            "mild_cut":  "📉 Mild cut — lose ~0.25kg/week",
            "maintain":  "⚖️ Maintain weight",
            "mild_bulk": "📈 Mild bulk — gain ~0.25kg/week",
            "bulk":      "📈 Bulk — gain ~0.5kg/week",
        }
        goal_keys = list(goal_options.keys())
        current_goal = profile.get("goal_type", "maintain")
        goal_type = st.selectbox(
            "Goal",
            options=goal_keys,
            format_func=lambda x: goal_options[x],
            index=goal_keys.index(current_goal) if current_goal in goal_keys else 2,
        )

        submitted = st.form_submit_button("Calculate & Save Goals", type="primary", use_container_width=True)

    if submitted:
        with st.spinner("Calculating your targets..."):
            try:
                r = httpx.post(f"{API_BASE}/api/profile", json={
                    "name": name, "age": age, "gender": gender,
                    "height_cm": height_cm, "weight_kg": weight_kg,
                    "activity_level": activity_level, "goal_type": goal_type,
                }, timeout=10, headers=HEADERS)
                result = r.json()
                goals = result.get("goals", {})

                st.success("✅ Goals updated!")
                st.divider()
                st.subheader("Your New Targets")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Daily Calories", f"{goals.get('target_calories', 0):.0f} kcal")
                c2.metric("Protein", f"{goals.get('protein_g', 0):.0f}g")
                c3.metric("Carbs", f"{goals.get('carbs_g', 0):.0f}g")
                c4.metric("Fat", f"{goals.get('fat_g', 0):.0f}g")

                st.divider()
                st.subheader("How we got there")
                c1, c2, c3 = st.columns(3)
                c1.metric("BMR", f"{goals.get('bmr', 0):.0f} kcal", help="Calories your body needs at complete rest")
                c2.metric("TDEE", f"{goals.get('tdee', 0):.0f} kcal", help="Maintenance calories with your activity level")
                adj = goals.get('adjustment', 0)
                c3.metric("Goal adjustment", f"{'+' if adj >= 0 else ''}{adj} kcal")

                bmi = goals.get("bmi", 0)
                bmi_label = "Underweight" if bmi < 18.5 else "Normal" if bmi < 25 else "Overweight" if bmi < 30 else "Obese"
                st.info(f"BMI: **{bmi}** ({bmi_label})")

                st.cache_data.clear()
                st.session_state["profile_saved"] = True

            except Exception as e:
                st.error(f"Could not update profile: {e}")

    else:
        # Show current calculated values without saving
        st.divider()
        st.subheader("Current Goals")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Daily Calories", f"{profile.get('calorie_goal', 0):.0f} kcal")
        c2.metric("Protein", f"{profile.get('protein_goal', 0):.0f}g")
        c3.metric("Carbs", f"{profile.get('carbs_goal', 0):.0f}g")
        c4.metric("Fat", f"{profile.get('fat_goal', 0):.0f}g")