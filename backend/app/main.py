import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlmodel import Session, SQLModel, select, text

# Canonical scenario weights live in the pipeline config (single source of truth).
# Safe to import at runtime: pipeline/config.py imports only pathlib at module top,
# and uvicorn runs from backend/ where `pipeline` resolves as a namespace package
# (seed.py imports it the same way).
from pipeline.config import SCENARIO_WEIGHTS

from app.database import engine, db_kind
from app.geocode import geocode
from app.models import (  # noqa: F401 — register tables
    GeocodeCache, Improvement, Poi, Sector, SectorScore,
)

logger = logging.getLogger("nfs")


# ---------------------------------------------------------------------------
# Scenario — closed set of valid scoring scenarios.
# Used as the param/body type so FastAPI returns 422 on an unknown value
# instead of a misleading 404 from a downstream "score not found" lookup.
# ---------------------------------------------------------------------------

class Scenario(str, Enum):
    family = "family"
    senior = "senior"
    remote = "remote"

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
    # NOTE: the STRtree currently indexes sectors from ALL cities. With only
    # Brussels loaded this is correct; once a second city (Antwerp) has data,
    # this lookup should be scoped to the requested city to avoid cross-city
    # point-in-polygon false matches. Not implemented yet — single city today.
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

# Compress JSON responses (the GeoJSON choropleth in particular).
app.add_middleware(GZipMiddleware, minimum_size=1024)


# ---------------------------------------------------------------------------
# Rate limiting
# The app runs behind Coolify/Traefik, so the real client IP is in the first
# entry of X-Forwarded-For; fall back to the socket peer for local/direct use.
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please slow down and try again."},
    )


# ---------------------------------------------------------------------------
# Security headers — applied to every response.
# CSP is tuned for this app: the SPA is served same-origin by StaticFiles, the
# only third party is the MapLibre basemap from OpenFreeMap (vector tiles, glyphs
# and sprites from *.openfreemap.org), and the app calls its own /api (same
# origin). MapLibre uses web workers (blob:) and injects inline styles, hence
# worker-src/child-src blob: and style-src 'unsafe-inline'.
# img-src/connect-src also allow *.openstreetmap.org in case the basemap style
# references OSM raster/attribution endpoints.
# ---------------------------------------------------------------------------

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "worker-src 'self' blob:; "
    "child-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://*.openfreemap.org https://*.openstreetmap.org; "
    "font-src 'self' data:; "
    "connect-src 'self' https://*.openfreemap.org https://*.openstreetmap.org; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = _CSP
    return response


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
    except Exception:
        logger.exception("health check failed")
        raise HTTPException(status_code=500, detail="Database unavailable")


@app.get("/api/hello")
def hello():
    return {"message": "Hello from Neighbourhood Fit Score API"}


# ---------------------------------------------------------------------------
# Scenario weights — canonical single source of truth for the frontend.
# Static between deploys, so cache aggressively. No rate limit needed.
# Shape: {"family": {category: weight, ...}, "senior": {...}, "remote": {...}}
# ---------------------------------------------------------------------------

@app.get("/api/weights")
def weights():
    return JSONResponse(
        content=SCENARIO_WEIGHTS,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ---------------------------------------------------------------------------
# Score by address
# ---------------------------------------------------------------------------

@app.get("/api/score")
@limiter.limit("30/minute")
def score_by_address(
    request: Request,
    address: str = Query(..., description="Street address"),
    scenario: Scenario = Query(Scenario.family, description="family | senior | remote"),
    city: str = Query("brussels"),
    db: Session = Depends(get_session),
):
    lat, lng = geocode(address, db, city=city)
    if lat is None:
        raise HTTPException(404, detail=f"Address not found: {address!r}")

    sector_id = _find_sector_id(lat, lng)
    if sector_id is None:
        raise HTTPException(
            404,
            detail=f"No sector found for ({lat:.4f}, {lng:.4f}) in {city}. "
                   "Check that the address is within the city.",
        )

    return _sector_score_response(sector_id, scenario.value, {"lat": lat, "lng": lng}, db)


# ---------------------------------------------------------------------------
# Score by sector id
# ---------------------------------------------------------------------------

@app.get("/api/sector/{sector_id}")
def score_by_sector(
    sector_id: str,
    scenario: Scenario = Query(Scenario.family),
    db: Session = Depends(get_session),
):
    sector = db.get(Sector, sector_id)
    if sector is None:
        raise HTTPException(404, detail=f"Sector not found: {sector_id!r}")
    return _sector_score_response(sector_id, scenario.value, None, db)


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

# Same label set used by /api/explain — kept as a single source of truth.
_CAT_LABELS = _EXPLAIN_CAT_LABELS


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
    scenario: Scenario = Query(Scenario.family),
    db: Session = Depends(get_session),
):
    scenario = scenario.value
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
                "centroid": {"lat": sec_a.centroid_lat, "lng": sec_a.centroid_lon},
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
                "centroid": {"lat": sec_b.centroid_lat, "lng": sec_b.centroid_lon},
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
    scenario: Scenario = Scenario.family
    question: str | None = Field(default=None, max_length=500)


