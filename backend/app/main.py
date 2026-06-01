import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, text

from app.database import engine, db_kind
from app.models import Improvement, Poi, Sector, SectorScore  # noqa: F401 — register tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    print(f"db: {db_kind}")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": db_kind}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend 👋"}


# Serve the built React app in production.
# API routes above take precedence; this catches everything else.
_public = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(_public):
    app.mount("/", StaticFiles(directory=_public, html=True), name="static")
