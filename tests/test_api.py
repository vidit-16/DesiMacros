"""API endpoints.

No network and no API key: the meal parser and the nutrition lookup are stubbed,
and each test gets its own SQLite database.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db.models import DailyLog, MealEntry, User

# ── health ───────────────────────────────────────────────────────────────────


def test_health_reports_storage_backend(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["storage"] in {"sqlite", "postgres"}


# ── visitor identity ─────────────────────────────────────────────────────────


def test_no_token_falls_back_to_the_local_profile(client, local_user):
    assert client.get("/api/profile").json()["name"] == "Local"


def test_a_new_token_creates_its_own_profile(client, db_session, local_user):
    body = client.get("/api/profile", headers={"X-User-Token": "visitor-one"}).json()
    assert body["name"] == "You"
    assert db_session.query(User).filter(User.token == "visitor-one").count() == 1


def test_the_same_token_reuses_its_profile(client, db_session, local_user):
    headers = {"X-User-Token": "visitor-one"}
    client.post("/api/profile", json={"name": "Asha", "age": 30}, headers=headers)
    assert client.get("/api/profile", headers=headers).json()["name"] == "Asha"
    assert db_session.query(User).filter(User.token == "visitor-one").count() == 1


def test_a_new_visitor_starts_with_computed_goals(client, local_user):
    body = client.get("/api/profile", headers={"X-User-Token": "fresh"}).json()
    assert body["calorie_goal"] > 0
    assert body["protein_goal"] > 0


def test_missing_local_profile_is_a_named_error(client, db_session):
    """No seeded profile and no token: a clear 500, not an AttributeError."""
    response = client.get("/api/profile")
    assert response.status_code == 500
    assert "local profile" in response.json()["detail"].lower()


def test_one_visitor_cannot_see_another_visitors_log(
    client, local_user, stub_parser, stub_nutrition
):
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    client.post("/api/log", json={"text": "poha"}, headers={"X-User-Token": "alice"})

    bob = client.get("/api/summary", headers={"X-User-Token": "bob"}).json()
    assert bob["entries"] == []
    assert bob["totals"]["calories"] == 0


# ── logging a meal ───────────────────────────────────────────────────────────


def test_log_meal_saves_entries_and_returns_totals(logged_meal):
    assert logged_meal["parse_confidence"] == "high"
    assert [e["food_name"] for e in logged_meal["entries"]] == ["dal tadka", "roti"]
    # 1 katori + 2 pieces at 100 kcal per unit of quantity.
    assert logged_meal["daily_totals"]["calories"] == 300.0


def test_log_meal_persists_rows(logged_meal, db_session, local_user):
    assert db_session.query(MealEntry).count() == 2
    log = db_session.query(DailyLog).one()
    assert log.user_id == local_user.id
    assert log.raw_input == "dal and 2 rotis"


def test_totals_accumulate_across_several_messages(
    client, local_user, stub_parser, stub_nutrition
):
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    client.post("/api/log", json={"text": "poha"})
    second = client.post("/api/log", json={"text": "more poha"}).json()
    assert second["daily_totals"]["calories"] == 200.0


def test_log_meal_accepts_an_explicit_date(client, local_user, stub_parser, stub_nutrition):
    stub_parser(items=[{"food_name": "idli", "quantity": 2, "unit": "piece"}])
    yesterday = str(date.today() - timedelta(days=1))
    body = client.post("/api/log", json={"text": "2 idli", "log_date": yesterday}).json()
    assert body["log_date"] == yesterday


def test_remaining_calories_come_off_the_goal(logged_meal, local_user):
    totals = logged_meal["daily_totals"]
    assert totals["calories_remaining"] == pytest.approx(local_user.calorie_goal - 300.0)


# ── logging failures ─────────────────────────────────────────────────────────


def test_an_unparseable_meal_is_422_with_the_clarification(
    client, local_user, stub_parser, stub_nutrition
):
    stub_parser(items=[], confidence="low", clarification="How much rice?")
    response = client.post("/api/log", json={"text": "some food"})
    assert response.status_code == 422
    assert response.json()["detail"] == "How much rice?"


def test_no_items_and_no_clarification_still_explains_itself(
    client, local_user, stub_parser, stub_nutrition
):
    stub_parser(items=[])
    response = client.post("/api/log", json={"text": "mmm"})
    assert response.status_code == 422
    assert "Couldn't identify" in response.json()["detail"]


def test_blank_text_is_rejected(client, local_user, stub_parser, stub_nutrition):
    stub_parser(items=[])
    assert client.post("/api/log", json={"text": "   "}).status_code == 422


def test_a_missing_api_key_surfaces_as_503(client, local_user, stub_parser):
    from app.services.meal_parser import MealParserError

    stub_parser(error=MealParserError("GROQ_API_KEY is not set."))
    response = client.post("/api/log", json={"text": "2 rotis"})
    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]


def test_an_unexpected_parser_failure_surfaces_as_502(client, local_user, stub_parser):
    stub_parser(error=RuntimeError("upstream exploded"))
    assert client.post("/api/log", json={"text": "2 rotis"}).status_code == 502


def test_a_bad_log_date_is_400_not_500(client, local_user, stub_parser, stub_nutrition):
    stub_parser(items=[{"food_name": "roti", "quantity": 1, "unit": "piece"}])
    response = client.post("/api/log", json={"text": "roti", "log_date": "14-09-2026"})
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.json()["detail"]


def test_text_is_required(client, local_user):
    assert client.post("/api/log", json={}).status_code == 422


# ── date parsing across endpoints ────────────────────────────────────────────
#
# Only /api/log used to validate its date. The others called fromisoformat
# inline, so a malformed value raised ValueError and surfaced as a 500.


@pytest.mark.parametrize(
    "url",
    [
        "/api/summary?log_date=not-a-date",
        "/api/alerts?log_date=2026-13-45",
        "/api/history?start_date=yesterday",
        "/api/history?end_date=14%2F09%2F2026",
    ],
)
def test_malformed_dates_are_400(client, local_user, url):
    response = client.get(url)
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.json()["detail"]


def test_a_reversed_range_is_rejected(client, local_user):
    response = client.get("/api/history?start_date=2026-09-10&end_date=2026-09-01")
    assert response.status_code == 400


# ── summary ──────────────────────────────────────────────────────────────────


def test_summary_lists_todays_entries(logged_meal, client):
    body = client.get("/api/summary").json()
    assert body["date"] == str(date.today())
    assert len(body["entries"]) == 2
    assert body["entries"][0]["source"] == "ifct"


def test_summary_of_an_empty_day_is_zeroed(client, local_user):
    body = client.get("/api/summary?log_date=2026-01-01").json()
    assert body["entries"] == []
    assert body["totals"]["calories"] == 0


# ── history ──────────────────────────────────────────────────────────────────


def test_history_defaults_to_the_last_week(logged_meal, client):
    body = client.get("/api/history").json()
    start = date.fromisoformat(body["start_date"])
    end = date.fromisoformat(body["end_date"])
    assert (end - start).days == 6
    assert len(body["history"]) == 1


def test_history_merges_several_messages_from_one_day(
    client, local_user, stub_parser, stub_nutrition
):
    """A day has one DailyLog per message; history must show the date once."""
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    client.post("/api/log", json={"text": "poha"})
    client.post("/api/log", json={"text": "chai"})

    history = client.get("/api/history").json()["history"]
    assert len(history) == 1
    assert len(history[0]["raw_inputs"]) == 2
    assert history[0]["totals"]["calories"] == 200.0


def test_history_excludes_days_outside_the_range(
    client, local_user, stub_parser, stub_nutrition
):
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    old = str(date.today() - timedelta(days=30))
    client.post("/api/log", json={"text": "poha", "log_date": old})
    assert client.get("/api/history").json()["history"] == []


# ── weekly views ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["/api/weekly", "/api/weekly-summary", "/api/patterns"])
def test_weekly_endpoints_respond_for_an_empty_log(client, local_user, url):
    assert client.get(url).status_code == 200


def test_weekly_always_covers_seven_days_ending_today(logged_meal, client):
    weekly = client.get("/api/weekly").json()["weekly"]
    assert len(weekly) == 7
    assert weekly[-1]["date"] == str(date.today())
    assert weekly[0]["date"] == str(date.today() - timedelta(days=6))


def test_weekly_days_are_in_ascending_order(client, local_user):
    dates = [row["date"] for row in client.get("/api/weekly").json()["weekly"]]
    assert dates == sorted(dates)


def test_weekly_marks_which_days_were_logged(logged_meal, client):
    weekly = client.get("/api/weekly").json()["weekly"]
    assert weekly[-1]["logged"] is True
    assert weekly[-1]["totals"]["calories"] == 300.0
    assert all(row["logged"] is False for row in weekly[:-1])


def test_weekly_reports_the_users_goals(logged_meal, client, local_user):
    goals = client.get("/api/weekly").json()["goals"]
    assert goals["calories"] == local_user.calorie_goal
    assert goals["protein"] == local_user.protein_goal


def test_weekly_summary_counts_logged_days(logged_meal, client):
    assert client.get("/api/weekly-summary").json()["days_logged"] == 1


def test_patterns_asks_for_more_data_when_there_is_little(client, local_user):
    patterns = client.get("/api/patterns").json()["patterns"]
    assert any("3 days" in p for p in patterns)


# ── deleting entries ─────────────────────────────────────────────────────────


def test_deleting_an_entry_removes_it_from_the_totals(logged_meal, client, db_session):
    entry_id = logged_meal["entries"][0]["id"]
    assert client.delete(f"/api/entry/{entry_id}").status_code == 200

    assert db_session.query(MealEntry).count() == 1
    assert client.get("/api/summary").json()["totals"]["calories"] == 200.0


def test_deleting_the_last_entry_removes_the_parent_log(
    client, local_user, db_session, stub_parser, stub_nutrition
):
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    body = client.post("/api/log", json={"text": "poha"}).json()

    client.delete(f"/api/entry/{body['entries'][0]['id']}")
    assert db_session.query(DailyLog).count() == 0


def test_deleting_a_missing_entry_is_404(client, local_user):
    assert client.delete("/api/entry/9999").status_code == 404


def test_a_visitor_cannot_delete_someone_elses_entry(
    client, local_user, stub_parser, stub_nutrition
):
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    body = client.post(
        "/api/log", json={"text": "poha"}, headers={"X-User-Token": "alice"}
    ).json()

    response = client.delete(
        f"/api/entry/{body['entries'][0]['id']}", headers={"X-User-Token": "bob"}
    )
    assert response.status_code == 404


# ── profile ──────────────────────────────────────────────────────────────────


def test_profile_returns_stats_and_goals(client, local_user):
    body = client.get("/api/profile").json()
    assert body["weight_kg"] == 70.0
    assert set(body) >= {"name", "age", "gender", "height_cm", "weight_kg",
                         "activity_level", "goal_type", "calorie_goal"}


def test_updating_the_profile_recalculates_goals(client, local_user, db_session):
    response = client.post("/api/profile", json={
        "name": "Vid", "age": 22, "gender": "male",
        "height_cm": 175.0, "weight_kg": 70.0,
        "activity_level": "active", "goal_type": "bulk",
    })
    assert response.status_code == 200

    body = response.json()
    assert body["goals"]["goal_type"] == "bulk"
    assert body["calorie_goal"] == body["goals"]["target_calories"]

    db_session.refresh(local_user)
    assert local_user.name == "Vid"
    assert local_user.calorie_goal == body["calorie_goal"]


def test_a_cut_lowers_the_target_a_bulk_raises_it(client, local_user):
    base = dict(name="Vid", age=22, gender="male", height_cm=175.0,
                weight_kg=70.0, activity_level="moderate")

    maintain = client.post("/api/profile", json={**base, "goal_type": "maintain"}).json()
    cut = client.post("/api/profile", json={**base, "goal_type": "cut"}).json()
    bulk = client.post("/api/profile", json={**base, "goal_type": "bulk"}).json()

    assert cut["calorie_goal"] < maintain["calorie_goal"] < bulk["calorie_goal"]


def test_goals_feed_straight_into_the_daily_totals(
    client, local_user, stub_parser, stub_nutrition
):
    client.post("/api/profile", json={
        "name": "Vid", "age": 22, "gender": "male", "height_cm": 175.0,
        "weight_kg": 70.0, "activity_level": "sedentary", "goal_type": "cut",
    })
    stub_parser(items=[{"food_name": "poha", "quantity": 1, "unit": "katori"}])
    totals = client.post("/api/log", json={"text": "poha"}).json()["daily_totals"]

    goal = client.get("/api/profile").json()["calorie_goal"]
    assert totals["calories_remaining"] == pytest.approx(goal - 100.0)


# ── tdee preview ─────────────────────────────────────────────────────────────


def test_tdee_preview_needs_no_profile_and_writes_nothing(client, db_session):
    response = client.post("/api/tdee-preview", json={
        "name": "Anyone", "age": 25, "gender": "female", "height_cm": 160.0,
        "weight_kg": 55.0, "activity_level": "light", "goal_type": "mild_cut",
    })
    assert response.status_code == 200
    assert response.json()["target_calories"] > 0
    assert db_session.query(User).count() == 0


def test_tdee_preview_matches_the_saved_goals(client, local_user):
    payload = {
        "name": "Vid", "age": 22, "gender": "male", "height_cm": 175.0,
        "weight_kg": 70.0, "activity_level": "moderate", "goal_type": "maintain",
    }
    preview = client.post("/api/tdee-preview", json=payload).json()
    saved = client.post("/api/profile", json=payload).json()
    assert preview["target_calories"] == saved["calorie_goal"]


# ── alerts ───────────────────────────────────────────────────────────────────


def test_alerts_report_against_the_day_requested(logged_meal, client):
    body = client.get("/api/alerts").json()
    assert body["date"] == str(date.today())
    assert isinstance(body["alerts"], list)
    assert body["totals"]["calories"] == 300.0
