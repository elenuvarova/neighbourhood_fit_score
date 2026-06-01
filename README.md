# Neighbourhood Fit Score

EU-first location-intelligence tool that scores Brussels neighbourhoods per life-scenario (Family / Senior / Remote Work) with narrative "why" + "how to improve". Deploys free on Render.

## Stack

- **Frontend:** React 18, Vite 5 (JavaScript)
- **Backend:** Python 3.12, FastAPI, SQLModel
- **Database:** SQLite locally, PostgreSQL on Render (picked automatically from `DATABASE_URL`)
- **Offline pipeline:** `backend/pipeline/` — heavy geo scripts (geopandas, osmnx) that precompute scores; never deployed
- **Deploy:** Render free tier — free web service + free Postgres, provisioned via `render.yaml` Blueprint

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
├── render.yaml
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

## Deploy to Render

1. Push this repo to GitHub.
2. Go to [Render](https://render.com) → **New → Blueprint** and connect your repo.
3. Render reads `render.yaml`, provisions a free Postgres database and a Docker-based web service, and wires `DATABASE_URL` automatically.

> **Note:** The free web service sleeps after ~15 minutes of inactivity — expect a ~30-second cold start. Render's free Postgres expires after 30 days; the seed script restores it in seconds.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Checks the database connection; returns `{ status, db }` |
| GET | `/api/hello` | Returns a greeting JSON message |
| GET | `*` | Serves the built React app (production only) |
