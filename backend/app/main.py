import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from sqlmodel import Session, SQLModel, select, text

from app.database import engine, db_kind
from app.geocode import geocode
from app.models import (  # noqa: F401 — register tables
    GeocodeCache, Improvement, Poi, Sector, SectorScore,
)

# ---------------------------------------------------------------------------
# Groq client (lazy — only used for /api/explain)
# ---------------------------------------------------------------------------

_GROQ_BASE    = "https://api.groq.com/openai/v1"
_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

_EXPLAIN_SYSTEM = """\
You are a neighbourhood analyst answering questions for people considering relocating to Brussels.

Rules you MUST follow:
1. Use ONLY the facts provided in the user message — no external knowledge or invented data.
2. Do not mention demographics, income, ethnicity, or crime.
3. Write in British English, present tense, 2-4 sentences.
4. If the question cannot be answered from the provided facts, say so briefly.
5. Never start with "I" or "As an AI". Be direct and specific.
"""

_EXPLAIN_CAT_LABELS: dict[str, str] = {
    "school": "schools", "childcare": "childcare", "playground": "playgrounds",
    "park": "parks", "pharmacy": "pharmacies", "gp": "GP clinics",
    "hospital": "hospitals", "supermarket": "supermarkets",
    "convenience": "local shops", "transit": "public transport",
    "cafe": "cafés", "restaurant": "restaurants", "library": "libraries",
    "sport": "sports facilities", "coworking": "coworking spaces",
}

_SCENARIO_LABELS: dict[str, str] = {
    "family": "families with children",
    "senior": "older adults",
    "remote": "remote workers",
}

# ---------------------------------------------------------------------------
# Spatial index (loaded once at startup)
# ---------------------------------------------------------------------------

_sector_ids: list[str] = []
_strtree: STRtree | None = None


def _build_spatial_index(session: Session) -> None:
    global _sector_ids, _strtree
    sectors = session.exec(select(Sector)).all()
    geoms, ids = [], []
    for s in sectors:
        if s.geometry:
            try:
                geoms.append(shape(s.geometry))
                ids.append(s.id)
            except Exception:
                pass
    _sector_ids = ids
    _strtree = STRtree(geoms) if geoms else None


def _find_sector_id(lat: float, lng: float) -> str | None:
    if _strtree is None:
        return None
    pt = Point(lng, lat)
    hits = _strtree.query(pt, predicate="contains")
    if len(hits) == 0:
        return None
    return _sector_ids[int(hits[0])]


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    print(f"db: {db_kind}")
    with Session(engine) as session:
        _build_spatial_index(session)
    n = len(_sector_ids)
    print(f"spatial index: {n} sectors")
    yield


app = FastAPI(lifespan=lifespan)


def get_session():
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": db_kind, "sectors_indexed": len(_sector_ids)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/hello")
def hello():
    return {"message": "Hello from Neighbourhood Fit Score API"}


# ---------------------------------------------------------------------------
# Score by address
# ---------------------------------------------------------------------------

@app.get("/api/score")
def score_by_address(
    address: str = Query(..., description="Street address in Brussels"),
    scenario: str = Query("family", description="family | senior | remote"),
    db: Session = Depends(get_session),
):
    lat, lng = geocode(address, db)
    if lat is None:
        raise HTTPException(404, detail=f"Address not found: {address!r}")

    sector_id = _find_sector_id(lat, lng)
    if sector_id is None:
        raise HTTPException(
            404,
            detail=f"No Brussels sector found for ({lat:.4f}, {lng:.4f}). "
                   "Check that the address is within Brussels municipality.",
        )

    return _sector_score_response(sector_id, scenario, {"lat": lat, "lng": lng}, db)


# ---------------------------------------------------------------------------
# Score by sector id
# ---------------------------------------------------------------------------

@app.get("/api/sector/{sector_id}")
def score_by_sector(
    sector_id: str,
    scenario: str = Query("family"),
    db: Session = Depends(get_session),
):
    sector = db.get(Sector, sector_id)
    if sector is None:
        raise HTTPException(404, detail=f"Sector not found: {sector_id!r}")
    return _sector_score_response(sector_id, scenario, None, db)


