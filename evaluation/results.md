# Parser model A/B (run 2026-09-15)

10 hand-labelled Indian meals, production prompt, IFCT-only lookup (USDA disabled).
Reproduce: `python evaluation/ab_parser_models.py` (needs `GROQ_API_KEY`).

| Model | Valid parse | Item count match | Median calorie error | Within 15% | Median latency |
| --- | --- | --- | --- | --- | --- |
| `openai/gpt-oss-120b` (default) | 100% | 100% | 0.0% | 100% | 0.83s |
| `openai/gpt-oss-20b` | 100% | 100% | 0.0% | 100% | 0.82s |
| `llama-3.3-70b-versatile` | n/a | n/a | n/a | n/a | n/a |
| `llama-3.1-8b-instant` | n/a | n/a | n/a | n/a | n/a |

The two Llama models returned `404 model_not_found` from Groq for this account
(retired / unavailable), so they could not be scored. Both gpt-oss models are
tied on this set; the 120b default is kept since the 20b offers no latency win.


# Food estimator A/B (run 2026-09-17)

For foods in neither database the model is asked for per-100 g values
(`app/services/food_estimator.py`). Scored here on the 24 benchmark foods the
IFCT table does not contain, against USDA FNDDS.

| Estimator | Median calorie error | Within 20% | Within 30% |
| --- | --- | --- | --- |
| `openai/gpt-oss-120b` on Groq (default, free) | 9.5% | 18/23 | 19/23 |
| `gpt-5-mini` on OpenAI | 6.0% | 17/23 | 18/23 |
| `gpt-4.1-mini` on OpenAI | 5.9% | 18/24 | 19/24 |

Effectively a tie, so the free default stands; paid calls per logged meal are
not worth a difference this size. All three miss the same foods - chicken curry
(150 estimated against 107), pakora (250-360 against 125), boiled potato (77-87
against 126) - which is the reference disagreeing rather than the model being
wrong: USDA's survey curries are more dilute and its potato entry includes added
fat.

One Groq caveat found while running this: the free tier allows 200,000 tokens a
day per model, and a full benchmark re-parse plus estimates uses a noticeable
share of it. Runs cache both, so a repeat run costs nothing.
