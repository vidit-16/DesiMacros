"""Shared fixtures.

Every test runs against throwaway storage: a temporary IFCT database seeded
from IFCT_SEED_DATA, and a fresh SQLite file per API test. The Groq and USDA
clients are mocked wherever they appear, so the whole suite runs with no API
keys and no network.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, User, get_db


@pytest.fixture(scope="session", autouse=True)
def ifct_db(tmp_path_factory):
    """Seed a temporary IFCT database and point the module at it."""
    path = tmp_path_factory.mktemp("ifct") / "ifct.db"
    os.environ["IFCT_DB_PATH"] = str(path)

    from app.services import nutrition

    nutrition.IFCT_DB_PATH = str(path)
    nutrition.init_ifct_db()
    return str(path)


@pytest.fixture
def no_usda(monkeypatch):
    """Force the USDA fallback and model estimates to return nothing.

    Most lookup tests care about IFCT resolution. Without this, a missing food
    would depend on whether a USDA or Groq key happens to be set in the
    environment, and tests would make network calls.
    """
    from app.services import nutrition

    monkeypatch.setattr(nutrition, "search_usda", lambda name: None)
    monkeypatch.setattr(nutrition, "ESTIMATES_ENABLED", False)


# ── API fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def local_user(db_session):
    """The token-less profile the app falls back to when no header is sent."""
    user = User(
        name="Local", token=None, age=22, gender="male",
        height_cm=175.0, weight_kg=70.0,
        activity_level="moderate", goal_type="maintain",
        calorie_goal=2600.0, protein_goal=126.0,
        carbs_goal=300.0, fat_goal=72.0,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session, monkeypatch):
    """A TestClient wired to the temp database, with lifespan startup skipped.

    The real lifespan calls init_db(), which would build tables in the app's
    configured database rather than the temp one.
    """
    from app.api import main

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "init_ifct_db", lambda: None)

    main.app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


@pytest.fixture
def stub_parser(monkeypatch):
    """Replace the meal parser with one that returns a scripted result."""
    from app.api import main
    from app.services.meal_parser import ParsedMealItem, ParsedMealResponse

    def install(items=None, confidence="high", clarification="", error=None):
        def get_parser():
            if error:
                raise error

            class Stub:
                def parse(self, text):
                    return ParsedMealResponse(
                        items=[ParsedMealItem(**item) for item in (items or [])],
                        raw_input=text,
                        parse_confidence=confidence,
                        clarification_needed=clarification,
                    )

            return Stub()

        monkeypatch.setattr(main, "get_meal_parser", get_parser)

    return install


@pytest.fixture
def stub_nutrition(monkeypatch):
    """Deterministic macros, so API tests don't depend on the IFCT table."""
    from app.api import main

    def fake_lookup(food_name, quantity, unit):
        return {
            "food_name": food_name, "quantity": quantity, "unit": unit,
            "grams": quantity * 100, "calories": 100.0 * quantity,
            "protein": 5.0 * quantity, "carbs": 12.0 * quantity,
            "fat": 3.0 * quantity, "fiber": 1.0 * quantity, "source": "ifct",
        }

    monkeypatch.setattr(main, "lookup_nutrition", fake_lookup)
    return fake_lookup


@pytest.fixture
def logged_meal(client, local_user, stub_parser, stub_nutrition):
    """Log one two-item meal for today and return the response body."""
    stub_parser(items=[
        {"food_name": "dal tadka", "quantity": 1, "unit": "katori", "meal_time": "lunch"},
        {"food_name": "roti", "quantity": 2, "unit": "piece", "meal_time": "lunch"},
    ])
    response = client.post("/api/log", json={"text": "dal and 2 rotis"})
    assert response.status_code == 200
    return response.json()
