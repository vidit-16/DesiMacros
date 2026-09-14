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
