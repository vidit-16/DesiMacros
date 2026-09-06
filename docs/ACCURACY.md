# How accurate is DesiMacros?

Short version: good enough to tell you that your protein is low three days
running, not good enough to tell you that you ate 1,847 calories. This page
explains where every number comes from, what has actually been measured, and
which errors are still in there.

## How one number is produced

Four steps, each with its own error:

1. **Parse.** An LLM turns your sentence into items with a quantity and a unit.
   "2 rotis with dal" becomes `roti / 2 / piece` and `dal tadka / 1 / katori`,
   the katori being inferred because you didn't say.
2. **Match.** The food name is looked up in a curated IFCT table of Indian
   dishes; anything not there falls through to the USDA FoodData Central API.
3. **Weigh.** The quantity and unit become grams. This is where the biggest
   assumptions live — see below.
4. **Scale.** Per-100g macros are multiplied by the gram weight.

## What has been measured

Fifteen realistic meal descriptions, 30 items total, run end to end.

**Database coverage**

| | IFCT | USDA fallback | Not found |
|---|---|---|---|
| Before the fixes below | 77% | 20% | 3% |
| After | 93% | 7% | 0% |

Read that second row with suspicion. The same 15 meals that produced the
measurement were used to decide what to add, so 93% is an optimistic number on
a sample the table has now seen. It says the common cases work; it does not
generalise to arbitrary meals.

**Parse stability.** Two meal descriptions, three runs each: identical calorie
totals every time. One run labelled rotis with the unit `roti` and another with
`piece`; both resolve to 40g, so the total was unaffected. At `temperature=0.1`
the parser is effectively deterministic for ordinary inputs.

**Corrections this evaluation forced.** Each was found by measurement, not by
reading code:

| Item | Was | Now | Why it was wrong |
|---|---|---|---|
| chicken biryani, 1 plate | 210 kcal | 630 kcal | "plate" was an unknown unit, defaulting to 100g |
| papad, 1 piece | 371 kcal | 48 kcal | 100g default; a papad is about 13g |
| bhature, 2 | 0 kcal | 650 kcal | missing from the table, logged as nothing |
| filter coffee, 1 cup | 2 kcal | 144 kcal | USDA matched plain black coffee |
| coconut chutney, 1 serving | 246 kcal | 58 kcal | generic USDA chutney at a 100g serving |
| masala dosa | 200 kcal | 263 kcal | was an alias of plain dosa, so no filling |
| maggi, 1 packet | 137 kcal | 315 kcal | USDA plain noodles at a 100g default |

A separate defect surfaced while checking these: the regex doing portion
matching had been corrupted into a character class that never matched, so every
food whose name was not an exact dictionary key silently fell back to 100g.
"tandoori roti" and "boiled eggs" were affected; "roti" and "boiled egg" were
not. `tests/test_nutrition.py` now asserts these paths so it cannot happen
quietly again.

## Errors still in there

Roughly in order of how much they matter:

**Volume units assume water.** A katori is 150g whatever is in it. Poha and
upma are fluffy and weigh well under that; dal and curd are close; a katori of
dry nuts would be far over. This is probably the largest systematic error in
the app and it applies to most logged items.

**Cooking fat is invisible.** The single biggest calorie variable in Indian
cooking is how much oil or ghee went in, and nothing in a sentence like "dal
chawal" carries it. Home dal and restaurant dal makhani differ by more than a
factor of two. The table stores one number per dish.

**Generic words have no right answer.** "sabzi" means "a vegetable dish". It is
currently matched against USDA and returns *SABZI POLO*, a Persian rice dish, at
186 kcal per katori. A generic Indian mixed-vegetable value would be a guess of
a different flavour. Any single number here is fiction; the honest fix is asking
which sabzi.

**The table is 43 dishes, approximated.** Values are per-100g figures based on
the NIN IFCT 2017 publication and common references, not the full IFCT dataset,
and the six most recent entries are approximations added to close gaps this
evaluation exposed. Editing `IFCT_SEED_DATA` and restarting updates existing
databases in place.

**USDA is a US database.** When an Indian dish falls through to it, the match is
approximate at best. A word-overlap check rejects matches sharing no words with
the query, so unknown foods now log as 0 kcal and are flagged rather than being
silently recorded as something unrelated — before that check, "unknown food xyz"
came back as Oats at 389 kcal.

**Vague input becomes an assumption.** "some rice" gets a conservative estimate
and a `parse_confidence` of low or medium, surfaced in the UI. Nothing prevents
the assumption from being wrong.

**Unknown foods count as zero.** They are flagged in the log, but the day's
total is quietly short by whatever they were.

## What this has not been measured against

Nobody has weighed a plate of food and compared it to what the app produced.
Every number above is coverage and internal consistency, not ground truth. The
honest next step for anyone wanting a real error bar is to weigh ten ordinary
meals on a kitchen scale, log them as you normally would, and compare — that
would turn "probably fine for trends" into an actual percentage.

## What it is and is not for

Reasonable: tracking direction over weeks, noticing that protein is consistently
low, comparing Tuesday against Wednesday, getting a rough daily figure without
weighing anything.

Not reasonable: precise calorie counting, medical or clinical use, managing a
condition, or anything where being wrong by a third would matter.

**This is not medical or dietary advice.** The calorie and macro targets come
from the Mifflin-St Jeor equation applied to numbers you typed in, which is a
population-level estimate and not a recommendation for you specifically. Talk to
a doctor or a registered dietitian before acting on any of it, particularly if
you are managing a health condition, pregnant, or under 18.
