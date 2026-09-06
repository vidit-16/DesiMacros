# 🥗 DesiMacros — Conversational Calorie Tracker

A calorie and macro tracker built for Indian diets. Log meals by describing them
in plain English — "2 rotis with dal tadka and a katori of dahi" — and it works
out the portions, macros and daily totals for you.

Built because every mainstream tracker expects you to weigh food in grams and
has no idea what a katori is.

## What it does

- **Talk to log.** An LLM turns a free-text meal description into structured
  items, splitting combined dishes ("rajma chawal" → rajma + rice) and handling
  desi units (katori, chapati, glass, handful).
- **Indian food first.** Nutrition comes from a curated IFCT table of common
  Indian dishes, falling back to the USDA database for everything else. Foods it
  genuinely can't find are flagged instead of quietly guessed.
- **Portion-aware.** "2 pieces" means something different for a roti (40g) than
  for a dosa (100g), and the lookup knows the difference.
- **Goals from your stats.** Mifflin-St Jeor BMR → TDEE → calorie and macro
  targets based on your activity level and whether you're cutting or bulking.
- **Insights.** Instant rule-based alerts on the day's macros, multi-day pattern
  detection, and an LLM-written weekly summary.
- **A log per visitor.** On a shared deployment each visitor gets their own
  entries and goals, keyed by a token the app keeps in the URL. Run it locally
  with no token and it behaves as a plain single-user app.

## Stack

- **Backend**: FastAPI + SQLAlchemy (SQLite locally, Postgres when deployed)
- **Frontend**: Streamlit
- **LLM**: Groq (`openai/gpt-oss-120b` by default, set `GROQ_MODEL` to change)
- **Nutrition**: curated IFCT subset in SQLite + USDA FoodData Central API
- **Container**: Docker

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env      # then add your keys
```

You need a free [Groq API key](https://console.groq.com/keys) for meal parsing.
The [USDA key](https://fdc.nal.usda.gov/api-key-signup) is optional — without it
you just lose the non-Indian food fallback.

Run the two processes in separate terminals:

```bash
uvicorn app.api.main:app --reload --port 8000
streamlit run app/ui/streamlit_app.py
```

The UI is at http://localhost:8501, the API docs at http://localhost:8000/docs.
Both SQLite databases are created and seeded on first run.

### Docker

```bash
docker compose up --build
```

Or run everything in one container (Streamlit public, API internal on 8000) —
this is what a single-service host like Render or Hugging Face Spaces wants:

```bash
docker build -t desimacros . && docker run -p 8501:8501 --env-file .env desimacros
```

## Deploying

The image runs both processes in one container, with Streamlit on the public
port and the API reachable only from inside it, so any single-service host works.

`render.yaml` describes the service for **Render**, so pointing a Blueprint at
this repository is enough to deploy it. Render prompts for `GROQ_API_KEY` and
the other secrets in its own dashboard; none of them live in the repo. The
container listens on whatever `$PORT` the host sets.

Hugging Face Spaces works too, but only its Docker SDK can run a process like
this, and that is a paid tier.

Storage on free tiers is ephemeral, so SQLite files are cleared whenever the app
restarts. To keep history, create a free Postgres database (Neon or Supabase) and
set `DATABASE_URL` to its connection string — paste it as given, including the
`postgres://` form some providers hand out, and the app points it at the right
driver itself. Nothing else changes: the schema is created on first boot.

With `DATABASE_URL` left alone the app uses SQLite exactly as before, so local
development needs no database server.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `GROQ_API_KEY` | Meal parsing + weekly summary | *required* |
| `GROQ_MODEL` | Groq model ID (they get retired periodically) | `openai/gpt-oss-120b` |
| `USDA_API_KEY` | Fallback nutrition lookup | optional |
| `DATABASE_URL` | Where meal logs live; SQLite or Postgres | `sqlite:///./data/desimacros.db` |
| `API_BASE_URL` | Where the UI finds the API | `http://localhost:8000` |
| `DEMO_MODE` | Warn visitors that data resets on restart | off |

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/log` | Parse a meal description and log it |
| `GET` | `/api/summary` | One day's totals and entries |
| `GET` | `/api/history` | Entries grouped by date over a range |
| `DELETE` | `/api/entry/{id}` | Remove a mis-logged entry |
| `GET` | `/api/weekly` | Last 7 days of totals |
| `GET` | `/api/alerts` | Rule-based alerts for a day |
| `GET` | `/api/patterns` | Multi-day pattern detection |
| `GET` | `/api/weekly-summary` | LLM-written weekly feedback |
| `GET`/`POST` | `/api/profile` | Read/update stats and recalculate goals |
| `POST` | `/api/tdee-preview` | Calculate goals without saving |

## Project structure

```
calorie-tracker/
├── app/
│   ├── api/main.py            # FastAPI routes
│   ├── core/config.py         # Settings from .env
│   ├── db/models.py           # SQLAlchemy models + migrations
│   ├── services/
│   │   ├── meal_parser.py     # LLM text → structured meal items
│   │   ├── nutrition.py       # IFCT + USDA lookup, portion sizes
│   │   ├── tdee.py            # BMR / TDEE / macro targets
│   │   └── insights.py        # Alerts, patterns, weekly summary
│   └── ui/streamlit_app.py    # Streamlit frontend
├── data/                      # SQLite DBs, created on first run
├── tests/test_parser.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── start.sh                   # Runs both processes in one container
```

## Tests

```bash
python -m tests.test_parser
```

The nutrition half runs without any API key. Uncomment `test_parser()` at the
bottom of the file to exercise the LLM parser once `GROQ_API_KEY` is set.

## Notes on the data

The IFCT table is a curated subset of ~37 common Indian dishes with approximate
per-100g values based on the NIN IFCT 2017 publication, plus portion weights for
counted foods. It's good enough for day-to-day tracking, not for clinical use.
Editing `IFCT_SEED_DATA` and restarting updates existing databases in place.