@app.post("/api/explain")
@limiter.limit("10/minute")
async def explain(request: Request, body: ExplainRequest, db: Session = Depends(get_session)):
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
        except Exception:
            # Log the real cause server-side; never leak provider/internal detail
            # (API keys, upstream URLs, stack info) to the client.
            logger.exception("explain stream failed")
            yield f"data: {json.dumps({'error': 'The explanation service is temporarily unavailable.'})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(token_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# All sectors as GeoJSON (choropleth source)
# ---------------------------------------------------------------------------

# Module-level cache of already-serialized GeoJSON bodies, keyed by
# (city, scenario). The choropleth data is static between deploys, so the cache
# is populated lazily on first request and never invalidated (only 3 valid keys
# per city). Each value is (etag, body_bytes).
_GEOJSON_CACHE: dict[tuple[str, str], tuple[str, bytes]] = {}

_GEOJSON_COORD_PRECISION = 5


def _round_coords(coords):
    """Recursively round all float coordinates to _GEOJSON_COORD_PRECISION places.

    GeoJSON coordinate arrays nest arbitrarily (Point -> [x, y],
    Polygon -> [[[x, y], ...]], etc.); walk them and round every float.
    """
    if isinstance(coords, (list, tuple)):
        return [_round_coords(c) for c in coords]
    if isinstance(coords, float):
        return round(coords, _GEOJSON_COORD_PRECISION)
    return coords


@app.get("/api/sectors.geojson")
def sectors_geojson(
    request: Request,
    scenario: Scenario = Query(Scenario.family),
    city: str = Query("brussels"),
    db: Session = Depends(get_session),
):
    scenario = scenario.value
    cache_key = (city, scenario)
    cached = _GEOJSON_CACHE.get(cache_key)

    if cached is None:
        sectors = db.exec(select(Sector).where(Sector.city == city)).all()
        sector_ids = {s.id for s in sectors}
        scores_by_sector = {
            row.sector_id: row
            for row in db.exec(
                select(SectorScore).where(
                    SectorScore.scenario == scenario,
                    SectorScore.sector_id.in_(sector_ids),
                )
            ).all()
        }

        features = []
        for s in sectors:
            if not s.geometry:
                continue
            score_row = scores_by_sector.get(s.id)
            geometry = dict(s.geometry)
            if "coordinates" in geometry:
                geometry["coordinates"] = _round_coords(geometry["coordinates"])
            features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "id": s.id,
                    "name_fr": s.name_fr,
                    "name_nl": s.name_nl,
                    "population": s.population,
                    "score": score_row.score if score_row else None,
                    "percentile": score_row.percentile if score_row else None,
                },
            })

        body = json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
        ).encode("utf-8")
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        cached = (etag, body)
        _GEOJSON_CACHE[cache_key] = cached

    etag, body = cached
    headers = {"Cache-Control": "public, max-age=3600", "ETag": etag}

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    return Response(content=body, media_type="application/json", headers=headers)


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
# Sector filter — find sectors where all selected categories meet min score
# ---------------------------------------------------------------------------

# Categories the filter accepts: the scoring breakdown categories plus the
# POI-presence-only "dog_park". Derived from the shared label map so it stays in
# sync with the categories the rest of the API understands.
_FILTER_CATEGORIES = set(_EXPLAIN_CAT_LABELS) | {"dog_park"}


@app.get("/api/filter")
def filter_sectors(
    scenario: Scenario = Query(Scenario.family),
    categories: str = Query(..., description="Comma-separated category names"),
    min_score: int = Query(60, ge=0, le=100),
    city: str = Query("brussels"),
    db: Session = Depends(get_session),
):
    scenario = scenario.value
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    if not cats:
        raise HTTPException(400, detail="At least one category required")
    unknown = [c for c in cats if c not in _FILTER_CATEGORIES]
    if unknown:
        raise HTTPException(400, detail=f"Unknown categories: {', '.join(unknown)}")
    threshold = min_score / 100.0
    city_sector_ids = {
        s.id for s in db.exec(select(Sector).where(Sector.city == city)).all()
    }
    scores = db.exec(
        select(SectorScore).where(SectorScore.scenario == scenario)
    ).all()

    # Categories not in the scoring breakdown are filtered by POI presence instead
    score_cats = [c for c in cats if c not in {"dog_park"}]
    poi_cats   = [c for c in cats if c in {"dog_park"}]

    # Build set of sector_ids that have at least one POI for each poi_cat
    poi_sector_sets: list[set] = []
    for poi_cat in poi_cats:
        ids = {
            p.sector_id for p in db.exec(
                select(Poi).where(Poi.category == poi_cat, Poi.sector_id.isnot(None))
            ).all()
        }
        poi_sector_sets.append(ids)

    matching = [
        row.sector_id for row in scores
        if row.sector_id in city_sector_ids
        and row.breakdown
        and all(float(row.breakdown.get(cat, 0)) >= threshold for cat in score_cats)
        and all(row.sector_id in s for s in poi_sector_sets)
    ]
    return {"matching": matching, "total": len(matching)}


# ---------------------------------------------------------------------------
# Serve built React app in production
# ---------------------------------------------------------------------------

_public = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(_public):
    app.mount("/", StaticFiles(directory=_public, html=True), name="static")