def _sector_score_response(
    sector_id: str,
    scenario: str,
    geocode_result: dict | None,
    db: Session,
) -> dict:
    sector = db.get(Sector, sector_id)
    if sector is None:
        raise HTTPException(404, detail=f"Sector not found: {sector_id!r}")

    score_row = db.exec(
        select(SectorScore).where(
            SectorScore.sector_id == sector_id,
            SectorScore.scenario == scenario,
        )
    ).first()
    if score_row is None:
        raise HTTPException(
            404,
            detail=f"No score for sector {sector_id!r}, scenario {scenario!r}. "
                   "Run pipeline/06_seed.py first.",
        )

    improvements = db.exec(
        select(Improvement)
        .where(Improvement.sector_id == sector_id, Improvement.scenario == scenario)
        .order_by(Improvement.rank)
    ).all()

    response: dict[str, Any] = {
        "sector": {
            "id": sector.id,
            "name_fr": sector.name_fr,
            "name_nl": sector.name_nl,
            "municipality": sector.cd_munty_refnis,
            "population": sector.population,
            "centroid": {"lat": sector.centroid_lat, "lng": sector.centroid_lon},
        },
        "scenario": scenario,
        "score": score_row.score,
        "percentile": score_row.percentile,
        "breakdown": score_row.breakdown,
        "narrative": score_row.narrative,
        "highlights": score_row.highlights or [],
        "pros": score_row.pros or [],
        "cons": score_row.cons or [],
        "improvements": [
            {
                "rank": imp.rank,
                "title": imp.title,
                "category": imp.category,
                "score_delta": imp.score_delta,
                "from_score": imp.from_score,
                "to_score": imp.to_score,
                "suggested_lat": imp.suggested_lat,
                "suggested_lng": imp.suggested_lng,
            }
            for imp in improvements
        ],
        "disclosure": {
            "data_date": "2024",
            "source": "OpenStreetMap contributors, Statbel, STIB-MIVB",
            "note": "Scores represent potential access, not guaranteed availability. "
                    "Hours, private facilities, and service quality are not modelled.",
        },
    }
    if geocode_result:
        response["geocode"] = geocode_result
    return response


# ---------------------------------------------------------------------------
# Trade-off comparison between two sectors
# ---------------------------------------------------------------------------

_CAT_LABELS: dict[str, str] = {
    "school": "schools", "childcare": "childcare", "playground": "playgrounds",
    "park": "parks", "pharmacy": "pharmacies", "gp": "GP clinics",
    "hospital": "hospitals", "supermarket": "supermarkets",
    "convenience": "local shops", "transit": "public transport",
    "cafe": "cafés", "restaurant": "restaurants", "library": "libraries",
    "sport": "sports facilities", "coworking": "coworking spaces",
}


def _tradeoff_narrative(
    name_a: str, name_b: str, score_a: int, score_b: int, deltas: list[dict],
) -> str:
    diff = score_a - score_b
    if abs(diff) <= 3:
        return f"{name_a} and {name_b} score almost equally for this scenario."
    better, worse = (name_a, name_b) if diff > 0 else (name_b, name_a)
    better_key = "a" if diff > 0 else "b"
    worse_key  = "b" if diff > 0 else "a"
    b_wins = [d for d in deltas if d["winner"] == better_key]
    w_wins = [d for d in deltas if d["winner"] == worse_key]
    parts = [f"{better} scores {abs(diff)} points higher overall."]
    if b_wins:
        cats = " and ".join(_CAT_LABELS.get(d["category"], d["category"]) for d in b_wins[:2])
        parts.append(f"It leads on {cats}.")
    if w_wins:
        cats = " and ".join(_CAT_LABELS.get(d["category"], d["category"]) for d in w_wins[:1])
        parts.append(f"{worse} has an edge in {cats}.")
    return " ".join(parts)


@app.get("/api/compare")
def compare_sectors(
    a: str = Query(..., description="Sector ID A"),
    b: str = Query(..., description="Sector ID B"),
    scenario: str = Query("family"),
    db: Session = Depends(get_session),
):
    sec_a = db.get(Sector, a)
    sec_b = db.get(Sector, b)
    if sec_a is None:
        raise HTTPException(404, detail=f"Sector not found: {a!r}")
    if sec_b is None:
        raise HTTPException(404, detail=f"Sector not found: {b!r}")

    score_a = db.exec(
        select(SectorScore).where(SectorScore.sector_id == a, SectorScore.scenario == scenario)
    ).first()
    score_b = db.exec(
        select(SectorScore).where(SectorScore.sector_id == b, SectorScore.scenario == scenario)
    ).first()
    if score_a is None or score_b is None:
        raise HTTPException(404, detail="Score missing for one or both sectors. Run pipeline first.")

    bd_a: dict = score_a.breakdown or {}
    bd_b: dict = score_b.breakdown or {}
    all_cats = sorted(set(bd_a) | set(bd_b))
    deltas = []
    for cat in all_cats:
        va = round((bd_a.get(cat) or 0.0) * 100)
        vb = round((bd_b.get(cat) or 0.0) * 100)
        diff = vb - va
        deltas.append({
            "category": cat,
            "a": va,
            "b": vb,
            "delta": diff,
            "winner": "b" if diff > 5 else "a" if diff < -5 else "tie",
        })
    deltas.sort(key=lambda x: abs(x["delta"]), reverse=True)

    name_a = sec_a.name_fr or sec_a.id
    name_b = sec_b.name_fr or sec_b.id

    return {
        "scenario": scenario,
        "a": {
            "sector": {
                "id": sec_a.id,
                "name_fr": sec_a.name_fr,
                "name_nl": sec_a.name_nl,
                "municipality": sec_a.cd_munty_refnis,
            },
            "score": score_a.score,
            "percentile": score_a.percentile,
        },
        "b": {
            "sector": {
                "id": sec_b.id,
                "name_fr": sec_b.name_fr,
                "name_nl": sec_b.name_nl,
                "municipality": sec_b.cd_munty_refnis,
            },
            "score": score_b.score,
            "percentile": score_b.percentile,
        },
        "deltas": deltas,
        "tradeoffNarrative": _tradeoff_narrative(name_a, name_b, score_a.score, score_b.score, deltas),
    }


