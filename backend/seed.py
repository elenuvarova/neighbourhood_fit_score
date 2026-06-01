"""
Production seed script — loads committed seed files into the database.

Uses ONLY runtime dependencies (requirements.txt).  No geopandas needed.
Safe to re-run: skips entirely if the Sector table is already populated.

Run from backend/ directory:
  python seed.py

Used as Render preDeployCommand:
  cd /app/backend && python seed.py

Seed files expected at:
  pipeline/data/processed/sectors.geojson          (Brussels)
  pipeline/data/processed/scores.csv
  pipeline/data/processed/improvements.csv   (optional)
  pipeline/data/processed/transit_stops.geojson  (optional)
  pipeline/data/processed/antwerp/sectors.geojson  (Antwerp, when available)
  pipeline/data/processed/antwerp/scores.csv
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape
from sqlmodel import Session, SQLModel, select

_BACKEND = Path(__file__).parent
sys.path.insert(0, str(_BACKEND))

from app.database import engine
from app.models import GeocodeCache, Improvement, Poi, Sector, SectorScore  # noqa: F401

PROCESSED        = _BACKEND / "pipeline" / "data" / "processed"
SECTORS_FILE     = PROCESSED / "sectors.geojson"
SCORES_FILE      = PROCESSED / "scores.csv"
IMPS_FILE        = PROCESSED / "improvements.csv"
NARRATIVES_FILE  = PROCESSED / "narratives.csv"
TRANSIT_FILE     = PROCESSED / "transit_stops.geojson"
POIS_MAP_FILE    = PROCESSED / "pois_map.geojson"   # filtered: school/park/pharmacy/cafe/sport

# Scenario weights (mirrors config.py — kept in sync manually)
_WEIGHTS = {
    "family": {
        "school": 15, "childcare": 10, "supermarket": 10, "pharmacy": 8, "convenience": 2,
        "gp": 9, "hospital": 3, "park": 15, "playground": 8, "transit": 10,
        "cafe": 2, "restaurant": 2, "library": 3, "sport": 3,
    },
    "senior": {
        "supermarket": 12, "convenience": 6, "gp": 27, "hospital": 8,
        "park": 10, "library": 5, "transit": 17, "cafe": 5, "restaurant": 5, "sport": 5,
    },
    "remote": {
        "supermarket": 10, "pharmacy": 7, "convenience": 3, "gp": 5,
        "park": 14, "playground": 4, "transit": 13, "cafe": 8,
        "library": 8, "restaurant": 4, "sport": 4, "coworking": 10,
    },
}

_LABELS = {
    "school": "schools", "childcare": "childcare", "playground": "playgrounds",
    "park": "parks", "pharmacy": "pharmacies", "gp": "GPs", "hospital": "hospitals",
    "supermarket": "supermarkets", "convenience": "local shops", "transit": "transit",
    "cafe": "cafés", "restaurant": "restaurants", "coworking": "coworking",
    "library": "libraries", "sport": "sports",
}


def _pros_cons(breakdown: dict, scenario: str) -> tuple[list, list]:
    weights = _WEIGHTS.get(scenario, {})
    pros, cons = [], []
    for cat, score in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
        w = weights.get(cat, 0)
        if w < 3:
            continue
        label = _LABELS.get(cat, cat)
        if score >= 0.70 and len(pros) < 4:
            pros.append(f"Good access to {label}")
        elif score <= 0.30 and len(cons) < 4:
            cons.append(f"Limited {label} within walking distance")
    return pros, cons


def _seed_sectors(session: Session, sectors_file: Path, city: str = "brussels") -> int:
    with open(sectors_file) as f:
        gj = json.load(f)
    records = []
    for feat in gj["features"]:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        c = geom.centroid
        records.append(Sector(
            id=str(p["id"]),
            city=city,
            name_fr=p.get("name_fr"),
            name_nl=p.get("name_nl"),
            cd_munty_refnis=str(p.get("cd_munty_refnis") or ""),
            population=int(p["population"]) if p.get("population") is not None else None,
            area_ha=float(p["area_ha"]) if p.get("area_ha") is not None else None,
            geometry=feat["geometry"],
            centroid_lon=round(c.x, 6),
            centroid_lat=round(c.y, 6),
        ))
    session.add_all(records)
    return len(records)


def _load_narratives(narratives_file: Path | None = None) -> dict[tuple[str, str], tuple[str, list]]:
    out: dict[tuple[str, str], tuple[str, list]] = {}
    path = narratives_file or NARRATIVES_FILE
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["sector_id"], row["scenario"])
            highlights = json.loads(row["highlights_json"]) if row.get("highlights_json") else []
            out[key] = (row.get("narrative", ""), highlights)
    return out


def _seed_scores(session: Session, scores_file: Path, narratives_file: Path | None = None) -> int:
    narratives = _load_narratives(narratives_file)
    if narratives:
        print(f"  Narratives loaded: {len(narratives)}")
    records = []
    with open(scores_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            breakdown = json.loads(row["breakdown"])
            pros, cons = _pros_cons(breakdown, row["scenario"])
            key = (row["sector_id"], row["scenario"])
            narrative, highlights = narratives.get(key, ("", []))
            records.append(SectorScore(
                sector_id=row["sector_id"],
                scenario=row["scenario"],
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


def _seed_improvements(session: Session, imps_file: Path | None = None) -> int:
    path = imps_file or IMPS_FILE
    if not path.exists():
        return 0
    records = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append(Improvement(
                sector_id=row["sector_id"],
                scenario=row["scenario"],
                rank=int(row["rank"]),
                title=row["title"],
                category=row["category"],
                score_delta=int(row["score_delta"]),
                from_score=int(row["from_score"]),
                to_score=int(row["to_score"]),
                suggested_lat=float(row["suggested_lat"]) if row.get("suggested_lat") else None,
                suggested_lng=float(row["suggested_lng"]) if row.get("suggested_lng") else None,
            ))
    BATCH = 500
    for i in range(0, len(records), BATCH):
        session.add_all(records[i : i + BATCH])
        session.flush()
    return len(records)


def _seed_transit(session: Session, transit_file: Path | None = None) -> int:
    path = transit_file or TRANSIT_FILE
    if not path.exists():
        return 0
    with open(path) as f:
        gj = json.load(f)
    records = []
    for feat in gj["features"]:
        if feat.get("geometry") is None:
            continue
        coords = feat["geometry"]["coordinates"]
        p = feat.get("properties", {})
        records.append(Poi(
            sector_id=str(p["sector_id"]) if p.get("sector_id") else None,
            category="transit",
            name=p.get("stop_name"),
            lat=round(float(coords[1]), 6),
            lng=round(float(coords[0]), 6),
        ))
    BATCH = 500
    for i in range(0, len(records), BATCH):
        session.add_all(records[i : i + BATCH])
        session.flush()
    return len(records)


def _seed_pois(session: Session, pois_file: Path | None = None) -> int:
    """Seed map-visible POIs (school/park/pharmacy/cafe/sport) from pois_map.geojson."""
    path = pois_file or POIS_MAP_FILE
    if not path.exists():
        return 0
    with open(path) as f:
        gj = json.load(f)
    records = []
    for feat in gj["features"]:
        if feat.get("geometry") is None:
            continue
        coords = feat["geometry"]["coordinates"]
        p = feat.get("properties", {})
        records.append(Poi(
            sector_id=str(p["sector_id"]) if p.get("sector_id") else None,
            category=p.get("category", ""),
            name=p.get("name") or p.get("name:fr") or p.get("name:nl") or None,
            lat=round(float(coords[1]), 6),
            lng=round(float(coords[0]), 6),
        ))
    BATCH = 500
    for i in range(0, len(records), BATCH):
        session.add_all(records[i : i + BATCH])
        session.flush()
    return len(records)


def _city_paths(city: str) -> dict:
    """Return file paths for a given city. Brussels uses flat dir; others use subdir."""
    if city == "brussels":
        base = PROCESSED
    else:
        base = PROCESSED / city
    return {
        "sectors":    base / "sectors.geojson",
        "scores":     base / "scores.csv",
        "improvements": base / "improvements.csv",
        "narratives": base / "narratives.csv",
        "transit":    base / "transit_stops.geojson",
        "pois_map":   base / "pois_map.geojson",
    }


KNOWN_CITIES = ["brussels", "antwerp"]


def _seed_city(session: Session, city: str) -> bool:
    """Seed one city's data. Returns True if seeded, False if files not found."""
    paths = _city_paths(city)
    if not paths["sectors"].exists():
        return False
    print(f"\n  ── {city.title()} ──")
    n_s = _seed_sectors(session, paths["sectors"], city=city)
    session.flush()
    print(f"  Sectors:       {n_s}")

    n_sc = _seed_scores(session, paths["scores"], paths["narratives"])
    session.flush()
    print(f"  Scores:        {n_sc}")

    n_i = _seed_improvements(session, paths["improvements"])
    print(f"  Improvements:  {n_i}")

    n_t = _seed_transit(session, paths["transit"])
    print(f"  Transit stops: {n_t}")

    n_p = _seed_pois(session, paths["pois_map"])
    print(f"  Map POIs:      {n_p}")
    return True


