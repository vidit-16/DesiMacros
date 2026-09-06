# Working on DesiMacros

Architecture, conventions, and the traps that are not obvious from reading the
code. Read this before changing the nutrition or multi-user logic.

## Shape of the app

Two processes. FastAPI owns all data access; Streamlit is a client that talks to
it over HTTP and holds no database connection of its own. In development they
run separately; in a container `start.sh` runs both, with Streamlit on the public
`$PORT` and the API on 8000 reachable only from inside.

```
app/api/main.py       routes, visitor resolution, totals
app/core/config.py    settings from .env
app/db/models.py      SQLAlchemy models, engine selection, migrations
app/services/
  meal_parser.py      LLM text -> structured items
  nutrition.py        IFCT + USDA lookup, portion weights
  tdee.py             BMR / TDEE / macro targets (pure functions)
  insights.py         alerts, patterns, weekly summary
app/ui/streamlit_app.py
```

## Commands

```bash
uvicorn app.api.main:app --reload --port 8000
streamlit run app/ui/streamlit_app.py
python -m tests.test_nutrition    # offline assertions
docker compose up --build
```

## Traps

**`st.cache_data` is shared across sessions, not per user.** Any cached function
that fetches per-visitor data must take the visitor token as an argument so it
lands in the cache key. `load_profile(token)` does this. Forget it and one
visitor is served another's profile for the length of the TTL.

**Every visitor is a row, identified by a token in the URL.** The UI generates
it, keeps it in the `u` query parameter, and sends it as `X-User-Token`. The API
resolves it in the `get_current_user` dependency; a request with no token gets
the local profile, the one row where `token IS NULL`. That fallback is what
keeps single-user local development working, so do not make the header
mandatory. Anything reading or writing a user's data must go through the
dependency — do not reintroduce `db.query(User).first()` in a route, and scope
mutations by `user.id` the way `delete_entry` does.

**Streamlit's session state does not survive a refresh**, which is why identity
lives in the URL rather than in `st.session_state`. Streamlit 1.35 can read
cookies but not set them, so a cookie-based session is not available without a
version bump and a component.

**Groq retires model IDs.** `llama-3.3-70b-versatile` stopped existing during
development and every meal log started returning a 500. The model is read from
`GROQ_MODEL` for this reason. If parsing breaks with a 404 from Groq, check
which models the key can actually see before debugging anything else.

**Reasoning models spend `max_tokens` before they answer.** With gpt-oss the
visible reply is what is left after the reasoning, so budgets are deliberately
larger than the reply needs. Cutting them produces truncated JSON and empty
parses, which surface as a 422.

**The IFCT table is seeded by upsert**, keyed on name. Edit `IFCT_SEED_DATA` and
restart, and existing databases are corrected in place — no migration needed.

**USDA answers every query with something.** Searching it for nonsense returns a
confident, unrelated food. `_is_plausible_match` requires a shared word before a
USDA result is accepted; without it, unrecognised foods were logged as whatever
the search happened to surface. Keep the guard when touching that path.

**Portion weight is a three-step decision** in `unit_to_grams`: a counted unit
("piece", "packet", "serving") looks up the food in `PIECE_WEIGHTS`; otherwise a
known unit in `UNIT_TO_GRAMS` wins; otherwise the unit itself may name the food
("3 rotis"); otherwise 100g. The food-aware step matters — without it "2 pieces"
of anything is 200g, which had rotis at three times their real calories. A
corrupted regex once disabled the substring matching here silently, so
`tests/test_nutrition.py` asserts it.

**SQLite and Postgres are both supported.** `DATABASE_URL` decides; provider
URLs in `postgres://` or `postgresql://` form are rewritten to psycopg 3 in
`_normalize_db_url`. `check_same_thread` is SQLite-only and the in-place column
migration is too, since it exists purely to patch older SQLite files. Read env
vars with `os.getenv("X") or default`, never a default argument — a hosting
dashboard supplies an empty string for a blank field, and a default argument
will not catch it.

**The container runs as UID 1000** because that is how some hosts run
containers; anything the app writes has to be owned by `appuser`. It also sets
`TZ=Asia/Kolkata`, without which `date.today()` follows UTC and files anything
logged before 05:30 IST under the previous day.

## Conventions

- `.env` is never committed. `.env.example` documents the variables; hosts get
  their secrets from their own dashboard, and `render.yaml` marks them
  `sync: false` so they are prompted for rather than stored.
- `data/` is not committed either. Both SQLite files are created and seeded on
  first run.
- Nutrition values are approximations, and new ones should be added with that
  understood — see [ACCURACY.md](ACCURACY.md) for what is and is not measured.
