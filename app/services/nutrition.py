"""
Nutrition Lookup Service
------------------------
Two-layer lookup:
1. IFCT (Indian Food Composition Tables) — local SQLite, Indian foods first
2. USDA FoodData Central API — fallback for everything else

The IFCT data here is a curated subset of common Indian foods with
approximate nutrition per 100g (based on NIN IFCT 2017 publication).
For production, you'd load the full IFCT dataset.
"""

import re
import sqlite3
import httpx
import os
from dataclasses import dataclass
from typing import Optional
from app.core.config import get_settings

settings = get_settings()

IFCT_DB_PATH = "data/ifct.db"


@dataclass
class NutritionPer100g:
    food_name: str
    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float
    source: str  # "ifct" or "usda"


# ── IFCT Database Setup ───────────────────────────────────────────────────────

# Curated Indian food nutrition data (per 100g, cooked unless noted)
# Source: NIN IFCT 2017, approximate values
IFCT_SEED_DATA = [
    # (name, aliases, calories, protein, carbs, fat, fiber)
    ("dal tadka",       "dal,toor dal,arhar dal,yellow dal",        116, 7.0, 17.0, 2.8, 3.0),
    ("dal makhani",     "mah di dal,black dal",                     130, 6.5, 15.0, 5.0, 3.5),
    ("chana dal",       "bengal gram dal,split chickpea",           109, 7.3, 16.8, 2.1, 3.9),
    ("moong dal",       "green gram dal,mung dal",                  104, 7.6, 16.3, 1.1, 4.1),
    ("rajma",           "kidney beans,red beans",                   127, 8.7, 22.8, 0.5, 6.4),
    ("chole",           "chana masala,chickpeas,kabuli chana",      164, 8.9, 27.4, 2.6, 7.6),
    ("kadhi",           "kadhi pakora,besan kadhi",                  82, 3.1, 10.2, 3.4, 0.8),
    ("palak paneer",    "spinach paneer",                           168, 8.2,  6.4,12.8, 2.1),
    ("paneer bhurji",   "scrambled paneer",                        210,13.5,  4.2,15.8, 0.5),
    ("paneer butter masala","paneer makhani,butter paneer",        225,10.1,  9.3,17.2, 1.2),
    ("aloo gobi",       "potato cauliflower",                        72, 2.1, 10.8, 2.4, 2.8),
    ("aloo matar",      "potato peas",                               89, 2.8, 14.2, 2.9, 3.1),
    ("basmati rice",    "rice,steamed rice,plain rice,chawal",      130, 2.7, 28.2, 0.2, 0.4),
    ("jeera rice",      "cumin rice",                               148, 2.9, 29.1, 2.4, 0.5),
    ("roti",            "chapati,phulka,wheat roti,tawa roti",      264, 8.1, 51.7, 3.7, 3.4),
    ("plain paratha",   "paratha,tawa paratha,ghee paratha",        297, 7.9, 49.8, 7.9, 2.8),
    ("aloo paratha",    "potato paratha,stuffed paratha",           259, 6.2, 37.4, 9.8, 2.5),
    ("poha",            "flattened rice,beaten rice,chivda",        180, 3.5, 34.2, 4.1, 1.2),
    ("upma",            "rava upma,semolina upma",                  153, 4.2, 22.1, 5.4, 1.8),
    ("idli",            "idly",                                      58, 2.2, 11.3, 0.4, 0.6),
    ("dosa",            "plain dosa,sada dosa",                     133, 4.4, 24.1, 2.7, 1.1),
    ("sambar",          "sambhar",                                   47, 2.9,  7.3, 0.9, 2.1),
    ("curd",            "dahi,yogurt,plain yogurt",                  62, 3.1,  4.7, 3.4, 0.0),
    ("lassi",           "sweet lassi,salted lassi",                  78, 2.9,  9.8, 3.1, 0.0),
    ("khichdi",         "dal khichdi,moong khichdi",               118, 4.8, 21.3, 1.9, 1.8),
    ("biryani",         "chicken biryani,veg biryani,mutton biryani",210,8.1,28.4, 7.4, 1.2),
    ("omelette",        "egg omelette,plain omelette",             154,10.6,  0.6,12.1, 0.0),
    ("boiled egg",      "hard boiled egg,egg",                     155,13.0,  1.1,11.0, 0.0),
    ("scrambled eggs",  "anda bhurji,egg bhurji",                  148,10.1,  1.5,11.3, 0.0),
    ("banana",          "kela",                                      89, 1.1, 22.8, 0.3, 2.6),
    ("apple",           "seb",                                       52, 0.3, 13.8, 0.2, 2.4),
    ("whole milk",      "full fat milk,doodh",                      61, 3.2,  4.8, 3.3, 0.0),
    ("tea with milk",   "chai,masala chai,milk tea",                 40, 1.2,  5.8, 1.4, 0.0),
    ("peanuts",         "groundnuts,moongfali",                    567,25.8, 16.1,49.2, 8.5),
    ("white bread",     "bread slice,pav,loaf bread",              265, 9.0, 49.0, 3.2, 2.7),
    ("cornflakes",      "breakfast cereal,corn flakes",            357, 7.0, 84.0, 0.9, 1.2),
    ("oats",            "oatmeal,rolled oats,quaker oats",         389,16.9, 66.3, 6.9,10.6),
    # Added after evaluation: each of these was previously missing (logged as
    # 0 kcal) or matched to something unrelated in USDA.
    ("bhatura",         "bhature,batura",                          325, 6.0, 42.0,14.5, 1.5),
    ("papad",           "papadum,appalam,pappad",                  371,20.0, 52.0, 8.0, 8.0),
    ("masala dosa",     "masaladosa,potato dosa",                  175, 3.8, 27.5, 5.8, 2.0),
    ("maggi",           "instant noodles,2 minute noodles",        450, 9.5, 60.0,18.5, 2.5),
    ("coconut chutney", "nariyal chutney,white chutney",           194, 3.5,  8.0,17.0, 4.0),
    ("filter coffee",   "south indian coffee,kaapi,milk coffee",    60, 1.8,  8.5, 2.0, 0.0),
    ("paneer",          "cottage cheese,fresh paneer,malai paneer", 296,18.3,  1.2,22.8, 0.0),
    ("paneer sandwich", "grilled paneer sandwich,paneer toast",     260,10.5, 26.0,11.5, 2.0),
    ("veg sandwich",    "sandwich,vegetable sandwich,grilled sandwich,club sandwich", 220, 6.0, 30.0, 7.5, 2.5),
]

