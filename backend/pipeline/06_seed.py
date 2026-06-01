"""
Week 3+5 — Database seed

Loads pre-computed pipeline outputs into the app database:
  Sector      — 724 Brussels statistical sectors (geometry, population, centroid)
  SectorScore — 2 172 rows (sector × 3 scenarios): score, percentile, breakdown, pros, cons
  Poi         — OSM POIs + STIB transit stops
  Improvement — up to 6 516 rows: top-3 improvements per sector × scenario (optional)

Run from backend/ directory:
  python pipeline/06_seed.py

With an explicit database URL:
  DATABASE_URL=postgresql://... python pipeline/06_seed.py

Prerequisites (run in order):
  python pipeline/02_sectors.py   →  processed/sectors.geojson
  python pipeline/03_pois.py      →  processed/pois_all.geojson, transit_stops.geojson
  python pipeline/05_score.py     →  processed/scores.csv
  python pipeline/07_improvements.py  →  processed/improvements.csv  (optional)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from sqlmodel import Session, SQLModel, delete, select

# Add backend/ to path so we can import app.*
_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

from app.database import engine
from app.models import Improvement, Poi, Sector, SectorScore  # noqa: F401

PROCESSED = _BACKEND / "pipeline" / "data" / "processed"
IMPROVEMENTS_FILE = PROCESSED / "improvements.csv"
NARRATIVES_FILE   = PROCESSED / "narratives.csv"
SECTORS_FILE  = PROCESSED / "sectors.geojson"
POIS_FILE     = PROCESSED / "pois_all.geojson"
TRANSIT_FILE  = PROCESSED / "transit_stops.geojson"
SCORES_FILE   = PROCESSED / "scores.csv"

# ---------------------------------------------------------------------------
# Category labels for pros / cons
# ---------------------------------------------------------------------------
_LABELS: dict[str, str] = {
    "school":       "schools",
    "childcare":    "childcare centres",
    "playground":   "playgrounds",
    "park":         "parks & green space",
    "pharmacy":     "pharmacies",
    "gp":           "GPs / doctors",
    "hospital":     "hospitals / clinics",
    "supermarket":  "supermarkets",
    "convenience":  "local shops",
    "transit":      "public transport",
    "cafe":         "cafés",
    "restaurant":   "restaurants",
    "coworking":    "coworking spaces",
    "library":      "libraries",
    "sport":        "sports facilities",
}


def _pros_cons(breakdown: dict, weights: dict) -> tuple[list[str], list[str]]:
    """Template-based pros/cons from category sub-scores."""
    pros, cons = [], []
    # Sort by weight × score gap (most impactful categories first)
    items = sorted(
        ((cat, score, weights.get(cat, 0.0)) for cat, score in breakdown.items()),
        key=lambda x: x[2],
        reverse=True,
    )
    for cat, score, w in items:
        if w < 3.0:
            continue
        label = _LABELS.get(cat, cat)
        if score >= 0.70 and len(pros) < 4:
            pros.append(f"Good access to {label}")
        elif score <= 0.30 and len(cons) < 4:
            cons.append(f"Limited {label} within walking distance")
    return pros, cons


# ---------------------------------------------------------------------------
# Sector seeding
# ---------------------------------------------------------------------------

def _seed_sectors(session: Session, gdf: gpd.GeoDataFrame) -> int:
    gdf_wgs = gdf.to_crs("EPSG:4326")
    centroids = gdf_wgs.to_crs("EPSG:31370").geometry.centroid.to_crs("EPSG:4326")

    records = []
    for idx, row in gdf_wgs.iterrows():
        geom = json.loads(shapely.to_geojson(row.geometry)) if row.geometry else None
        cent = centroids.iloc[idx] if isinstance(idx, int) else centroids[idx]
        records.append(Sector(
            id=str(row["id"]),
            name_fr=row.get("name_fr"),
            name_nl=row.get("name_nl"),
            cd_munty_refnis=str(row.get("cd_munty_refnis", "")),
            population=int(row["population"]) if pd.notna(row.get("population")) else None,
            area_ha=float(row["area_ha"]) if pd.notna(row.get("area_ha")) else None,
            geometry=geom,
            centroid_lon=round(cent.x, 6),
            centroid_lat=round(cent.y, 6),
        ))

    session.add_all(records)
    return len(records)


# ---------------------------------------------------------------------------
# Score seeding
# ---------------------------------------------------------------------------

def _load_narratives() -> dict[tuple[str, str], tuple[str, list]]:
    """Load narratives.csv → {(sector_id, scenario): (narrative, highlights)}."""
    out: dict[tuple[str, str], tuple[str, list]] = {}
    if not NARRATIVES_FILE.exists():
        return out
    nar_df = pd.read_csv(NARRATIVES_FILE, keep_default_na=False)
    for _, row in nar_df.iterrows():
        key = (str(row["sector_id"]), str(row["scenario"]))
        highlights = json.loads(row["highlights_json"]) if row.get("highlights_json") else []
        out[key] = (str(row.get("narrative", "")), highlights)
    return out


def _seed_scores(session: Session, df: pd.DataFrame) -> int:
    from config import SCENARIO_WEIGHTS  # noqa: local import to avoid circular

    narratives = _load_narratives()
    if narratives:
        print(f"  Narratives loaded: {len(narratives)}")

    records = []
    for _, row in df.iterrows():
        breakdown = json.loads(row["breakdown"])
        weights = SCENARIO_WEIGHTS.get(row["scenario"], {})
        pros, cons = _pros_cons(breakdown, weights)
        key = (str(row["sector_id"]), str(row["scenario"]))
        narrative, highlights = narratives.get(key, ("", []))
        records.append(SectorScore(
            sector_id=str(row["sector_id"]),
            scenario=str(row["scenario"]),
            score=int(round(float(row["score"]) * 100)),
            percentile=int(round(float(row["percentile"]))),
            breakdown=breakdown,
            pros=pros,
            cons=cons,
            narrative=narrative or None,
            highlights=highlights or None,
        ))

    session.add_all(records)
    return len(records)


# ---------------------------------------------------------------------------
# POI seeding
# ---------------------------------------------------------------------------

def _seed_pois(session: Session) -> int:
    records = []

    if POIS_FILE.exists():
        pois = gpd.read_file(POIS_FILE).to_crs("EPSG:4326")
        for _, row in pois.iterrows():
            if row.geometry is None or row.geometry.is_empty:
                continue
            records.append(Poi(
                sector_id=str(row["sector_id"]) if pd.notna(row.get("sector_id")) else None,
                category=str(row["category"]),
                name=row.get("name") if pd.notna(row.get("name")) else None,
                lat=round(row.geometry.y, 6),
                lng=round(row.geometry.x, 6),
            ))

    if TRANSIT_FILE.exists():
        transit = gpd.read_file(TRANSIT_FILE).to_crs("EPSG:4326")
        for _, row in transit.iterrows():
            if row.geometry is None or row.geometry.is_empty:
                continue
            records.append(Poi(
                sector_id=str(row["sector_id"]) if pd.notna(row.get("sector_id")) else None,
                category="transit",
                name=row.get("stop_name") if pd.notna(row.get("stop_name")) else None,
                lat=round(row.geometry.y, 6),
                lng=round(row.geometry.x, 6),
            ))

    # Batch insert in chunks to avoid memory pressure
    BATCH = 500
    for i in range(0, len(records), BATCH):
        session.add_all(records[i : i + BATCH])
        session.flush()

    return len(records)


# ---------------------------------------------------------------------------
# Improvement seeding
# ---------------------------------------------------------------------------

def _seed_improvements(session: Session, df: pd.DataFrame) -> int:
    records = [
        Improvement(
            sector_id=str(row["sector_id"]),
            scenario=str(row["scenario"]),
            rank=int(row["rank"]),
            title=str(row["title"]),
            category=str(row["category"]),
            score_delta=int(row["score_delta"]),
            from_score=int(row["from_score"]),
            to_score=int(row["to_score"]),
            suggested_lat=float(row["suggested_lat"]) if pd.notna(row.get("suggested_lat")) else None,
            suggested_lng=float(row["suggested_lng"]) if pd.notna(row.get("suggested_lng")) else None,
        )
        for _, row in df.iterrows()
    ]
    BATCH = 500
    for i in range(0, len(records), BATCH):
        session.add_all(records[i : i + BATCH])
        session.flush()
    return len(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    for p, label in [(SECTORS_FILE, "sectors.geojson"), (SCORES_FILE, "scores.csv")]:
        if not p.exists():
            raise FileNotFoundError(f"Missing {label} — run pipeline steps 02–05 first")

    print("─── Creating / verifying tables ───")
    SQLModel.metadata.create_all(engine)

    print("─── Clearing existing data ───")
    with Session(engine) as session:
        session.exec(delete(Improvement))
        session.exec(delete(SectorScore))
        session.exec(delete(Poi))
        session.exec(delete(Sector))
        session.commit()

    print("─── Loading sectors ───")
    sectors_gdf = gpd.read_file(SECTORS_FILE).reset_index(drop=True)

    print("─── Loading scores ───")
    scores_df = pd.read_csv(SCORES_FILE)

    # Load improvements if available (generated by 07_improvements.py)
    improvements_df = pd.read_csv(IMPROVEMENTS_FILE) if IMPROVEMENTS_FILE.exists() else None
    if improvements_df is None:
        print("─── Improvements: not found (run 07_improvements.py to generate) ───")

    print("─── Seeding database ───")
    with Session(engine) as session:
        n_sectors = _seed_sectors(session, sectors_gdf)
        print(f"  Sectors:      {n_sectors}")

        n_scores = _seed_scores(session, scores_df)
        print(f"  Scores:       {n_scores}")

        n_pois = _seed_pois(session)
        print(f"  POIs:         {n_pois:,}")

        n_imps = 0
        if improvements_df is not None:
            n_imps = _seed_improvements(session, improvements_df)
        print(f"  Improvements: {n_imps:,}")

        session.commit()

    print("\n─── Verification ───")
    with Session(engine) as session:
        n_s  = len(session.exec(select(Sector)).all())
        n_sc = len(session.exec(select(SectorScore)).all())
        n_p  = len(session.exec(select(Poi)).all())
        n_i  = len(session.exec(select(Improvement)).all())
    print(f"  Sectors: {n_s}  |  Scores: {n_sc}  |  POIs: {n_p:,}  |  Improvements: {n_i:,}")

    print("\n  ✓ Seed complete — start the API:")
    print("    cd backend && uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()
