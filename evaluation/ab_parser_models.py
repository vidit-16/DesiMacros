"""A/B comparison of Groq models for the meal parser.

Each model parses the same hand-labelled meal descriptions with the production
prompt. The parsed items go through the real nutrition lookup (IFCT only, USDA
disabled so results are reproducible), and are scored against a gold parse
that went through the same lookup.

Metrics per model:
- valid:      share of runs that returned at least one item
- items:      share of runs with the gold item count
- cal_err:    median absolute calorie error vs the gold parse, in percent
- within_15:  share of meals whose total is within 15% of gold
- latency:    median seconds per call

Usage (needs GROQ_API_KEY; ~40 small calls):
    python evaluation/ab_parser_models.py > evaluation/results.md
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("IFCT_DB_PATH", str(Path(tempfile.mkdtemp()) / "ifct.db"))

from app.services import nutrition  # noqa: E402
from app.services.meal_parser import MealParser  # noqa: E402

MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# (description, gold items as (food_name, quantity, unit))
GOLD = [
    ("2 rotis with dal tadka and a katori of dahi",
     [("roti", 2, "piece"), ("dal tadka", 1, "katori"), ("curd", 1, "katori")]),
    ("rajma chawal for lunch", [("rajma", 1, "katori"), ("basmati rice", 1, "katori")]),
    ("3 idli with sambar", [("idli", 3, "piece"), ("sambar", 1, "katori")]),
    ("one plate poha and a glass of lassi", [("poha", 1, "plate"), ("lassi", 1, "glass")]),
    ("2 boiled eggs and a banana", [("boiled egg", 2, "piece"), ("banana", 1, "piece")]),
    ("aloo paratha with a bowl of curd", [("aloo paratha", 1, "piece"), ("curd", 1, "katori")]),
    ("1 plain dosa", [("dosa", 1, "piece")]),
    ("paneer butter masala with 3 rotis",
     [("paneer butter masala", 1, "katori"), ("roti", 3, "piece")]),
    ("a katori of khichdi", [("khichdi", 1, "katori")]),
    ("chole with jeera rice", [("chole", 1, "katori"), ("jeera rice", 1, "katori")]),
]


def total_calories(items) -> float:
    return sum(nutrition.lookup_nutrition(n, q, u)["calories"] for n, q, u in items)


def main() -> int:
    if not os.getenv("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set; nothing to compare.", file=sys.stderr)
        return 1
    nutrition.IFCT_DB_PATH = os.environ["IFCT_DB_PATH"]
    nutrition.init_ifct_db()
    nutrition.search_usda = lambda name: None  # reproducible: IFCT only

    parser = MealParser()
    rows = []
    for model in MODELS:
        parser.model = model
        valid = count_ok = within = 0
        errors, latencies = [], []
        for text, gold in GOLD:
            gold_cal = total_calories(gold)
            start = time.perf_counter()
            try:
                parsed = parser.parse(text)
                items = [(i.food_name, i.quantity, i.unit) for i in parsed.items]
            except Exception as exc:  # retired model, rate limit, bad output
                print(f"  {model}: {text!r} failed: {exc}", file=sys.stderr)
                items = []
            latencies.append(time.perf_counter() - start)
            if items:
                valid += 1
            if len(items) == len(gold):
                count_ok += 1
            err = abs(total_calories(items) - gold_cal) / gold_cal * 100
            errors.append(err)
            within += err <= 15
        n = len(GOLD)
        rows.append((model, valid / n, count_ok / n, statistics.median(errors),
                     within / n, statistics.median(latencies)))

    print("| Model | Valid parse | Item count match | Median calorie error | Within 15% | Median latency |")
    print("| --- | --- | --- | --- | --- | --- |")
    for model, v, c, e, w, lat in rows:
        print(f"| `{model}` | {v:.0%} | {c:.0%} | {e:.1f}% | {w:.0%} | {lat:.2f}s |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
