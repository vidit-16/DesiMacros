"""
DesiMacros FastAPI Backend
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from datetime import date, timedelta
from typing import Optional, List

from app.db.models import init_db, get_db, DailyLog, MealEntry, User, IS_SQLITE
from app.services.meal_parser import get_meal_parser, MealParserError
from app.services.nutrition import lookup_nutrition, init_ifct_db
from app.services.insights import get_daily_alerts, get_weekly_summary, detect_patterns
from app.services.tdee import calculate_goals

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_ifct_db()
    yield


app = FastAPI(
    title="DesiMacros API",
    description="Conversational calorie tracker for Indian diets",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class LogMealRequest(BaseModel):
    text: str
    log_date: Optional[str] = None

class MealEntryOut(BaseModel):
    id: int
    food_name: str
    quantity: float
    unit: str
    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float
    source: str

class LogMealResponse(BaseModel):
    log_date: str
    raw_input: str
    parse_confidence: str
    clarification_needed: str
    entries: List[MealEntryOut]
    daily_totals: dict

class ProfileUpdateRequest(BaseModel):
    name: str = "Vidit"
    age: int = 20
    gender: str = "male"
    height_cm: float = 170.0
    weight_kg: float = 65.0
    activity_level: str = "moderate"
    goal_type: str = "maintain"


# ── Current visitor ────────────────────────────────────────────────────────────

# Stats a brand-new visitor starts with; they can change them under Profile.
GUEST_DEFAULTS = {
    "age": 22, "gender": "male", "height_cm": 170.0, "weight_kg": 65.0,
    "activity_level": "moderate", "goal_type": "maintain",
}


def get_current_user(
    x_user_token: str = Header(None, alias="X-User-Token"),
    db: Session = Depends(get_db),
) -> User:
    """
    Every visitor gets their own log, keyed by a token the UI keeps in the URL.
    Requests with no token fall back to the local profile (token IS NULL), so
    running this on your own machine behaves exactly like a single-user app.
    """
    if x_user_token:
        user = db.query(User).filter(User.token == x_user_token).first()
        if user:
            return user

        goals = calculate_goals(
            GUEST_DEFAULTS["weight_kg"], GUEST_DEFAULTS["height_cm"], GUEST_DEFAULTS["age"],
            GUEST_DEFAULTS["gender"], GUEST_DEFAULTS["activity_level"], GUEST_DEFAULTS["goal_type"],
        )
        user = User(
            token=x_user_token, name="You", **GUEST_DEFAULTS,
            calorie_goal=goals["target_calories"], protein_goal=goals["protein_g"],
            carbs_goal=goals["carbs_g"], fat_goal=goals["fat_g"],
        )
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
        except IntegrityError:
            # Another request created this visitor first - take theirs.
            db.rollback()
            user = db.query(User).filter(User.token == x_user_token).first()
        return user

    user = db.query(User).filter(User.token.is_(None)).first()
    if not user:
        raise HTTPException(status_code=500, detail="No local profile found. Restart the API to seed one.")
    return user



# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    # "sqlite" tells the UI that storage is ephemeral on a host with a
    # temporary disk, so it can warn visitors that logs will not survive.
    return {
        "status": "ok",
        "service": "DesiMacros",
        "storage": "sqlite" if IS_SQLITE else "postgres",
    }


@app.post("/api/log", response_model=LogMealResponse)
def log_meal(req: LogMealRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        log_date = date.fromisoformat(req.log_date) if req.log_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    try:
        parser = get_meal_parser()
        parsed = parser.parse(req.text)
    except MealParserError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not parse that meal: {e}")

    if not parsed.items:
        raise HTTPException(
            status_code=422,
            detail=parsed.clarification_needed
            or "Couldn't identify any food in that. Try naming the dishes, e.g. '2 rotis with dal'.",
        )

    nutrition_results = []
    for item in parsed.items:
        result = lookup_nutrition(item.food_name, item.quantity, item.unit)
        nutrition_results.append(result)

    daily_log = DailyLog(user_id=user.id, log_date=log_date, raw_input=req.text)
    db.add(daily_log)
    db.flush()

    saved_entries = []
    for result in nutrition_results:
        entry = MealEntry(
            daily_log_id=daily_log.id,
            food_name=result["food_name"],
            quantity=result["quantity"],
            unit=result["unit"],
            calories=result["calories"],
            protein=result["protein"],
            carbs=result["carbs"],
            fat=result["fat"],
            fiber=result["fiber"],
            source=result["source"],
        )
        db.add(entry)
        saved_entries.append(entry)

    db.commit()
    for e in saved_entries:
        db.refresh(e)

    all_entries_today = (
        db.query(MealEntry)
        .join(DailyLog)
        .filter(DailyLog.log_date == log_date, DailyLog.user_id == user.id)
        .all()
    )
    daily_totals = _compute_totals(all_entries_today, user)

    return LogMealResponse(
        log_date=str(log_date),
        raw_input=req.text,
        parse_confidence=parsed.parse_confidence,
        clarification_needed=parsed.clarification_needed,
        entries=[MealEntryOut(**{k: getattr(e, k) for k in MealEntryOut.model_fields}) for e in saved_entries],
        daily_totals=daily_totals,
    )


@app.get("/api/history")
def get_history(start_date: Optional[str] = None, end_date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=6)

    logs = (
        db.query(DailyLog)
        .filter(DailyLog.user_id == user.id, DailyLog.log_date >= start, DailyLog.log_date <= end)
        .order_by(DailyLog.log_date.desc(), DailyLog.id.asc())
        .all()
    )

    # A day can have several DailyLog rows (one per message logged). Merge them
    # so each date shows up once with its combined totals.
    days: dict[str, dict] = {}
    entries_by_date: dict[str, list] = {}

    for log in logs:
        key = str(log.log_date)
        day = days.setdefault(key, {"date": key, "raw_inputs": [], "entries": []})
        entries_by_date.setdefault(key, [])
        if log.raw_input:
            day["raw_inputs"].append(log.raw_input)
        for e in log.meal_entries:
            entries_by_date[key].append(e)
            day["entries"].append({
                "id": e.id, "food_name": e.food_name, "quantity": e.quantity, "unit": e.unit,
                "calories": e.calories, "protein": e.protein, "carbs": e.carbs, "fat": e.fat,
            })

    result = []
    for key, day in days.items():
        day["totals"] = _compute_totals(entries_by_date[key], user)
        result.append(day)

    return {"history": result, "start_date": str(start), "end_date": str(end)}


@app.get("/api/summary")
def get_summary(log_date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target_date = date.fromisoformat(log_date) if log_date else date.today()

    entries = (
        db.query(MealEntry)
        .join(DailyLog)
        .filter(DailyLog.log_date == target_date, DailyLog.user_id == user.id)
        .all()
    )

    return {
        "date": str(target_date),
        "totals": _compute_totals(entries, user),
        "entries": [{
            "id": e.id, "food_name": e.food_name, "quantity": e.quantity, "unit": e.unit,
            "calories": e.calories, "protein": e.protein, "carbs": e.carbs, "fat": e.fat,
            "source": e.source,
        } for e in entries],
    }


@app.delete("/api/entry/{entry_id}")
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remove a single mis-logged meal entry."""
    entry = (
        db.query(MealEntry)
        .join(DailyLog)
        .filter(MealEntry.id == entry_id, DailyLog.user_id == user.id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Meal entry not found.")

    daily_log_id = entry.daily_log_id
    db.delete(entry)
    db.flush()

    # Drop the parent log too once its last entry is gone.
    remaining = db.query(MealEntry).filter(MealEntry.daily_log_id == daily_log_id).count()
    if remaining == 0:
        log = db.query(DailyLog).filter(DailyLog.id == daily_log_id).first()
        if log:
            db.delete(log)

    db.commit()
    return {"message": "Entry deleted", "entry_id": entry_id}


@app.get("/api/weekly")
def get_weekly(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = date.today()

    weekly = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        entries = (
            db.query(MealEntry)
            .join(DailyLog)
            .filter(DailyLog.log_date == d, DailyLog.user_id == user.id)
            .all()
        )
        totals = _compute_totals(entries, user)
        weekly.append({"date": str(d), "totals": totals, "logged": len(entries) > 0})

    return {"weekly": weekly, "goals": {
        "calories": user.calorie_goal,
        "protein": user.protein_goal,
        "carbs": user.carbs_goal,
        "fat": user.fat_goal,
    }}


@app.get("/api/alerts")
def get_alerts(log_date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target_date = date.fromisoformat(log_date) if log_date else date.today()

    entries = (
        db.query(MealEntry)
        .join(DailyLog)
        .filter(DailyLog.log_date == target_date, DailyLog.user_id == user.id)
        .all()
    )
    totals = _compute_totals(entries, user)
    goals = {"calorie_goal": user.calorie_goal, "protein_goal": user.protein_goal, "carbs_goal": user.carbs_goal, "fat_goal": user.fat_goal}
    return {"date": str(target_date), "alerts": get_daily_alerts(totals, goals), "totals": totals}


@app.get("/api/weekly-summary")
def weekly_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = date.today()

    weekly = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        entries = (db.query(MealEntry).join(DailyLog).filter(DailyLog.log_date == d, DailyLog.user_id == user.id).all())
        totals = _compute_totals(entries, user)
        weekly.append({"date": str(d), "totals": totals, "logged": len(entries) > 0})

    goals = {"calories": user.calorie_goal, "protein": user.protein_goal, "carbs": user.carbs_goal, "fat": user.fat_goal}
    summary = get_weekly_summary(weekly, goals)
    return {"summary": summary, "days_logged": sum(1 for d in weekly if d["logged"])}


@app.get("/api/patterns")
def weekly_patterns(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = date.today()

    weekly = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        entries = (db.query(MealEntry).join(DailyLog).filter(DailyLog.log_date == d, DailyLog.user_id == user.id).all())
        totals = _compute_totals(entries, user)
        weekly.append({"date": str(d), "totals": totals, "logged": len(entries) > 0})

    goals = {"calories": user.calorie_goal, "protein": user.protein_goal, "carbs": user.carbs_goal, "fat": user.fat_goal}
    return {"patterns": detect_patterns(weekly, goals)}


@app.get("/api/profile")
def get_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {
        "name": user.name,
        "age": user.age,
        "gender": user.gender,
        "height_cm": user.height_cm,
        "weight_kg": user.weight_kg,
        "activity_level": user.activity_level,
        "goal_type": user.goal_type,
        "calorie_goal": user.calorie_goal,
        "protein_goal": user.protein_goal,
        "carbs_goal": user.carbs_goal,
        "fat_goal": user.fat_goal,
    }


@app.post("/api/profile")
def update_profile(req: ProfileUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):

    user.name = req.name
    user.age = req.age
    user.gender = req.gender
    user.height_cm = req.height_cm
    user.weight_kg = req.weight_kg
    user.activity_level = req.activity_level
    user.goal_type = req.goal_type

    goals = calculate_goals(req.weight_kg, req.height_cm, req.age, req.gender, req.activity_level, req.goal_type)

    user.calorie_goal = goals["target_calories"]
    user.protein_goal = goals["protein_g"]
    user.carbs_goal = goals["carbs_g"]
    user.fat_goal = goals["fat_g"]

    db.commit()
    db.refresh(user)

    return {
        "message": "Profile updated successfully",
        "goals": goals,
        "calorie_goal": user.calorie_goal,
        "protein_goal": user.protein_goal,
        "carbs_goal": user.carbs_goal,
        "fat_goal": user.fat_goal,
    }


@app.post("/api/tdee-preview")
def tdee_preview(req: ProfileUpdateRequest):
    return calculate_goals(req.weight_kg, req.height_cm, req.age, req.gender, req.activity_level, req.goal_type)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_totals(entries, user) -> dict:
    totals = {
        "calories": round(sum(e.calories for e in entries), 1),
        "protein": round(sum(e.protein for e in entries), 1),
        "carbs": round(sum(e.carbs for e in entries), 1),
        "fat": round(sum(e.fat for e in entries), 1),
        "fiber": round(sum(e.fiber for e in entries), 1),
    }
    totals["calories_remaining"] = round(user.calorie_goal - totals["calories"], 1)
    totals["protein_pct"] = round((totals["protein"] / user.protein_goal) * 100) if user.protein_goal else 0
    totals["calorie_pct"] = round((totals["calories"] / user.calorie_goal) * 100) if user.calorie_goal else 0
    return totals