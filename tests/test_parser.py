"""Meal parser.

The Groq client is mocked throughout: these tests check how the parser handles
what the model returns, not whether the model is any good. That means no API
key, no network, and no cost to run them in CI.

The old version of this file called the live API and printed results without
asserting anything, so a broken parser passed silently.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import meal_parser as mp
from app.services.meal_parser import MealParser, MealParserError


def make_response(content: str):
    """Mimic the shape of a Groq chat completion."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeGroq:
    """Records calls and returns a scripted response, or raises."""

    def __init__(self, content="{}", fail_times=0, error=None):
        self.content = content
        self.fail_times = fail_times
        self.error = error or RuntimeError("groq is unavailable")
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_times:
            raise self.error
        return make_response(self.content)


@pytest.fixture
def parser(monkeypatch):
    """A MealParser wired to a fake client, with a key present."""

    def build(content="{}", fail_times=0, error=None):
        monkeypatch.setattr(mp.settings, "groq_api_key", "test-key")
        fake = FakeGroq(content, fail_times, error)
        monkeypatch.setattr(mp, "Groq", lambda api_key: fake)
        instance = MealParser()
        return instance, fake

    return build


# ── construction ─────────────────────────────────────────────────────────────


def test_missing_api_key_fails_with_a_useful_message(monkeypatch):
    monkeypatch.setattr(mp.settings, "groq_api_key", "")
    with pytest.raises(MealParserError, match="GROQ_API_KEY"):
        MealParser()


# ── parsing model output ─────────────────────────────────────────────────────


VALID = json.dumps(
    {
        "items": [
            {"food_name": "dal tadka", "quantity": 1.5, "unit": "katori", "meal_time": "lunch"},
            {"food_name": "roti", "quantity": 2, "unit": "piece", "meal_time": "lunch"},
        ],
        "parse_confidence": "high",
        "clarification_needed": "",
    }
)


def test_parses_items_into_the_response_model(parser):
    instance, _ = parser(VALID)
    result = instance.parse("2 rotis with dal for lunch")

    assert result.parse_confidence == "high"
    assert result.raw_input == "2 rotis with dal for lunch"
    assert [item.food_name for item in result.items] == ["dal tadka", "roti"]
    assert result.items[0].quantity == 1.5
    assert result.items[0].unit == "katori"
    assert result.items[1].meal_time == "lunch"


def test_optional_fields_get_defaults(parser):
    instance, _ = parser(json.dumps({"items": [{"food_name": "poha", "quantity": 1, "unit": "katori"}]}))
    item = instance.parse("poha").items[0]
    assert item.meal_time == "unknown"
    assert item.notes == ""


def test_confidence_defaults_to_medium_when_absent(parser):
    instance, _ = parser(json.dumps({"items": []}))
    assert instance.parse("something").parse_confidence == "medium"


def test_clarification_is_carried_through(parser):
    instance, _ = parser(
        json.dumps({"items": [], "clarification_needed": "How much rice?", "parse_confidence": "low"})
    )
    assert instance.parse("some rice").clarification_needed == "How much rice?"


# ── model misbehaviour ───────────────────────────────────────────────────────


def test_markdown_fenced_json_is_still_parsed(parser):
    """Models wrap JSON in code fences despite being told not to."""
    instance, _ = parser(f"```json\n{VALID}\n```")
    assert len(instance.parse("anything").items) == 2


def test_unparseable_output_degrades_instead_of_crashing(parser):
    instance, _ = parser("I'm sorry, I can't help with that.")
    result = instance.parse("2 rotis")

    assert result.items == []
    assert result.parse_confidence == "low"
    assert "Could not parse" in result.clarification_needed


def test_empty_content_degrades_instead_of_crashing(parser):
    instance, _ = parser("")
    assert instance.parse("2 rotis").parse_confidence == "low"


def test_missing_items_key_yields_no_items(parser):
    instance, _ = parser(json.dumps({"parse_confidence": "high"}))
    assert instance.parse("anything").items == []


# ── transport failures ───────────────────────────────────────────────────────


def test_json_mode_rejection_falls_back_to_a_plain_call(parser):
    """Not every Groq model supports response_format, so one retry is allowed."""
    instance, fake = parser(VALID, fail_times=1)
    result = instance.parse("2 rotis with dal")

    assert len(result.items) == 2
    assert len(fake.calls) == 2
    assert "response_format" in fake.calls[0]
    assert "response_format" not in fake.calls[1]


def test_a_total_outage_raises_a_named_error(parser):
    instance, fake = parser(VALID, fail_times=2, error=RuntimeError("model decommissioned"))
    with pytest.raises(MealParserError, match="Groq request failed"):
        instance.parse("2 rotis")
    assert len(fake.calls) == 2


def test_the_request_carries_the_meal_text_and_a_low_temperature(parser):
    instance, fake = parser(VALID)
    instance.parse("  rajma chawal for dinner  ")

    messages = fake.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "rajma chawal for dinner" in messages[1]["content"]
    assert fake.calls[0]["temperature"] <= 0.2


# ── singleton ────────────────────────────────────────────────────────────────


def test_get_meal_parser_reuses_one_instance(parser, monkeypatch):
    parser(VALID)
    monkeypatch.setattr(mp, "_parser_instance", None)
    assert mp.get_meal_parser() is mp.get_meal_parser()


# ── stated and assumed amounts ───────────────────────────────────────────────


def test_amounts_are_assumed_given_unless_the_model_says_otherwise(parser):
    body = {"items": [
        {"food_name": "roti", "quantity": 2, "unit": "piece"},
        {"food_name": "rice", "quantity": 1, "unit": "katori", "quantity_given": False},
    ]}
    instance, _ = parser(json.dumps(body))
    items = instance.parse("2 rotis and some rice").items
    assert [i.quantity_given for i in items] == [True, False]


@pytest.mark.parametrize("unit", ["plate", "Bowl", " serving ", "portions"])
def test_container_units_are_never_a_stated_amount(parser, unit):
    """The model does not always follow this rule, so it is enforced in code."""
    body = {"items": [{"food_name": "biryani", "quantity": 1, "unit": unit, "quantity_given": True}]}
    instance, _ = parser(json.dumps(body))
    assert instance.parse("a plate of biryani").items[0].quantity_given is False
