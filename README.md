# Neighbourhood Fit Score

EU-first location-intelligence tool that scores Brussels neighbourhoods per life-scenario (Family / Senior / Remote Work) with narrative "why" + "how to improve". Ships as a single Docker image.

## Stack

- **Frontend:** React 18, Vite 5 (JavaScript)
- **Backend:** Python 3.12, FastAPI, SQLModel
- **Database:** SQLite locally, PostgreSQL in production (picked automatically from `DATABASE_URL`)
- **Offline pipeline:** `backend/pipeline/` — heavy geo scripts (geopandas, osmnx) that precompute scores; never deployed
- **Deploy:** single Docker image (multi-stage build), hosted on Coolify

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI app, API routes
│   │   ├── database.py    # engine setup (sqlite ↔ postgres via DATABASE_URL)
│   │   └── models.py      # SQLModel table definitions
│   ├── pipeline/          # offline geo scripts (geopandas/osmnx — not deployed)
│   ├── requirements.txt   # runtime deps only
│   └── requirements-pipeline.txt
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       └── styles.css
├── Dockerfile
├── .env.example
├── .gitignore
├── .dockerignore
└── README.md
```

## Local development

No database to install — SQLite is created automatically on first run.

**Terminal 1 — backend:**

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 3001
```

**Terminal 2 — frontend:**

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The frontend proxies `/api` requests to the backend at port 3001.

## Deploy (Docker / Coolify)

The app ships as a single Docker image: the multi-stage `Dockerfile` builds the
React frontend, then serves it together with the FastAPI backend from one
container. On boot the container runs `python seed.py` (idempotent — restores the
DB from the committed seed files) and then starts `uvicorn`.

1. Push this repo to GitHub.
2. In Coolify: **New Resource → Docker / Dockerfile** and connect the repo.
3. Set the environment variables below, point the domain at the service, and deploy.

**Environment variables**

| Var | Required | Notes |
|-----|----------|-------|
| `DATABASE_URL` | prod | Postgres URL. Coolify-internal Postgres is plaintext (no `sslmode`); external (Neon/Supabase) needs `?sslmode=require`. Blank → SQLite. |
| `GROQ_API_KEY` | for `/api/explain` | Groq API key (live LLM Q&A). Without it `/api/explain` returns 503. |
| `NOMINATIM_USER_AGENT` | recommended | Identifies the geocoder client to Nominatim. |
| `PORT` | no | Defaults to `3001`. |
| `RESEED_SCORES` | no | When set, refreshes only scores/improvements from the CSVs without wiping the DB. |

> The container is non-root (uid 10001) with a `HEALTHCHECK` probing `/api/health`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | DB connection check; returns `{ status, db, sectors_indexed }` |
| GET | `/api/score` | Score by address (`address`, `scenario`) — geocodes and locates the sector |
| GET | `/api/sector/{id}` | Score by sector id (`scenario`) |
| GET | `/api/compare` | Trade-off comparison of two sectors (`a`, `b`, `scenario`) |
| POST | `/api/explain` | Streaming (SSE) LLM explanation for a sector |
| GET | `/api/sectors.geojson` | All sectors as GeoJSON for the choropleth (`scenario`, `city`) |
| GET | `/api/pois` | POIs for a sector (`sector_id`, `categories`) |
| GET | `/api/filter` | Sectors meeting a min score across categories |
| GET | `*` | Serves the built React app (production only) |