def main() -> None:
    SQLModel.metadata.create_all(engine)

    if not SECTORS_FILE.exists():
        print(f"⚠  {SECTORS_FILE} not found — run the pipeline first, then re-deploy")
        return

    with Session(engine) as session:
        existing_sectors = session.exec(select(Sector)).first()
        existing_map_pois = session.exec(
            select(Poi).where(Poi.category != "transit")
        ).first()

        if existing_sectors and existing_map_pois:
            by_city = {}
            for s in session.exec(select(Sector)).all():
                by_city[s.city] = by_city.get(s.city, 0) + 1
            summary = ", ".join(f"{c}: {n}" for c, n in sorted(by_city.items()))
            print(f"✓ DB already seeded ({summary}) — skipping")
            return

        print("─── Seeding database ───")

        if not existing_sectors:
            seeded = []
            for city in KNOWN_CITIES:
                if _seed_city(session, city):
                    seeded.append(city)
            if seeded:
                print(f"\n  ✓ Sectors/scores/improvements seeded: {', '.join(seeded)}")
        else:
            print("  Sectors already present — skipping sector/score seed")

        if not existing_map_pois:
            print("  Seeding missing map POIs …")
            n_p = _seed_pois(session)
            print(f"  Map POIs: {n_p}")

        session.commit()
        print("  ✓ Done")


if __name__ == "__main__":
    main()
