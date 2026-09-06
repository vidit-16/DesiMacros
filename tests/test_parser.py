"""
Quick test for the meal parser and nutrition lookup.
Run from the project root: python -m tests.test_parser
Make sure GROQ_API_KEY is set in your .env first (only needed for test_parser).
"""

import sys

from dotenv import load_dotenv
load_dotenv()

# Windows consoles default to cp1252 and choke on the emoji below.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.meal_parser import MealParser
from app.services.nutrition import init_ifct_db, lookup_nutrition

def test_parser():
    parser = MealParser()
    
    test_inputs = [
        "Had poha for breakfast and chai",
        "2 rotis with dal tadka and a katori of dahi for lunch",
        "Rajma chawal for dinner, also had some salad on the side",
        "Mess food — sabzi, dal, 3 chapatis and a glass of lassi",
        "Omelette with 2 eggs and 2 bread slices in the morning",
    ]
    
    print("=" * 60)
    print("MEAL PARSER TEST")
    print("=" * 60)
    
    for inp in test_inputs:
        print(f"\n📝 Input: {inp}")
        result = parser.parse(inp)
        print(f"   Confidence: {result.parse_confidence}")
        for item in result.items:
            print(f"   → {item.food_name}: {item.quantity} {item.unit} ({item.meal_time})")
        if result.clarification_needed:
            print(f"   ⚠️  {result.clarification_needed}")

def test_nutrition():
    init_ifct_db()
    
    print("\n" + "=" * 60)
    print("NUTRITION LOOKUP TEST")
    print("=" * 60)
    
    test_items = [
        ("dal tadka", 1.5, "katori"),
        ("basmati rice", 1, "katori"),
        ("roti", 2, "piece"),
        ("dahi", 1, "small bowl"),
        ("omelette", 1, "piece"),
        ("unknown food xyz", 1, "piece"),
    ]
    
    for food, qty, unit in test_items:
        result = lookup_nutrition(food, qty, unit)
        print(f"\n🍽️  {food} ({qty} {unit}) → {result['grams']}g")
        print(f"   Calories: {result['calories']} kcal | P: {result['protein']}g | C: {result['carbs']}g | F: {result['fat']}g")
        print(f"   Source: {result['source']}")

if __name__ == "__main__":
    test_nutrition()   # test this first (no API key needed)
    # test_parser()    # uncomment once GROQ_API_KEY is set