# ---------------------------------------------------------------------------
# Live Grok explanation (SSE streaming)
# ---------------------------------------------------------------------------

class ExplainRequest(BaseModel):
    sector_id: str
    scenario: str = "family"
    question: str | None = None


@app.post("/api/explain")
async def explain(body: ExplainRequest, db: Session = Depends(get_session)):
    if not _GROQ_API_KEY:
        raise HTTPException(503, detail="GROQ_API_KEY not configured on this server.")

    sector = db.get(Sector, body.sector_id)
    if sector is None:
        raise HTTPException(404, detail=f"Sector not found: {body.sector_id!r}")

    score_row = db.exec(
        select(SectorScore).where(
            SectorScore.sector_id == body.sector_id,
            SectorScore.scenario == body.scenario,
        )
    ).first()
    if score_row is None:
        raise HTTPException(404, detail="Score not found — run the pipeline first.")

    breakdown: dict = score_row.breakdown or {}
    facts = [
        {
            "category": _EXPLAIN_CAT_LABELS.get(cat, cat),
            "score": round(float(v) * 100),
        }
        for cat, v in sorted(breakdown.items(), key=lambda kv: -float(kv[1]))
        if cat in _EXPLAIN_CAT_LABELS
    ]

    sector_name = sector.name_fr or body.sector_id
    scenario_label = _SCENARIO_LABELS.get(body.scenario, body.scenario)
    question_line = (
        f'User question: "{body.question}"'
        if body.question
        else f"Summarise what makes this neighbourhood good or challenging for {scenario_label}."
    )

    user_msg = json.dumps({
        "sector": sector_name,
        "scenario": scenario_label,
        "overall_score": score_row.score,
        "facts": facts,
        "task": question_line,
    }, ensure_ascii=False)

    client = AsyncOpenAI(api_key=_GROQ_API_KEY, base_url=_GROQ_BASE)

    async def token_stream():
        try:
            stream = await client.chat.completions.create(
                model=_GROQ_MODEL,
                max_tokens=300,
                temperature=0.4,
                stream=True,
                messages=[
                    {"role": "system", "content": _EXPLAIN_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
            )
            async for chunk in stream:
                token = chunk.choices[0].delta.content or ""
                if token:
                    yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(token_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# All sectors as GeoJSON (choropleth source)
# ---------------------------------------------------------------------------

@app.get("/api/sectors.geojson")
def sectors_geojson(
    scenario: str = Query("family"),
    db: Session = Depends(get_session),
):
    sectors = db.exec(select(Sector)).all()
    scores_by_sector = {
        row.sector_id: row
        for row in db.exec(
            select(SectorScore).where(SectorScore.scenario == scenario)
        ).all()
    }

    features = []
    for s in sectors:
        if not s.geometry:
            continue
        score_row = scores_by_sector.get(s.id)
        features.append({
            "type": "Feature",
            "geometry": s.geometry,
            "properties": {
                "id": s.id,
                "name_fr": s.name_fr,
                "name_nl": s.name_nl,
                "population": s.population,
                "score": score_row.score if score_row else None,
                "percentile": score_row.percentile if score_row else None,
            },
        })

    return JSONResponse({"type": "FeatureCollection", "features": features})


# ---------------------------------------------------------------------------
# POIs for a sector (map layer)
# ---------------------------------------------------------------------------

@app.get("/api/pois")
def pois(
    sector_id: str = Query(...),
    categories: str | None = Query(None, description="Comma-separated list of categories"),
    db: Session = Depends(get_session),
):
    stmt = select(Poi).where(Poi.sector_id == sector_id)
    if categories:
        cats = [c.strip() for c in categories.split(",")]
        stmt = stmt.where(Poi.category.in_(cats))
    rows = db.exec(stmt).all()
    return {
        "sector_id": sector_id,
        "count": len(rows),
        "pois": [
            {
                "id": p.id,
                "category": p.category,
                "name": p.name,
                "lat": p.lat,
                "lng": p.lng,
            }
            for p in rows
        ],
    }


# ---------------------------------------------------------------------------
# Serve built React app in production
# ---------------------------------------------------------------------------

_public = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(_public):
    app.mount("/", StaticFiles(directory=_public, html=True), name="static")