def init_ifct_db():
    """Create and seed the IFCT SQLite database."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(IFCT_DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS ifct_foods (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            aliases TEXT,
            calories REAL,
            protein REAL,
            carbs REAL,
            fat REAL,
            fiber REAL
        )
    """)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ifct_name ON ifct_foods(name)")
    # Upsert so corrections to IFCT_SEED_DATA reach databases that already exist.
    c.executemany(
        """
        INSERT INTO ifct_foods (name, aliases, calories, protein, carbs, fat, fiber)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            aliases  = excluded.aliases,
            calories = excluded.calories,
            protein  = excluded.protein,
            carbs    = excluded.carbs,
            fat      = excluded.fat,
            fiber    = excluded.fiber
        """,
        IFCT_SEED_DATA,
    )
    conn.commit()
    conn.close()


def search_ifct(food_name: str) -> Optional[NutritionPer100g]:
    """Search IFCT database. Tries exact match, then alias match."""
    conn = sqlite3.connect(IFCT_DB_PATH)
    c = conn.cursor()
    query = food_name.lower().strip()

    # 1. Exact name match
    row = c.execute(
        "SELECT name, calories, protein, carbs, fat, fiber FROM ifct_foods WHERE LOWER(name) = ?",
        (query,)
    ).fetchone()

    # 2. Name contains query, shortest (closest) name first so the result does
    #    not depend on insertion order.
    if not row:
        row = c.execute(
            "SELECT name, calories, protein, carbs, fat, fiber FROM ifct_foods "
            "WHERE LOWER(name) LIKE ? ORDER BY LENGTH(name) ASC LIMIT 1",
            (f"%{query}%",)
        ).fetchone()

    # 3. Alias match
    if not row:
        row = c.execute(
            "SELECT name, calories, protein, carbs, fat, fiber FROM ifct_foods WHERE LOWER(aliases) LIKE ?",
            (f"%{query}%",)
        ).fetchone()

    # 4. The other direction: a stored name inside the query, so "maggi noodles"
    #    finds "maggi". Longest name wins, so "masala dosa" beats "dosa".
    if not row:
        row = c.execute(
            "SELECT name, calories, protein, carbs, fat, fiber FROM ifct_foods "
            "WHERE ? LIKE '%' || LOWER(name) || '%' ORDER BY LENGTH(name) DESC LIMIT 1",
            (query,)
        ).fetchone()

    conn.close()

    if row:
        return NutritionPer100g(
            food_name=row[0], calories=row[1], protein=row[2],
            carbs=row[3], fat=row[4], fiber=row[5], source="ifct"
        )
    return None


