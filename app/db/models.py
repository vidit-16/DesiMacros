from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, date, timezone
import os

def _normalize_db_url(url: str) -> str:
    """
    Hosted Postgres providers hand out postgres:// or postgresql:// URLs, both of
    which SQLAlchemy would route to psycopg2. Point them at psycopg 3 instead,
    which is the driver in requirements.txt, so a pasted connection string works.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", "sqlite:///./data/desimacros.db"))
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# data/ holds the IFCT reference table regardless of where the logs live.
os.makedirs("data", exist_ok=True)

engine_kwargs = {"pool_pre_ping": True}  # serverless Postgres drops idle connections
if IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="User")

    # Identifies one visitor's log. NULL is the local single-user profile.
    token = Column(String, unique=True, index=True, nullable=True)

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

    # Safe migration for SQLite files created by earlier versions. On Postgres
    # create_all above has already produced the current schema.
    if not IS_SQLITE:
        _seed_local_user()
        return

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
        "token":          "VARCHAR",
    }
    with engine.connect() as conn:
        for col, typedef in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
        # SQLite can't add a UNIQUE column in place, so index it afterwards.
        # Multiple NULLs are allowed, which is what the local profile uses.
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_token ON users(token)"))
        conn.commit()

    _seed_local_user()


def _seed_local_user():
    """The token-less profile used when the app runs as a single-user app."""
    db = SessionLocal()
    if not db.query(User).filter(User.token.is_(None)).first():
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