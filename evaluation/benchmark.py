"""Calorie and macro accuracy against the USDA-labelled meal set.

Two modes, so a miss can be blamed on the right step:

* ``--mode parsed`` (default) runs the real meal parser on each description and
  sends its items through the real nutrition lookup. This is what a user gets.
* ``--mode oracle`` skips the parser and feeds the lookup the correct items
  (food, quantity, unit). Error here is the food table and portion weights
  alone.

Parser output is cached in ``evaluation/.cache`` keyed by model, prompt and text, so a
change to the lookup can be re-scored without calling the LLM again. USDA
fallback is disabled so runs are reproducible.

    python evaluation/benchmark.py --mode oracle
    python evaluation/benchmark.py --split test --show-worst 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE_DIR = Path(__file__).with_name(".cache")
CACHE_DIR.mkdir(exist_ok=True)
# Kept between runs so model estimates for unknown foods are cached, not re-asked.
os.environ.setdefault("IFCT_DB_PATH", str(CACHE_DIR / "ifct.db"))

from app.services import nutrition  # noqa: E402

GOLD = Path(__file__).with_name("meals_gold.json")
CACHE = CACHE_DIR / "parses.json"

# How the gold set's quantity kinds read to the lookup in oracle mode.
ORACLE_UNIT = {"n": "piece", "k": "katori", "c": "cup", "gl": "glass",
               "tb": "tbsp", "ts": "tsp", "g": "g", "q": "serving"}
# Gold short names that a user (and the parser) would say differently.
ORACLE_NAME = {"omelette egg": "omelette", "moong": "moong dal", "cola": "coke", "oats": "cooked oats"}


def _oracle_items(meal: dict) -> list[tuple[str, float, str]]:
    from evaluation.build_gold import MEALS
    index = int(meal["id"].split("_")[1]) - 1
    return [(ORACLE_NAME.get(food, food), amount, ORACLE_UNIT[how])
            for food, how, amount in MEALS[index][1]]


def _parsed_items(meal: dict, parser, cache: dict) -> list[tuple[str, float, str]]:
    from app.services.meal_parser import SYSTEM_PROMPT
    # The prompt is part of the key: a prompt change must re-parse, not reuse.
    key = hashlib.sha1(f"{parser.model}|{SYSTEM_PROMPT}|{meal['text']}".encode()).hexdigest()
    if key not in cache:
        result = parser.parse(meal["text"])
        cache[key] = [(i.food_name, i.quantity, i.unit) for i in result.items]
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return [tuple(x) for x in cache[key]]


def score(mode: str, split: str, show_worst: int, estimates: bool = True, density: bool = True) -> dict:
    nutrition.IFCT_DB_PATH = os.environ["IFCT_DB_PATH"]
    nutrition.init_ifct_db()
    nutrition.search_usda = lambda name: None
    nutrition.ESTIMATES_ENABLED = estimates
    if not density:
        nutrition.CUP_GRAMS = {}

    meals = json.loads(GOLD.read_text(encoding="utf-8"))["meals"]
    if split != "all":
        meals = [m for m in meals if m["split"] == split]

    parser, cache = None, {}
    if mode == "parsed":
        from app.services.meal_parser import MealParser
        parser = MealParser()
        if CACHE.exists():
            cache = json.loads(CACHE.read_text(encoding="utf-8"))

    rows = []
    for meal in meals:
        items = _oracle_items(meal) if mode == "oracle" else _parsed_items(meal, parser, cache)
        looked = [nutrition.lookup_nutrition(n, q, u) for n, q, u in items]
        got = {k: sum(x[k] for x in looked) for k in ("calories", "protein", "carbs", "fat")}
        gold = meal["totals"]
        err = {k: abs(got[k] - gold[k]) / max(gold[k], 1.0) * 100 for k in got}
        rows.append({
            "id": meal["id"], "text": meal["text"], "unsized": meal["unsized"],
            "gold": gold["calories"], "got": round(got["calories"], 1), "err": err,
            "signed": (got["calories"] - gold["calories"]) / gold["calories"] * 100,
            "not_found": sum(x["source"] == "not_found" for x in looked),
            "items": [(x["food_name"], round(x["grams"]), x["calories"], x["source"]) for x in looked],
        })

    def summary(subset: list[dict]) -> dict:
        cal = [r["err"]["calories"] for r in subset]
        return {
            "meals": len(subset),
            "median_cal_err": statistics.median(cal),
            "within_10": sum(e <= 10 for e in cal) / len(cal),
            "within_20": sum(e <= 20 for e in cal) / len(cal),
            "median_protein_err": statistics.median(r["err"]["protein"] for r in subset),
            "median_signed": statistics.median(r["signed"] for r in subset),
            "meals_with_not_found": sum(r["not_found"] > 0 for r in subset),
        }

    out = {"all": summary(rows)}
    sized = [r for r in rows if not r["unsized"]]
    if sized and len(sized) < len(rows):
        out["sized"] = summary(sized)
        out["unsized"] = summary([r for r in rows if r["unsized"]])

    print(f"mode={mode} split={split}")
    for name, s in out.items():
        print(f"  {name:8} n={s['meals']:3}  median cal err {s['median_cal_err']:5.1f}%  "
              f"within 10% {s['within_10']:4.0%}  within 20% {s['within_20']:4.0%}  "
              f"protein err {s['median_protein_err']:5.1f}%  bias {s['median_signed']:+5.1f}%  "
              f"meals with unknown food {s['meals_with_not_found']}")
    for r in sorted(rows, key=lambda r: -r["err"]["calories"])[:show_worst]:
        print(f"  {r['err']['calories']:6.0f}%  gold {r['gold']:6.0f} got {r['got']:6.0f}  {r['text']}")
        for item in r["items"]:
            print(f"            {item}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["parsed", "oracle"], default="parsed")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--show-worst", type=int, default=0)
    ap.add_argument("--no-estimates", action="store_true", help="log unknown foods as 0 kcal (old behaviour)")
    ap.add_argument("--no-density", action="store_true", help="weigh every volume as water (old behaviour)")
    args = ap.parse_args()
    if (args.mode == "parsed" or not args.no_estimates) and not os.getenv("GROQ_API_KEY"):
        from app.core.config import get_settings
        if not get_settings().groq_api_key:
            print("GROQ_API_KEY is not set; use --mode oracle --no-estimates or add the key.",
                  file=sys.stderr)
            return 1
    score(args.mode, args.split, args.show_worst,
          estimates=not args.no_estimates, density=not args.no_density)
    return 0


if __name__ == "__main__":
    sys.exit(main())