# ── USDA Lookup ───────────────────────────────────────────────────────────────

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

def _is_plausible_match(description: str, query: str) -> bool:
    """
    USDA's search returns a best-effort match for *any* string — "unknown food xyz"
    comes back as "Oats". Require at least one shared word so unrecognised foods
    fall through to not_found instead of being logged as something unrelated.
    """
    stop = {
        "and", "the", "with", "raw", "cooked", "includes", "including",
        "food", "foods", "for", "usda", "program", "distribution",
        "nfs", "prepared", "from", "not", "further", "specified", "other",
    }

    def words(text: str) -> set:
        out = set()
        for w in re.findall(r"[a-z]+", text.lower()):
            if len(w) <= 2 or w in stop:
                continue
            out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)  # crude plural
        return out

    q_words = words(query)
    if not q_words:
        return False

    # Every meaningful word in the query has to be there. One shared word is not
    # enough: "paneer sandwich" and "Palak Paneer" share "paneer" and are not the
    # same food. Falling through to not_found is better than a confident mistake.
    return q_words <= words(description)


def search_usda(food_name: str) -> Optional[NutritionPer100g]:
    """Search USDA FoodData Central API."""
    if not settings.usda_api_key:
        return None
    try:
        resp = httpx.get(
            USDA_SEARCH_URL,
            params={"query": food_name, "api_key": settings.usda_api_key, "pageSize": 1},
            timeout=5.0,
        )
        resp.raise_for_status()
        foods = resp.json().get("foods", [])
        if not foods:
            return None

        food = foods[0]
        description = food.get("description", food_name)
        if not _is_plausible_match(description, food_name):
            return None

        nutrients = {n["nutrientName"]: n["value"] for n in food.get("foodNutrients", [])}

        return NutritionPer100g(
            food_name=description,
            calories=nutrients.get("Energy", 0),
            protein=nutrients.get("Protein", 0),
            carbs=nutrients.get("Carbohydrate, by difference", 0),
            fat=nutrients.get("Total lipid (fat)", 0),
            fiber=nutrients.get("Fiber, total dietary", 0),
            source="usda",
        )
    except Exception:
        return None


# ── Unit → grams conversion ───────────────────────────────────────────────────

UNIT_TO_GRAMS = {
    "katori": 150,      # small steel bowl ~150ml
    "bowl": 200,
    "small bowl": 150,
    "large bowl": 300,
    "cup": 240,
    "glass": 250,
    "plate": 300,       # a full plate of rice/biryani, not a token serving
    "thali": 500,
    "piece": 100,
    "slice": 30,
    "chapati": 40,
    "roti": 40,
    "paratha": 80,
    "idli": 40,
    "dosa": 100,
    "tbsp": 15,
    "tablespoon": 15,
    "tsp": 5,
    "teaspoon": 5,
    "g": 1,
    "gram": 1,
    "grams": 1,
    "handful": 30,
    "ml": 1,
    "egg": 55,
    "banana": 120,
    "apple": 182,
}

