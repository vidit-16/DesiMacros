"""
DesiMacros FastAPI Backend
"""

import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IS_SQLITE, DailyLog, MealEntry, User, get_db, init_db
from app.services.insights import detect_patterns, get_daily_alerts, get_weekly_summary
from app.services.meal_parser import MealParserError, get_meal_parser
from app.services.nutrition import init_ifct_db, lookup_nutrition
from app.services.tdee import calculate_goals

logger = logging.getLogger(__name__)

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
    log_date: str | None = None

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
    entries: list[MealEntryOut]
    daily_totals: dict

class ProfileUpdateRequest(BaseModel):
    # Bounds reject values the formulas cannot use: a height of 0 used to raise
    # ZeroDivisionError in the BMI step (a 500), and a negative weight produced
    # negative calorie targets that were then saved to the profile.
    name: str = Field("Vidit", max_length=100)
    age: int = Field(20, ge=10, le=120)
    gender: Literal["male", "female"] = "male"
    height_cm: float = Field(170.0, ge=50, le=280)
    weight_kg: float = Field(65.0, ge=20, le=400)
    activity_level: Literal["sedentary", "light", "moderate", "active", "very_active"] = "moderate"
    goal_type: Literal["cut", "mild_cut", "maintain", "mild_bulk", "bulk"] = "maintain"


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
            if not user:
                # Neither the insert nor the re-read produced a row. Returning
                # None here used to surface as AttributeError on user.id inside
                # whichever endpoint was called.
                raise HTTPException(
                    status_code=500, detail="Could not create or load your profile."
                ) from None
        return user

    user = db.query(User).filter(User.token.is_(None)).first()
    if not user:
        raise HTTPException(
        status_code=500,
        detail="No local profile found. Restart the API to seed one.",
    )
    return user



# ── Shared helpers ─────────────────────────────────────────────────────────────

WEEK_LENGTH = 7


