"""
Meal Parser Service
-------------------
Takes a natural language meal description and returns structured meal data.
Uses Groq (Llama 3 70B) for fast, free inference.

Handles:
- Mixed Indian + western meals
- Desi quantity units (katori, chapati, etc.)
- Multiple meals in one message ("had poha for breakfast and dal chawal for dinner")
- Vague descriptions ("normal lunch at home")
"""

import json
import re
from groq import Groq
from app.core.config import get_settings
from pydantic import BaseModel
from typing import List

settings = get_settings()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ParsedMealItem(BaseModel):
    food_name: str
    quantity: float
    unit: str                  # "katori", "piece", "g", "cup", "tbsp", etc.
    meal_time: str = "unknown" # "breakfast", "lunch", "dinner", "snack", "unknown"
    notes: str = ""            # e.g. "with less oil", "homemade"

class ParsedMealResponse(BaseModel):
    items: List[ParsedMealItem]
    raw_input: str
    parse_confidence: str      # "high", "medium", "low"
    clarification_needed: str = ""  # if the LLM isn't sure about something


# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a nutrition assistant specialized in Indian and South Asian diets.
Your job is to parse a user's meal description into structured data.

RULES:
1. Return ONLY valid JSON, no markdown, no explanation, no preamble.
2. Split combined dishes into components where nutritionally meaningful:
   - "dal chawal" → dal (1 katori) + rice (1 katori)
   - "rajma chawal" → rajma (1 katori) + rice (1 katori)
   - "bread omelette" → bread (2 slice) + omelette (1 piece)
3. Handle Indian quantity units naturally:
   - "katori" = small bowl (~150ml)
   - "chapati/roti" = piece
   - "glass" = ~250ml
   - "handful" = ~30g for dry foods
   - If quantity is vague ("some", "a bit"), estimate conservatively
4. Identify meal time from context if mentioned (breakfast, lunch, dinner, snack)
5. If something is truly ambiguous, note it in clarification_needed
6. parse_confidence: "high" if quantities are clear, "medium" if estimated, "low" if very vague

OUTPUT FORMAT (strict JSON):
{
  "items": [
    {
      "food_name": "dal tadka",
      "quantity": 1.5,
      "unit": "katori",
      "meal_time": "lunch",
      "notes": ""
    }
  ],
  "parse_confidence": "high",
  "clarification_needed": ""
}

EXAMPLES of food_name values (use simple, searchable names):
- "dal tadka", "dal makhani", "chana dal"
- "basmati rice", "jeera rice"
- "paneer butter masala", "paneer bhurji"
- "aloo paratha", "plain paratha", "missi roti"
- "curd", "dahi", "lassi"
- "poha", "upma", "idli", "dosa"
- "rajma", "chole", "kadhi"
- "boiled egg", "scrambled eggs", "omelette"
- "banana", "apple", "orange"
"""

USER_PROMPT_TEMPLATE = """Parse this meal description: "{user_input}"

Remember: Return ONLY the JSON object. No other text."""


# ── Parser ────────────────────────────────────────────────────────────────────

class MealParserError(RuntimeError):
    """Raised when the LLM call itself fails (bad key, retired model, outage)."""


class MealParser:
    def __init__(self):
        if not settings.groq_api_key:
            raise MealParserError(
                "GROQ_API_KEY is not set. Add it to your .env file - get a free key at "
                "https://console.groq.com/keys"
            )
        self.client = Groq(api_key=settings.groq_api_key)
        self.model = settings.groq_model

    def parse(self, user_input: str) -> ParsedMealResponse:
        """
        Parse a natural language meal description into structured data.
        
        Args:
            user_input: e.g. "had 2 rotis with dal and a small bowl of dahi for lunch"
        
        Returns:
            ParsedMealResponse with list of meal items
        """
        prompt = USER_PROMPT_TEMPLATE.format(user_input=user_input.strip())

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,   # low temp = more consistent structured output
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            # Not every model supports JSON mode - retry plain before giving up.
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=2048,
                )
            except Exception:
                raise MealParserError(f"Groq request failed ({self.model}): {e}") from e

        raw_text = (response.choices[0].message.content or "").strip()
        parsed_data = self._safe_parse_json(raw_text)

        return ParsedMealResponse(
            items=[ParsedMealItem(**item) for item in parsed_data.get("items", [])],
            raw_input=user_input,
            parse_confidence=parsed_data.get("parse_confidence", "medium"),
            clarification_needed=parsed_data.get("clarification_needed", ""),
        )

    def _safe_parse_json(self, text: str) -> dict:
        """Strip markdown fences if present and parse JSON safely."""
        # Remove ```json ... ``` if model wraps it anyway
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # Return a minimal valid response so the app doesn't crash
            return {
                "items": [],
                "parse_confidence": "low",
                "clarification_needed": f"Could not parse response: {str(e)}. Raw: {text[:200]}",
            }


# ── Singleton ─────────────────────────────────────────────────────────────────

_parser_instance = None

def get_meal_parser() -> MealParser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = MealParser()
    return _parser_instance
