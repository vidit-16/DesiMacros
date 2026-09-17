"""Regenerate evaluation/reference_values.csv: the food table against two references.

A value in ``IFCT_SEED_DATA`` is only changed when both references disagree with
it in the same direction, so every comparison is recorded rather than applied
silently. See docs/ACCURACY.md.

Inputs, neither of which is committed (one is 1 MB of Excel, the other 66 MB of
JSON, and both belong to their publishers):

* ``INDB.xlsx`` - Indian Nutrient Databank, from
  github.com/lindsayjaacks/Indian-Nutrient-Databank-INDB- (Nanavati et al.,
  *Current Developments in Nutrition*, 2024, CC BY). Recipes derived from
  ICMR-NIN IFCT 2017.
* ``surveyDownload.json`` - USDA FoodData Central survey foods (FNDDS), from
  fdc.nal.usda.gov/download-datasets.html (public domain).

    python evaluation/compare_references.py INDB.xlsx surveyDownload.json
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.nutrition import IFCT_SEED_DATA  # noqa: E402

OUT = Path(__file__).with_name("reference_values.csv")

# app food -> (INDB recipe code, USDA fdcId, what was decided and why)
COMPARISONS = [
    ("idli", "ASC144", "2708346", "corrected: was one idli's energy as a per-100 g value"),
    ("sambar", "ASC167", "2707430", "corrected: both references about twice the old value"),
    ("dosa", "BFP148", "2708347", "corrected towards USDA; INDB's batter-weight row is an outlier"),
    ("palak paneer", "ASC215", "2709631", "corrected down: both references far lower"),
    ("aloo gobi", "ASC171", "2710067", "corrected up: too low for a dish cooked in oil"),
    ("aloo matar", "ASC190", "2710067", "corrected to INDB"),
    ("rajma", "ASC165", "2707381", "corrected: old value was dry-boiled beans, not the curry"),
    ("biryani", "ASC122", "2706538", "corrected to the Indian recipes, not USDA's lighter survey dish"),
    ("coconut chutney", "ASC386", "2709309", "corrected up: below both references"),
    ("naan", "ASC142", "2707613", "added: had a piece weight but no nutrition row"),
    ("upma", "BFP039", "2709128", "kept: INDB agrees, USDA is the outlier"),
    ("chole", "ASC162", "2707416", "kept: INDB agrees"),
    ("poha", "BFP045", "", "kept: INDB agrees"),
    ("plain paratha", "ASC097", "2707715", "kept: both references agree"),
    ("aloo paratha", "ASC098", "", "kept: one reference only, and it is lower"),
    ("masala dosa", "ASC146", "2709129", "kept: INDB agrees"),
    ("basmati rice", "ASC113", "2708408", "kept: all three within 10%"),
    ("roti", "ASC096", "2707713", "kept: sits between the two references"),
    ("veg sandwich", "BFP458", "2709132", "kept: INDB agrees, USDA is the outlier"),
    ("paneer", "", "2705740", "kept: USDA agrees"),
    ("papad", "", "2707429", "kept: USDA agrees"),
    ("curd", "", "2705418", "kept: one reference only (USDA whole-milk yoghurt, 78)"),
    ("maggi", "", "", "kept: dry-weight value paired with a dry piece weight"),
    ("boiled egg", "ASC056", "2707154", "INDB unusable: its boiled dishes include cooking water"),
    ("bhatura", "ASC143", "", "INDB unusable: all frying oil counted as absorbed"),
    ("lassi", "ASC021", "", "INDB unusable: recipe mass diluted by water"),
]


def load_indb(path: str) -> dict:
    import pandas as pd

    frame = pd.read_excel(path)
    return {
        row["food_code"]: (row["food_name"], round(row["energy_kcal"], 1))
        for _, row in frame.iterrows()
    }


def load_usda(path: str) -> dict:
    raw = json.load(open(path, encoding="utf-8"))
    foods = raw[next(iter(raw))]
    out = {}
    for food in foods:
        energy = next(
            (n.get("amount") for n in food["foodNutrients"]
             if n["nutrient"]["name"] == "Energy" and n["nutrient"]["unitName"].lower() == "kcal"),
            None,
        )
        out[str(food["fdcId"])] = (food["description"], energy)
    return out


def main(indb_path: str, usda_path: str) -> int:
    indb, usda = load_indb(indb_path), load_usda(usda_path)
    app = {row[0]: row[2] for row in IFCT_SEED_DATA}

    rows = []
    for food, code, fdc, decision in COMPARISONS:
        indb_name, indb_kcal = indb.get(code, ("", ""))
        usda_name, usda_kcal = usda.get(fdc, ("", ""))
        rows.append({
            "food": food,
            "app_kcal_per_100g": app.get(food, ""),
            "indb_code": code,
            "indb_name": indb_name,
            "indb_kcal": indb_kcal,
            "usda_fdc_id": fdc,
            "usda_name": usda_name,
            "usda_kcal": usda_kcal,
            "decision": decision,
        })

    with open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)")
    for row in rows:
        print(f"  {row['food']:16} app {str(row['app_kcal_per_100g']):>5}  "
              f"indb {str(row['indb_kcal']):>6}  usda {str(row['usda_kcal']):>5}  {row['decision']}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
