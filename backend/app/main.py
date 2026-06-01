import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from sqlmodel import Session, SQLModel, select, text

from app.database import engine, db_kind
from app.geocode import geocode
from app.models import (  # noqa: F401 — register tables
    GeocodeCache, Improvement, Poi, Sector, SectorScore,
)

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