# When the parser says "2 pieces", the weight depends on the food, not the unit.
# Without this, "2 pieces of roti" becomes 200g (~594 kcal) instead of 80g.
PIECE_WEIGHTS = {
    "roti": 40, "chapati": 40, "phulka": 40, "wheat roti": 40, "missi roti": 50,
    "bhatura": 90, "papad": 13, "maggi": 70, "coconut chutney": 30, "chutney": 30,
    "masala dosa": 150, "paneer sandwich": 140, "veg sandwich": 130, "sandwich": 130,
    "paratha": 80, "plain paratha": 80, "aloo paratha": 100,
    "poori": 30, "puri": 30, "naan": 90, "kulcha": 80,
    "idli": 40, "dosa": 100, "masala dosa": 150, "vada": 45, "dhokla": 40,
    "boiled egg": 55, "egg": 55, "omelette": 120, "scrambled eggs": 120,
    "bread": 30, "white bread": 30, "bread slice": 30, "toast": 30,
    "banana": 120, "apple": 182, "orange": 130, "mango": 200,
    "samosa": 60, "pakora": 25, "laddu": 40, "gulab jamun": 40, "jalebi": 30,
    "biscuit": 12, "rusk": 15,
}

# Units that only say "how many", leaving the weight to the food itself.
GENERIC_COUNT_UNITS = {
    "piece", "pieces", "pc", "pcs", "no", "nos",
    "unit", "units", "count", "serving", "servings", "portion",
    "packet", "packets", "pack", "packs",
}


def _piece_weight(food_name: str) -> Optional[float]:
    """Grams for one piece of a named food, or None if we don't know it."""
    name = (food_name or "").lower().strip()
    if not name:
        return None
    if name in PIECE_WEIGHTS:
        return PIECE_WEIGHTS[name]
    # Longest key first so "aloo paratha" wins over "paratha".
    for key in sorted(PIECE_WEIGHTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}s?\b", name):
            return PIECE_WEIGHTS[key]
    return None


def unit_to_grams(unit: str, quantity: float, food_name: str = "") -> float:
    """Convert quantity + unit to grams, using the food name for counted units."""
    unit_lower = unit.lower().strip()

    if unit_lower in GENERIC_COUNT_UNITS:
        per_piece = _piece_weight(food_name)
        if per_piece:
            return quantity * per_piece

    if unit_lower in UNIT_TO_GRAMS:
        return quantity * UNIT_TO_GRAMS[unit_lower]

    # The unit may itself name the food ("2 rotis", "3 idli").
    per_piece = _piece_weight(unit_lower)
    if per_piece:
        return quantity * per_piece

    return quantity * 100  # default 100g if the unit is unknown


# ── Main lookup ───────────────────────────────────────────────────────────────

def lookup_nutrition(food_name: str, quantity: float, unit: str) -> dict:
    """
    Full nutrition lookup for a meal item.
    Returns macros scaled to the actual quantity consumed.
    """
    grams = unit_to_grams(unit, quantity, food_name)

    # Try IFCT first (Indian foods)
    nutrition = search_ifct(food_name)

    # Fall back to USDA
    if not nutrition:
        nutrition = search_usda(food_name)

    if not nutrition:
        # Last resort: return zeros with a flag
        return {
            "food_name": food_name,
            "quantity": quantity,
            "unit": unit,
            "grams": grams,
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "fiber": 0.0,
            "source": "not_found",
        }

    scale = grams / 100.0
    return {
        "food_name": nutrition.food_name,
        "quantity": quantity,
        "unit": unit,
        "grams": grams,
        "calories": round(nutrition.calories * scale, 1),
        "protein": round(nutrition.protein * scale, 1),
        "carbs": round(nutrition.carbs * scale, 1),
        "fat": round(nutrition.fat * scale, 1),
        "fiber": round(nutrition.fiber * scale, 1),
        "source": nutrition.source,
    }
