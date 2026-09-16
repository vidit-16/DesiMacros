"""Input validation, smoke and parity tests.

- Smoke: every module imports and the FastAPI app exposes its documented routes.
- Validation: profile stats the formulas cannot use are rejected with 422
  instead of crashing (height 0 used to be a ZeroDivisionError -> 500).
- Parity: the HTTP endpoint returns exactly what the core function computes,
  for every activity level x goal combination.
"""

from __future__ import annotations

import importlib
import itertools

import pytest

from app.services.tdee import ACTIVITY_MULTIPLIERS, GOAL_ADJUSTMENTS, calculate_goals

# ── Smoke ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", [
    "app.core.config",
    "app.db.models",
    "app.services.tdee",
    "app.services.nutrition",
    "app.services.meal_parser",
    "app.services.insights",
    "app.api.main",
])
def test_modules_import(module):
    importlib.import_module(module)


def test_app_exposes_documented_routes():
    from app.api.main import app

    paths = {route.path for route in app.routes}
    for expected in ["/health", "/api/log", "/api/summary", "/api/history", "/api/weekly",
                     "/api/alerts", "/api/patterns", "/api/weekly-summary", "/api/profile",
                     "/api/tdee-preview", "/api/entry/{entry_id}"]:
        assert expected in paths


# ── Validation ───────────────────────────────────────────────────────────────

VALID = dict(name="A", age=25, gender="female", height_cm=165.0, weight_kg=60.0,
             activity_level="light", goal_type="mild_cut")


@pytest.mark.parametrize("field,value", [
    ("height_cm", 0),
    ("height_cm", -170),
    ("weight_kg", 0),
    ("weight_kg", -5),
    ("age", 0),
    ("age", 500),
    ("gender", "robot"),
    ("activity_level", "couch"),
    ("goal_type", "shred"),
])
def test_unusable_stats_are_422(client, local_user, field, value):
    payload = {**VALID, field: value}
    assert client.post("/api/tdee-preview", json=payload).status_code == 422
    assert client.post("/api/profile", json=payload).status_code == 422


def test_rejected_profile_update_leaves_goals_untouched(client, local_user, db_session):
    before = local_user.calorie_goal
    client.post("/api/profile", json={**VALID, "height_cm": 0})
    db_session.refresh(local_user)
    assert local_user.calorie_goal == before


def test_valid_payload_still_accepted(client, local_user):
    assert client.post("/api/tdee-preview", json=VALID).status_code == 200


# ── Parity: API vs core ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "activity,goal", list(itertools.product(ACTIVITY_MULTIPLIERS, GOAL_ADJUSTMENTS))
)
def test_tdee_preview_matches_core_function(client, activity, goal):
    payload = {**VALID, "activity_level": activity, "goal_type": goal}
    api = client.post("/api/tdee-preview", json=payload).json()
    core = calculate_goals(60.0, 165.0, 25, "female", activity, goal)
    assert api == core
