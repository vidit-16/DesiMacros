from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, date, timezone
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/desimacros.db")

os.makedirs("data", exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="User")

    # Physical stats
    age = Column(Integer, default=20)
    gender = Column(String, default="male")
    height_cm = Column(Float, default=170.0)
    weight_kg = Column(Float, default=65.0)
    activity_level = Column(String, default="moderate")
    goal_type = Column(String, default="maintain")

    # Daily goals
    calorie_goal = Column(Float, default=2000.0)
    protein_goal = Column(Float, default=80.0)
    carbs_goal = Column(Float, default=250.0)
    fat_goal = Column(Float, default=65.0)

    logs = relationship("DailyLog", back_populates="user")


class DailyLog(Base):
    __tablename__ = "daily_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), default=1)
    log_date = Column(Date, default=date.today)
    raw_input = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="logs")
    meal_entries = relationship("MealEntry", back_populates="daily_log", cascade="all, delete-orphan")


class MealEntry(Base):
    __tablename__ = "meal_entries"

    id = Column(Integer, primary_key=True, index=True)
    daily_log_id = Column(Integer, ForeignKey("daily_logs.id"))
    food_name = Column(String)
    quantity = Column(Float)
    unit = Column(String)
    calories = Column(Float, default=0.0)
    protein = Column(Float, default=0.0)
    carbs = Column(Float, default=0.0)
    fat = Column(Float, default=0.0)
    fiber = Column(Float, default=0.0)
    source = Column(String, default="unknown")

    daily_log = relationship("DailyLog", back_populates="meal_entries")


def init_db():
    Base.metadata.create_all(bind=engine)

    # Safe migration for existing DBs
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    existing = [c["name"] for c in insp.get_columns("users")]
    new_cols = {
        "age":            "INTEGER DEFAULT 20",
        "gender":         "VARCHAR DEFAULT 'male'",
        "height_cm":      "FLOAT DEFAULT 170.0",
        "weight_kg":      "FLOAT DEFAULT 65.0",
        "activity_level": "VARCHAR DEFAULT 'moderate'",
        "goal_type":      "VARCHAR DEFAULT 'maintain'",
    }
    with engine.connect() as conn:
        for col, typedef in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
        conn.commit()

    db = SessionLocal()
    if not db.query(User).first():
        default_user = User(
            name="Vidit",
            age=20, gender="male",
            height_cm=170.0, weight_kg=65.0,
            activity_level="moderate", goal_type="maintain",
            calorie_goal=2200.0, protein_goal=100.0,
            carbs_goal=280.0, fat_goal=70.0,
        )
        db.add(default_user)
        db.commit()
    db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()