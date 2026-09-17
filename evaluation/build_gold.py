"""Build the labelled meal set from USDA FoodData Central survey foods.

Every reference number here comes from USDA FNDDS (public domain), never from
the app's own food table, so the benchmark cannot simply agree with itself.

* Nutrients per 100 g are the FNDDS values for the listed fdcId.
* Counted items ("2 rotis") use the FNDDS gram weight for one piece.
* Volumes use the FNDDS weight of one cup (240 ml) scaled to the volume, so a
  katori of dal and a katori of upma weigh what those foods actually weigh.
* Unsized servings ("a plate of biryani", "some rice") use FNDDS "Quantity not
  specified", the typical amount eaten in the dietary survey.

Unit volumes: katori 150 ml, cup 240 ml, glass 250 ml, tablespoon 15 ml.

Run once with the FNDDS JSON download in place:

    python evaluation/build_gold.py path/to/surveyDownload.json

It writes evaluation/meals_gold.json, which is committed; the 66 MB USDA file
is not.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

OUT = Path(__file__).with_name("meals_gold.json")

KATORI_ML, CUP_ML, GLASS_ML, TBSP_ML = 150, 240, 250, 15

# Short name -> (fdcId, portion used for one "piece", portion used for one cup).
FOODS = {
    "roti": ("2707713", "1 medium chappatti", None),
    "naan": ("2707613", "1 Naan (10\" dia)", None),
    "paratha": ("2707715", "1 paratha", None),
    "puri": ("2707714", "1 puri", None),
    "idli": ("2708346", "1 item", None),
    "dosa": ("2708347", "1 medium", None),
    "masala dosa": ("2709129", "1 medium", None),
    "vada": ("2709130", "1 item, any size", None),
    "sambar": ("2707430", None, "1 cup"),
    "upma": ("2709128", None, "1 cup, cooked"),
    "dal": ("2707427", None, "1 cup"),
    "lentil curry": ("2707431", None, "1 cup"),
    "chicken biryani": ("2706538", None, "1 cup"),
    "veg biryani": ("2708985", None, "1 cup"),
    "mutton biryani": ("2706490", None, "1 cup"),
    "chicken curry": ("2706437", None, "1 cup"),
    "fish curry": ("2706460", None, "1 cup"),
    "vegetable curry": ("2710067", None, "1 cup"),
    "palak paneer": ("2709631", None, "1 cup"),
    "paneer": ("2705740", None, "1 cup"),
    "samosa": ("2708730", "1 regular/large", None),
    "pakora": ("2710066", "1 pakora", None),
    "papad": ("2707429", "1 papad", None),
    "chutney": ("2709309", None, None),
    "ladoo": ("2710351", "1 piece", None),
    "ghee": ("2710168", None, "1 cup"),
    "rice": ("2708408", None, "1 cup, cooked"),
    "brown rice": ("2708414", None, "1 cup, cooked"),
    "boiled egg": ("2707154", "1 egg", None),
    "omelette egg": ("2707200", "1 egg", None),
    "milk": ("2705385", None, "1 cup"),
    "curd": ("2705418", None, "1 cup"),
    "buttermilk": ("2705393", None, "1 cup"),
    "banana": ("2709224", "1 banana", None),
    "apple": ("2709215", "1 medium", None),
    "orange": ("2709171", "1 fruit", None),
    "mango": ("2709242", "1 mango", None),
    "bread": ("2707598", "1 medium or regular slice", None),
    "peanuts": ("2707515", None, None),
    "almonds": ("2707489", "1 nut", None),
    "cashews": ("2707497", "1 nut", None),
    "chickpeas": ("2707416", None, "1 cup"),
    "kidney beans": ("2707381", None, "1 cup"),
    "moong": ("2707389", None, "1 cup"),
    "cornflakes": ("2708453", None, "1 cup"),
    "chicken breast": ("2705956", "1 medium breast", None),
    "potato": ("2709385", "1 medium", "1 cup"),
    "cucumber": ("2709784", "1 piece/slice", None),
    "oats": ("2708381", None, "1 cup, cooked"),
    "cola": ("2710541", "1 can", None),
    "latte": ("2710386", None, "1 cup (8 fl oz)"),
    "veg sandwich": ("2709132", "1 regular", None),
    "cheese sandwich": ("2705794", "1 sandwich", None),
    "noodles": ("2708352", None, "1 cup, cooked"),
    "sugar": ("2710258", None, None),
    "pizza": ("2708615", "1 piece, NFS", None),
}

# (text, [(food, how, amount)]). how: n = pieces, k = katori, c = cup,
# gl = glass, tb = tablespoon, ts = teaspoon, g = grams, q = typical serving.
MEALS = [
    ("2 rotis with a katori of dal", [("roti", "n", 2), ("dal", "k", 1)]),
    ("had 3 chapatis and 1 katori dal for lunch", [("roti", "n", 3), ("dal", "k", 1)]),
    ("a katori of rice with a katori of dal and a katori of curd",
     [("rice", "k", 1), ("dal", "k", 1), ("curd", "k", 1)]),
    ("dal chawal, one katori each", [("dal", "k", 1), ("rice", "k", 1)]),
    ("4 idlis with a katori of sambar", [("idli", "n", 4), ("sambar", "k", 1)]),
    ("breakfast was 2 idli, 1 vada and sambar (1 katori)",
     [("idli", "n", 2), ("vada", "n", 1), ("sambar", "k", 1)]),
    ("one masala dosa with sambar, 1 katori", [("masala dosa", "n", 1), ("sambar", "k", 1)]),
    ("2 plain dosas with 2 tablespoons of chutney", [("dosa", "n", 2), ("chutney", "tb", 2)]),
    ("a katori of upma for breakfast", [("upma", "k", 1)]),
    ("2 katori upma and a cup of latte", [("upma", "k", 2), ("latte", "c", 1)]),
    ("a cup of chicken biryani", [("chicken biryani", "c", 1)]),
    ("2 cups of veg biryani with a katori of curd", [("veg biryani", "c", 2), ("curd", "k", 1)]),
    ("mutton biryani, 1.5 cups", [("mutton biryani", "c", 1.5)]),
    ("2 rotis and a katori of chicken curry", [("roti", "n", 2), ("chicken curry", "k", 1)]),
    ("fish curry 1 katori with 1 cup rice", [("fish curry", "k", 1), ("rice", "c", 1)]),
    ("3 rotis with mixed vegetable curry, 1 katori", [("roti", "n", 3), ("vegetable curry", "k", 1)]),
    ("palak paneer 1 katori and 2 rotis", [("palak paneer", "k", 1), ("roti", "n", 2)]),
    ("1 naan with a katori of palak paneer", [("naan", "n", 1), ("palak paneer", "k", 1)]),
    ("100g paneer", [("paneer", "g", 100)]),
    ("150 grams of paneer and 2 rotis", [("paneer", "g", 150), ("roti", "n", 2)]),
    ("2 samosas", [("samosa", "n", 2)]),
    ("a samosa and a cup of latte", [("samosa", "n", 1), ("latte", "c", 1)]),
    ("6 pakoras with 1 tbsp chutney", [("pakora", "n", 6), ("chutney", "tb", 1)]),
    ("2 papads with dal and rice, 1 katori each", [("papad", "n", 2), ("dal", "k", 1), ("rice", "k", 1)]),
    ("one ladoo", [("ladoo", "n", 1)]),
    ("2 besan ladoos after dinner", [("ladoo", "n", 2)]),
    ("2 plain parathas with a katori of curd", [("paratha", "n", 2), ("curd", "k", 1)]),
    ("1 paratha with 1 tsp ghee", [("paratha", "n", 1), ("ghee", "ts", 1)]),
    ("3 puris with a katori of vegetable curry", [("puri", "n", 3), ("vegetable curry", "k", 1)]),
    ("5 puris", [("puri", "n", 5)]),
    ("2 boiled eggs and 2 slices of bread", [("boiled egg", "n", 2), ("bread", "n", 2)]),
    ("3 egg omelette with 2 bread slices", [("omelette egg", "n", 3), ("bread", "n", 2)]),
    ("4 boiled eggs", [("boiled egg", "n", 4)]),
    ("a glass of milk", [("milk", "gl", 1)]),
    ("2 glasses of milk and a banana", [("milk", "gl", 2), ("banana", "n", 1)]),
    ("a glass of buttermilk", [("buttermilk", "gl", 1)]),
    ("1 katori curd with 1 tsp sugar", [("curd", "k", 1), ("sugar", "ts", 1)]),
    ("a banana and an apple", [("banana", "n", 1), ("apple", "n", 1)]),
    ("2 bananas", [("banana", "n", 2)]),
    ("one orange", [("orange", "n", 1)]),
    ("a mango", [("mango", "n", 1)]),
    ("30g roasted peanuts", [("peanuts", "g", 30)]),
    ("50 grams of peanuts", [("peanuts", "g", 50)]),
    ("10 almonds", [("almonds", "n", 10)]),
    ("15 cashews and 10 almonds", [("cashews", "n", 15), ("almonds", "n", 10)]),
    ("a katori of boiled chickpeas", [("chickpeas", "k", 1)]),
    ("1 cup boiled kidney beans with 1 cup rice", [("kidney beans", "c", 1), ("rice", "c", 1)]),
    ("a katori of boiled moong", [("moong", "k", 1)]),
    ("a cup of cornflakes with a glass of milk", [("cornflakes", "c", 1), ("milk", "gl", 1)]),
    ("2 cups of cornflakes with 1 cup milk", [("cornflakes", "c", 2), ("milk", "c", 1)]),
    ("a grilled chicken breast", [("chicken breast", "n", 1)]),
    ("200g grilled chicken breast with 1 cup rice", [("chicken breast", "g", 200), ("rice", "c", 1)]),
    ("2 boiled potatoes", [("potato", "n", 2)]),
    ("a cup of oats made with water", [("oats", "c", 1)]),
    ("a can of coke", [("cola", "n", 1)]),
    ("a veg sandwich", [("veg sandwich", "n", 1)]),
    ("cheese sandwich and a cup of latte", [("cheese sandwich", "n", 1), ("latte", "c", 1)]),
    ("2 slices of cheese pizza", [("pizza", "n", 2)]),
    ("a cup of boiled noodles", [("noodles", "c", 1)]),
    ("1 cup brown rice with 1 katori dal", [("brown rice", "c", 1), ("dal", "k", 1)]),
    ("lunch: 2 rotis, 1 katori dal, 1 katori curd, dinner: 1 cup rice and 1 katori chicken curry",
     [("roti", "n", 2), ("dal", "k", 1), ("curd", "k", 1), ("rice", "c", 1), ("chicken curry", "k", 1)]),
    ("breakfast 3 idli and sambar 1 katori, lunch 2 roti with vegetable curry 1 katori",
     [("idli", "n", 3), ("sambar", "k", 1), ("roti", "n", 2), ("vegetable curry", "k", 1)]),
    ("had 2 parathas with curd (1 katori) in the morning and a glass of milk at night",
     [("paratha", "n", 2), ("curd", "k", 1), ("milk", "gl", 1)]),
    ("snack: 2 samosas and 1 tbsp chutney", [("samosa", "n", 2), ("chutney", "tb", 1)]),
    ("an apple and 30g peanuts", [("apple", "n", 1), ("peanuts", "g", 30)]),
    ("2 egg omelette, 2 slices bread and a glass of milk",
     [("omelette egg", "n", 2), ("bread", "n", 2), ("milk", "gl", 1)]),
    ("half katori dal and 2 rotis", [("dal", "k", 0.5), ("roti", "n", 2)]),
    ("1.5 katori rice with 1 katori sambar", [("rice", "k", 1.5), ("sambar", "k", 1)]),
    ("2 katori chicken curry", [("chicken curry", "k", 2)]),
    ("a katori of fish curry and 3 rotis", [("fish curry", "k", 1), ("roti", "n", 3)]),
    # Unsized servings: the reference is the USDA typical serving, so these
    # measure how sensible the app's default portion is, not parsing.
    ("a plate of chicken biryani", [("chicken biryani", "q", 1)]),
    ("some rice and dal", [("rice", "q", 1), ("dal", "q", 1)]),
    ("a bowl of dal", [("dal", "q", 1)]),
    ("a serving of palak paneer", [("palak paneer", "q", 1)]),
    ("a bowl of upma", [("upma", "q", 1)]),
    ("a plate of veg biryani", [("veg biryani", "q", 1)]),
    ("a serving of curd", [("curd", "q", 1)]),
    ("some peanuts", [("peanuts", "q", 1)]),
    ("an omelette", [("omelette egg", "q", 1)]),
]


def portion(food: dict, label: str) -> float:
    for desc, grams in food["portions"]:
        if desc.lower().startswith(label.lower()):
            return float(grams)
    raise KeyError(f"{food['desc']}: no portion starting {label!r}")


def grams_for(name: str, how: str, amount: float, db: dict) -> float:
    fdc, piece, cup = FOODS[name]
    food = db[fdc]
    if how == "g":
        return amount
    if how == "n":
        return amount * portion(food, piece)
    if how == "q":
        return amount * portion(food, "Quantity not specified")
    if how == "tb" and name == "chutney":
        return amount * portion(food, "1 tablespoon")
    if how == "ts" and name == "sugar":
        return amount * portion(food, "1 teaspoon")
    ml = {"k": KATORI_ML, "c": CUP_ML, "gl": GLASS_ML, "tb": TBSP_ML, "ts": TBSP_ML / 3}[how]
    return amount * portion(food, cup) * ml / CUP_ML


def main(source: str) -> None:
    raw = json.load(open(source, encoding="utf-8"))
    db = {}
    for f in raw[next(iter(raw))]:
        nutrients = {}
        for n in f["foodNutrients"]:
            name, unit = n["nutrient"]["name"], n["nutrient"]["unitName"]
            if name == "Energy" and unit.lower() != "kcal":
                continue
            nutrients[name] = n.get("amount") or 0.0
        db[str(f["fdcId"])] = {
            "desc": f["description"],
            "portions": [(p.get("portionDescription") or "", p.get("gramWeight") or 0)
                         for p in f["foodPortions"]],
            "per100": {
                "calories": nutrients.get("Energy", 0.0),
                "protein": nutrients.get("Protein", 0.0),
                "carbs": nutrients.get("Carbohydrate, by difference", 0.0),
                "fat": nutrients.get("Total lipid (fat)", 0.0),
            },
        }

    rng = random.Random(7)
    order = list(range(len(MEALS)))
    rng.shuffle(order)
    test_ids = set(order[: len(MEALS) // 2])

    meals = []
    for i, (text, items) in enumerate(MEALS):
        gold_items, totals = [], {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
        for name, how, amount in items:
            fdc = FOODS[name][0]
            grams = grams_for(name, how, amount, db)
            macros = {k: round(v * grams / 100, 1) for k, v in db[fdc]["per100"].items()}
            for k in totals:
                totals[k] += macros[k]
            gold_items.append({"food": name, "fdc_id": fdc, "usda_description": db[fdc]["desc"],
                               "grams": round(grams, 1), **macros})
        meals.append({
            "id": f"meal_{i + 1:03d}",
            "text": text,
            "split": "test" if i in test_ids else "dev",
            "unsized": any(how == "q" for _, how, _ in items),
            "items": gold_items,
            "totals": {k: round(v, 1) for k, v in totals.items()},
        })

    OUT.write_text(json.dumps({
        "source": "USDA FoodData Central, FNDDS survey foods (2024-10-31), public domain",
        "units_ml": {"katori": KATORI_ML, "cup": CUP_ML, "glass": GLASS_ML, "tablespoon": TBSP_ML},
        "meals": meals,
    }, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({len(meals)} meals, {len(test_ids)} test)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "surveyDownload.json")