def _parse_date(value: str | None, field: str) -> date | None:
    """Parse a YYYY-MM-DD query parameter, or reject it with a 400.

    Every endpoint that takes a date goes through this. Parsing inline meant an
    unparseable date raised ValueError and surfaced as a 500, so a typo in the
    UI looked like a server fault.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field}. Use YYYY-MM-DD."
        ) from None


def _entries_between(db: Session, user: User, start: date, end: date) -> list[MealEntry]:
    """Every meal entry for a user within an inclusive date range."""
    return (
        db.query(MealEntry)
        .join(DailyLog)
        .filter(
            DailyLog.user_id == user.id,
            DailyLog.log_date >= start,
            DailyLog.log_date <= end,
        )
        .all()
    )


def _entries_on(db: Session, user: User, target_date: date) -> list[MealEntry]:
    return _entries_between(db, user, target_date, target_date)


def _week_window(today: date | None = None) -> tuple[date, date]:
    """The seven-day window ending today, inclusive."""
    end = today or date.today()
    return end - timedelta(days=WEEK_LENGTH - 1), end


def _weekly_rows(db: Session, user: User) -> list[dict]:
    """One row per day for the last week, oldest first.

    Built from a single query. The three weekly endpoints each used to run
    seven queries of their own, with the loop body copy-pasted between them.
    """
    start, end = _week_window()

    by_date: dict[date, list[MealEntry]] = {}
    for entry in _entries_between(db, user, start, end):
        by_date.setdefault(entry.daily_log.log_date, []).append(entry)

    rows = []
    for offset in range(WEEK_LENGTH):
        day = start + timedelta(days=offset)
        entries = by_date.get(day, [])
        rows.append({
            "date": str(day),
            "totals": _compute_totals(entries, user),
            "logged": len(entries) > 0,
        })
    return rows


def _user_goals(user: User) -> dict:
    """Goal names as the insights module expects them."""
    return {
        "calories": user.calorie_goal,
        "protein": user.protein_goal,
        "carbs": user.carbs_goal,
        "fat": user.fat_goal,
    }


def _entry_dict(entry: MealEntry, include_source: bool = False) -> dict:
    payload = {
        "id": entry.id, "food_name": entry.food_name, "quantity": entry.quantity,
        "unit": entry.unit, "calories": entry.calories, "protein": entry.protein,
        "carbs": entry.carbs, "fat": entry.fat,
    }
    if include_source:
        payload["source"] = entry.source
    return payload


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
def log_meal(
    req: LogMealRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    log_date = _parse_date(req.log_date, "log_date") or date.today()

    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Tell me what you ate.")

    try:
        parser = get_meal_parser()
        parsed = parser.parse(req.text)
    except MealParserError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        # The parser handles bad model output itself, so anything reaching here
        # is unexpected. Log it rather than only reporting it to the caller.
        logger.exception("meal parsing failed")
        raise HTTPException(status_code=502, detail=f"Could not parse that meal: {e}") from e

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
        entries=[
            MealEntryOut(**{k: getattr(e, k) for k in MealEntryOut.model_fields})
            for e in saved_entries
        ],
        daily_totals=daily_totals,
    )


@app.get("/api/history")
def get_history(
    start_date: str | None = None, end_date: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    end = _parse_date(end_date, "end_date") or date.today()
    start = _parse_date(start_date, "start_date") or end - timedelta(days=WEEK_LENGTH - 1)

    if start > end:
        raise HTTPException(status_code=400, detail="start_date is after end_date.")

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
            day["entries"].append(_entry_dict(e))

    result = []
    for key, day in days.items():
        day["totals"] = _compute_totals(entries_by_date[key], user)
        result.append(day)

    return {"history": result, "start_date": str(start), "end_date": str(end)}


@app.get("/api/summary")
def get_summary(
    log_date: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target_date = _parse_date(log_date, "log_date") or date.today()
    entries = _entries_on(db, user, target_date)

    return {
        "date": str(target_date),
        "totals": _compute_totals(entries, user),
        "entries": [_entry_dict(e, include_source=True) for e in entries],
    }


@app.delete("/api/entry/{entry_id}")
def delete_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
def get_weekly(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"weekly": _weekly_rows(db, user), "goals": _user_goals(user)}


@app.get("/api/alerts")
def get_alerts(
    log_date: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target_date = _parse_date(log_date, "log_date") or date.today()
    totals = _compute_totals(_entries_on(db, user, target_date), user)
    goals = {
        "calorie_goal": user.calorie_goal, "protein_goal": user.protein_goal,
        "carbs_goal": user.carbs_goal, "fat_goal": user.fat_goal,
    }
    return {"date": str(target_date), "alerts": get_daily_alerts(totals, goals), "totals": totals}


@app.get("/api/weekly-summary")
def weekly_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    weekly = _weekly_rows(db, user)
    return {
        "summary": get_weekly_summary(weekly, _user_goals(user)),
        "days_logged": sum(1 for day in weekly if day["logged"]),
    }


@app.get("/api/patterns")
def weekly_patterns(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"patterns": detect_patterns(_weekly_rows(db, user), _user_goals(user))}


@app.get("/api/profile")
def get_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
def update_profile(
    req: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.name = req.name
    user.age = req.age
    user.gender = req.gender
    user.height_cm = req.height_cm
    user.weight_kg = req.weight_kg
    user.activity_level = req.activity_level
    user.goal_type = req.goal_type

    goals = calculate_goals(
        req.weight_kg, req.height_cm, req.age,
        req.gender, req.activity_level, req.goal_type,
    )

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
    return calculate_goals(
        req.weight_kg, req.height_cm, req.age,
        req.gender, req.activity_level, req.goal_type,
    )


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
    totals["protein_pct"] = (
        round((totals["protein"] / user.protein_goal) * 100) if user.protein_goal else 0
    )
    totals["calorie_pct"] = (
        round((totals["calories"] / user.calorie_goal) * 100) if user.calorie_goal else 0
    )
    return totals
