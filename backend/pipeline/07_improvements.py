"""
Week 5 — Improvement suggestions

For each sector × scenario, simulates adding one POI per underperforming
category and computes the resulting score gain.

Algorithm:
  1. For each category with sub_score < 0.70 AND scenario weight ≥ 5.0:
       delta = (1.0 - sub_score) × weight / total_weight × 100  (score points)
  2. Rank by delta desc → keep top 3
  3. Suggest virtual POI location: t_p minutes walk from sector centroid
     in a category-specific compass direction
  4. Build human-readable title: "+1 Pharmacy within 400m"

Outputs:
  data/processed/improvements.csv  — up to 724 × 3 × 3 = 6 516 rows

Run:
  cd backend/pipeline
  python 07_improvements.py

Prerequisites: 02_sectors.py, 05_score.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CITY_CONFIG, CRS_LAMBERT, CRS_WGS84,
    DATA_PROCESSED,
    DECAY_PARAMS, SCENARIO_WEIGHTS,
    WALK_SPEED_DEFAULT, WALK_SPEED_SENIOR,
)

import argparse as _ap
_p = _ap.ArgumentParser(); _p.add_argument("--city", default="brussels", choices=list(CITY_CONFIG))
CITY = _p.parse_known_args()[0].city

if CITY == "brussels":
    SECTORS_PATH = DATA_PROCESSED / "sectors.geojson"
    SCORES_PATH  = DATA_PROCESSED / "scores.csv"
    OUT          = DATA_PROCESSED / "improvements.csv"
else:
    _city_dir    = DATA_PROCESSED / CITY
    SECTORS_PATH = _city_dir / "sectors.geojson"
    SCORES_PATH  = _city_dir / "scores.csv"
    OUT          = _city_dir / "improvements.csv"

CATEGORY_LABELS: dict[str, str] = {
    "school": "school",         "childcare": "childcare centre",
    "playground": "playground", "park": "park",
    "pharmacy": "pharmacy",     "gp": "GP / doctor",
    "hospital": "hospital",     "supermarket": "supermarket",
    "convenience": "local shop","transit": "transit stop",
    "cafe": "café",             "restaurant": "restaurant",
    "coworking": "coworking space","library": "library",
    "sport": "sports facility",
}

# Compass bearings (°) per category — spread virtual pins around the sector
CAT_BEARING: dict[str, float] = {
    "pharmacy": 0,    "gp": 45,       "school": 90,
    "childcare": 135, "supermarket": 180, "convenience": 225,
    "transit": 270,   "park": 315,    "playground": 22.5,
    "library": 67.5,  "sport": 112.5, "cafe": 157.5,
    "restaurant": 202.5, "coworking": 247.5, "hospital": 292.5,
}

SUB_SCORE_THRESHOLD = 0.70   # categories below this qualify for improvement
MIN_WEIGHT          = 5.0    # ignore minor categories


def _offset_coords(lat: float, lng: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """Displace (lat, lng) by dist_m metres in bearing_deg direction."""
    rad = math.radians(bearing_deg)
    dlat = (dist_m / 111_000) * math.cos(rad)
    dlng = (dist_m / (111_000 * math.cos(math.radians(lat)))) * math.sin(rad)
    return round(lat + dlat, 6), round(lng + dlng, 6)


def compute_improvements(
    sector_id: str,
    centroid_lat: float,
    centroid_lng: float,
    composite_score_01: float,
    breakdown: dict[str, float],
    scenario: str,
) -> list[dict]:
    weights = SCENARIO_WEIGHTS[scenario]
    total_w = sum(weights.values())
    composite_int = int(round(composite_score_01 * 100))
    walk_speed = WALK_SPEED_SENIOR if scenario == "senior" else WALK_SPEED_DEFAULT

    candidates = []
    for cat, w in weights.items():
        if w < MIN_WEIGHT:
            continue
        sub = breakdown.get(cat, 0.0)
        if sub >= SUB_SCORE_THRESHOLD:
            continue  # already good

        delta_raw = (1.0 - sub) * w / total_w * 100
        delta = max(1, int(round(delta_raw)))
        t_p = DECAY_PARAMS.get(cat, (5, 20, "nearest"))[0]
        dist_m = t_p * 60 * walk_speed
        label = CATEGORY_LABELS.get(cat, cat)
        dist_label = f"{int(round(dist_m / 50) * 50)}m"  # round to nearest 50m
        title = f"+1 {label} within {dist_label}"

        bearing = CAT_BEARING.get(cat, 45.0)
        sugg_lat, sugg_lng = _offset_coords(centroid_lat, centroid_lng, bearing, dist_m)

        candidates.append({
            "sector_id": sector_id,
            "scenario": scenario,
            "title": title,
            "category": cat,
            "score_delta": delta,
            "from_score": composite_int,
            "to_score": min(100, composite_int + delta),
            "suggested_lat": sugg_lat,
            "suggested_lng": sugg_lng,
        })

    candidates.sort(key=lambda x: x["score_delta"], reverse=True)
    for rank, row in enumerate(candidates[:3], 1):
        row["rank"] = rank
    return candidates[:3]


def main() -> None:
    if OUT.exists():
        print(f"✓ {OUT.name} already exists — delete to regenerate")
        return

    for p in [SECTORS_PATH, SCORES_PATH]:
        if not p.exists():
            raise FileNotFoundError(f"Missing {p.name} — run steps 02 and 05 first")

    print("─── Loading data ───")
    sectors = gpd.read_file(SECTORS_PATH).reset_index(drop=True)
    scores  = pd.read_csv(SCORES_PATH)
    print(f"  {len(sectors)} sectors  |  {len(scores):,} score rows")

    # Compute centroids in WGS84 (projected for accuracy)
    cents = sectors.to_crs(CRS_LAMBERT).geometry.centroid.to_crs(CRS_WGS84)
    sector_centroids = {
        row["id"]: (cents.iloc[i].y, cents.iloc[i].x)
        for i, (_, row) in enumerate(sectors.iterrows())
    }

    print("─── Computing improvements ───")
    all_rows = []
    for _, score_row in scores.iterrows():
        sid = str(score_row["sector_id"])
        scen = str(score_row["scenario"])
        composite = float(score_row["score"])
        breakdown = json.loads(score_row["breakdown"])
        lat, lng = sector_centroids.get(sid, (50.846, 4.352))

        imps = compute_improvements(sid, lat, lng, composite, breakdown, scen)
        all_rows.extend(imps)

    df = pd.DataFrame(all_rows)
    cols = ["sector_id", "scenario", "rank", "title", "category",
            "score_delta", "from_score", "to_score", "suggested_lat", "suggested_lng"]
    df[cols].to_csv(OUT, index=False)

    print(f"\n  ✓ {OUT.name}: {len(df):,} improvement suggestions")
    print(f"    scenarios: {sorted(df['scenario'].unique())}")
    print(f"    avg improvements/sector/scenario: "
          f"{len(df) / (len(sectors) * 3):.1f}")

    print("\n─── Top categories needing improvement ───")
    for scen in ("family", "senior", "remote"):
        top = (df[df["scenario"] == scen]
               .groupby("category")["score_delta"].mean()
               .sort_values(ascending=False).head(3))
        cats = ", ".join(f"{c} (+{d:.0f})" for c, d in top.items())
        print(f"  {scen:<8}  {cats}")

    print("\nNext: update 06_seed.py and run it to load improvements into DB")


if __name__ == "__main__":
    main()
