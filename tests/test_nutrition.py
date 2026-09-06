"""
Assertions for the nutrition layer.
Run from the project root: python -m tests.test_nutrition

Offline: touches only the local IFCT table and pure functions, no API calls.

These exist because a corrupted regex once disabled portion matching entirely
without failing anything - every lookup silently fell back to a 100g default.
"""

import sys

from app.services.nutrition import (
    init_ifct_db, lookup_nutrition, search_ifct, unit_to_grams,
    _piece_weight, _is_plausible_match,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def check_close(label, got, want, tol=0.5):
    if abs(got - want) > tol:
        failures.append(f"{label}: got {got!r}, want ~{want!r}")


def main():
    init_ifct_db()

    # ── portion weights ───────────────────────────────────────────────────────
    # A counted "piece" means different grams for different foods.
    check("roti piece", _piece_weight("roti"), 40)
    check("dosa piece", _piece_weight("dosa"), 100)
    check("papad piece", _piece_weight("papad"), 13)

    # Matching must work inside a longer name, and across a plural s.
    check("qualified name", _piece_weight("tandoori roti"), 40)
    check("plural", _piece_weight("boiled eggs"), 55)
    check("plural inside name", _piece_weight("2 rotis"), 40)
    check("unknown food", _piece_weight("something unheard of"), None)

    # Longest key wins, so a stuffed paratha isn't priced as a plain one.
    check("longest match", _piece_weight("aloo paratha"), 100)

    # ── unit conversion ───────────────────────────────────────────────────────
    check("2 roti pieces", unit_to_grams("piece", 2, "roti"), 80)
    check("katori", unit_to_grams("katori", 1, "dal tadka"), 150)
    check("plate is not 100g", unit_to_grams("plate", 1, "biryani"), 300)
    check("grams passthrough", unit_to_grams("g", 150, "chicken"), 150)
    check("unknown unit default", unit_to_grams("blorp", 1, "mystery"), 100)
    # The unit itself can name the food: "3 rotis".
    check("unit names the food", unit_to_grams("roti", 3, ""), 120)

    # ── IFCT lookup ───────────────────────────────────────────────────────────
    check("alias lookup", search_ifct("dahi").food_name, "curd")
    check("reverse containment", search_ifct("maggi noodles").food_name, "maggi")
    # Splitting these apart mattered: a paratha carries a paratha's fat.
    check("roti is not paratha", search_ifct("roti").food_name, "roti")
    check("masala dosa is its own dish", search_ifct("masala dosa").food_name, "masala dosa")
    # A bare ingredient must not resolve to whichever dish happens to contain it.
    check("paneer is not palak paneer", search_ifct("paneer").food_name, "paneer")
    check("a sandwich is not a bread slice", search_ifct("sandwich").food_name, "veg sandwich")
    check("paneer sandwich exists", search_ifct("paneer sandwich").food_name, "paneer sandwich")

    # ── scaling ───────────────────────────────────────────────────────────────
    r = lookup_nutrition("roti", 2, "piece")
    check("scaled source", r["source"], "ifct")
    check("scaled grams", r["grams"], 80)
    check_close("scaled calories", r["calories"], 211.2)

    # ── USDA relevance guard ──────────────────────────────────────────────────
    # USDA answers every query with something; unrelated matches must be refused.
    check("rejects unrelated", _is_plausible_match("Oats (Includes foods for USDA's Food Distribution Program)", "unknown food xyz"), False)
    check("accepts related", _is_plausible_match("Soybean curd", "curd"), True)
    check("accepts multi-word", _is_plausible_match("CHICKEN BREAST", "chicken breast"), True)
    check("plural tolerated", _is_plausible_match("Almonds, raw", "almond"), True)
    # One shared word is not enough - this logged 3.5 sandwiches as palak paneer.
    check("one shared word is not a match", _is_plausible_match("Palak Paneer", "paneer sandwich"), False)
    check("different dish rejected", _is_plausible_match("Chicken Biryani", "mutton biryani"), False)

    # ── the reported failure, end to end ──────────────────────────────────────
    r = lookup_nutrition("paneer sandwich", 3.5, "piece")
    check("sandwich source", r["source"], "ifct")
    check("sandwich matched", r["food_name"], "paneer sandwich")
    check("sandwich grams", r["grams"], 490.0)

    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All nutrition checks passed.")


if __name__ == "__main__":
    main()
