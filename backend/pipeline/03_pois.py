"""
Extract OSM POIs for Brussels from the Geofabrik Belgium PBF.
Uses osmium-tool (CLI) + pyosmium + geopandas.

Workflow:
  1. osmium extract  — crop Belgium PBF to Brussels bbox → brussels.osm.pbf
  2. osmium export   — convert OSM → GeoJSON (nodes + closed ways as polygons)
  3. geopandas       — categorise, area-gate parks, spatial-join to sectors
  4. STIB GTFS       — transit stops with departure frequency

Outputs:
  data/processed/pois_all.geojson        — all POIs with category + sector_id
  data/processed/sector_amenities.csv    — sector_id × category counts
  data/processed/transit_stops.geojson  — STIB stops with freq_peak / freq_allday

Run:
  cd backend/pipeline
  python 03_pois.py

Dependencies:
  brew install osmium-tool      (one-time system install)
  pip install osmium geopandas  (already in requirements-pipeline.txt)
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CITY_CONFIG, CRS_LAMBERT, CRS_WGS84,
    DATA_PROCESSED, DATA_RAW,
    LOW_CONFIDENCE_CATEGORIES, OSM_CATEGORY_TAGS, PARK_MIN_HA,
)

import argparse as _ap
_p = _ap.ArgumentParser(); _p.add_argument("--city", default="brussels", choices=list(CITY_CONFIG))
CITY = _p.parse_known_args()[0].city
CITY_BBOX = CITY_CONFIG[CITY]["bbox"]

PBF_BELGIUM  = DATA_RAW / "belgium-latest.osm.pbf"
PBF_CITY     = DATA_RAW / f"{CITY}.osm.pbf"
GEOJSON_POIS = DATA_RAW / f"{CITY}_pois.geojson"

if CITY == "brussels":
    SECTORS_PATH  = DATA_PROCESSED / "sectors.geojson"
    OUT_POIS      = DATA_PROCESSED / "pois_all.geojson"
    OUT_AMENITIES = DATA_PROCESSED / "sector_amenities.csv"
    OUT_TRANSIT   = DATA_PROCESSED / "transit_stops.geojson"
else:
    _city_dir     = DATA_PROCESSED / CITY
    SECTORS_PATH  = _city_dir / "sectors.geojson"
    OUT_POIS      = _city_dir / "pois_all.geojson"
    OUT_AMENITIES = _city_dir / "sector_amenities.csv"
    OUT_TRANSIT   = _city_dir / "transit_stops.geojson"


# ---------------------------------------------------------------------------
# Category assignment
# ---------------------------------------------------------------------------

def assign_category(tags: dict) -> str | None:
    amenity    = tags.get("amenity", "")
    shop       = tags.get("shop", "")
    leisure    = tags.get("leisure", "")
    landuse    = tags.get("landuse", "")
    natural_   = tags.get("natural", "")
    healthcare = tags.get("healthcare", "")
    office     = tags.get("office", "")
    building   = tags.get("building", "")

    if amenity == "school" or building == "school":                         return "school"
    if amenity in ("kindergarten", "childcare"):                            return "childcare"
    if leisure == "playground":                                             return "playground"
    if leisure in ("park", "garden", "nature_reserve") \
            or landuse == "forest" or natural_ == "wood":                   return "park"
    if amenity == "library":                                                return "library"
    if amenity == "pharmacy" or healthcare == "pharmacy":                   return "pharmacy"
    if amenity in ("hospital", "clinic") \
            or healthcare in ("hospital", "clinic"):                        return "hospital"
    if amenity == "doctors" or healthcare == "doctor":                      return "gp"
    if shop == "supermarket":                                               return "supermarket"
    if shop in ("convenience", "greengrocer", "bakery"):                    return "convenience"
    if amenity == "cafe":                                                   return "cafe"
    if amenity in ("restaurant", "fast_food"):                              return "restaurant"
    if amenity == "coworking_space" or office == "coworking":              return "coworking"
    if amenity == "bench" or leisure == "picnic_table":                     return "bench"
    if leisure in ("sports_centre", "fitness_centre", "pitch",
                   "swimming_pool"):                                        return "sport"
    if amenity in ("community_centre", "social_centre"):                    return "community"
    if amenity == "veterinary" or shop == "pet":                           return "veterinary"
    if leisure == "dog_park":                                               return "dog_park"
    return None


# ---------------------------------------------------------------------------
# osmium CLI helpers
# ---------------------------------------------------------------------------

def _require_osmium() -> str:
    path = shutil.which("osmium")
    if path is None:
        raise RuntimeError(
            "osmium-tool not found. Install it:\n"
            "  brew install osmium-tool\n"
            "Then re-run this script."
        )
    return path


def _run(cmd: list[str], desc: str) -> None:
    print(f"  {desc}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")


def extract_brussels(osmium: str) -> None:
    if PBF_CITY.exists():
        print(f"  ✓ {PBF_CITY.name} already exists")
        return
    bbox_str = f"{CITY_BBOX[0]},{CITY_BBOX[1]},{CITY_BBOX[2]},{CITY_BBOX[3]}"
    _run(
        [osmium, "extract", "-b", bbox_str,
         str(PBF_BELGIUM), "-o", str(PBF_CITY), "--overwrite"],
        f"Extracting {CITY} bbox {bbox_str} from Belgium PBF …"
    )
    size_mb = PBF_CITY.stat().st_size / 1_048_576
    print(f"    ✓ {PBF_CITY.name}  ({size_mb:.1f} MB)")


def export_geojson(osmium: str) -> None:
    if GEOJSON_POIS.exists():
        print(f"  ✓ {GEOJSON_POIS.name} already exists")
        return

    # Build tag filter expression for osmium: nwr/key=v1,v2,...
    # Collect all tag values per key from OSM_CATEGORY_TAGS
    from collections import defaultdict
    tag_index: dict[str, set[str]] = defaultdict(set)
    for tag_list in OSM_CATEGORY_TAGS.values():
        for tag_dict in tag_list:
            for k, v in tag_dict.items():
                tag_index[k].add(v)

    # Build filter expressions: key=v1,v2 (or just key= for all values)
    exprs = []
    for k, vals in tag_index.items():
        exprs.append(f"nwr/{k}={','.join(sorted(vals))}")

    # osmium tags-filter to a temp file, then export
    filtered_pbf = DATA_RAW / f"{CITY}_pois.osm.pbf"
    if not filtered_pbf.exists():
        _run(
            [osmium, "tags-filter", str(PBF_CITY),
             *exprs, "-o", str(filtered_pbf), "--overwrite"],
            f"Filtering POI tags from {CITY} PBF …"
        )
        size_mb = filtered_pbf.stat().st_size / 1_048_576
        print(f"    ✓ {filtered_pbf.name}  ({size_mb:.1f} MB)")

    _run(
        [osmium, "export", str(filtered_pbf),
         "--geometry-types=point,polygon",
         "-f", "geojson",
         "-o", str(GEOJSON_POIS), "--overwrite"],
        "Exporting POIs to GeoJSON …"
    )
    size_mb = GEOJSON_POIS.stat().st_size / 1_048_576
    print(f"    ✓ {GEOJSON_POIS.name}  ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Load & categorise
# ---------------------------------------------------------------------------

def load_and_categorise() -> gpd.GeoDataFrame:
    print(f"  Reading {GEOJSON_POIS.name} …")
    gdf = gpd.read_file(GEOJSON_POIS)
    print(f"  Raw features: {len(gdf):,}")

    # Assign category
    def _cat(row):
        tags = {k: (row[k] if k in row.index and pd.notna(row[k]) else "")
                for k in ["amenity", "shop", "leisure", "landuse", "natural",
                           "healthcare", "office", "building"]}
        return assign_category(tags)

    gdf["category"] = gdf.apply(_cat, axis=1)
    gdf = gdf[gdf["category"].notna()].copy()
    print(f"  After categorisation: {len(gdf):,}")

    # Compute area in m² (Lambert projection)
    gdf_proj = gdf.to_crs(CRS_LAMBERT)
    gdf["area_m2"] = gdf_proj.geometry.area

    # Park area gate: keep only parks ≥ PARK_MIN_HA
    park_mask = gdf["category"] == "park"
    if park_mask.any():
        min_m2 = PARK_MIN_HA * 10_000
        drop = park_mask & (gdf["area_m2"] < min_m2)
        kept = park_mask.sum() - drop.sum()
        gdf = gdf[~drop].copy()
        print(f"  Parks: {kept} kept (≥{PARK_MIN_HA} ha), {drop.sum()} dropped")

    # Convert all geometries to points for distance scoring
    is_poly = ~gdf.geometry.geom_type.isin(["Point", "MultiPoint"])
    if is_poly.any():
        gdf_proj2 = gdf[is_poly].to_crs(CRS_LAMBERT)
        points = gdf_proj2.geometry.representative_point().to_crs(CRS_WGS84)
        gdf.loc[is_poly, "geometry"] = points.values

    return gdf


def join_to_sectors(pois: gpd.GeoDataFrame, sectors: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    pois_proj = pois.to_crs(CRS_LAMBERT)
    sectors_proj = sectors[["id", "geometry"]].to_crs(CRS_LAMBERT)
    joined = gpd.sjoin(pois_proj, sectors_proj, how="left", predicate="within")
    joined = joined.rename(columns={"id": "sector_id"}).drop(columns=["index_right"], errors="ignore")
    inside = joined["sector_id"].notna().sum()
    outside = joined["sector_id"].isna().sum()
    print(f"  Spatial join: {inside:,} inside sectors, {outside:,} outside/unmatched")
    return joined.to_crs(CRS_WGS84)


def build_amenity_table(pois: gpd.GeoDataFrame) -> pd.DataFrame:
    return (
        pois[pois["sector_id"].notna()]
        .groupby(["sector_id", "category"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )


# ---------------------------------------------------------------------------
# STIB GTFS transit
# ---------------------------------------------------------------------------

def process_stib_gtfs() -> gpd.GeoDataFrame | None:
    gtfs_zip = DATA_RAW / "stib_gtfs.zip"
    gtfs_dir = DATA_RAW / "stib_gtfs"

    if not gtfs_zip.exists() and not gtfs_dir.exists():
        print("  ⚠  STIB GTFS not found — transit stops skipped")
        print("     Download from: https://opendata.stib-mivb.be/")
        print("     → GTFS Files (Production) → save as data/raw/stib_gtfs.zip")
        return None

    if not gtfs_dir.exists():
        print(f"  Extracting {gtfs_zip.name} …")
        gtfs_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(gtfs_zip) as z:
            z.extractall(gtfs_dir)

    def _find(name: str) -> Path | None:
        for p in [gtfs_dir / name, *gtfs_dir.glob(f"**/{name}")]:
            if p.exists():
                return p
        return None

    stops_path = _find("stops.txt")
    if stops_path is None:
        print("  ⚠  stops.txt not found in GTFS archive")
        return None

    print("  Loading STIB stops …")
    stops = pd.read_csv(stops_path)
    print(f"  Stops: {len(stops):,}")

    stop_times_path = _find("stop_times.txt")
    trips_path = _find("trips.txt")
    calendar_path = _find("calendar.txt")

    freq_df = None
    if stop_times_path and trips_path and calendar_path:
        freq_df = _compute_frequency(stop_times_path, trips_path, calendar_path)

    stops_gdf = gpd.GeoDataFrame(
        stops,
        geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]),
        crs=CRS_WGS84,
    )
    if freq_df is not None:
        stops_gdf = stops_gdf.merge(freq_df, on="stop_id", how="left")
    else:
        stops_gdf["freq_peak"] = None
        stops_gdf["freq_allday"] = None

    sectors = gpd.read_file(SECTORS_PATH)
    stops_proj = stops_gdf.to_crs(CRS_LAMBERT)
    sectors_proj = sectors[["id", "geometry"]].to_crs(CRS_LAMBERT)
    joined = gpd.sjoin(stops_proj, sectors_proj, how="left", predicate="within")
    joined = joined.rename(columns={"id": "sector_id"}).drop(columns=["index_right"], errors="ignore")
    print(f"  Transit stops in sectors: {joined['sector_id'].notna().sum():,}")
    return joined.to_crs(CRS_WGS84)


def _compute_frequency(stop_times_path, trips_path, calendar_path) -> pd.DataFrame:
    print("  Computing transit frequency …")
    calendar = pd.read_csv(calendar_path)
    monday_services = set(calendar[calendar["monday"] == 1]["service_id"].astype(str))
    if not monday_services:
        monday_services = set(calendar["service_id"].astype(str))

    trips = pd.read_csv(trips_path, usecols=["trip_id", "service_id"])
    trips["service_id"] = trips["service_id"].astype(str)
    monday_trips = set(trips[trips["service_id"].isin(monday_services)]["trip_id"])

    # engine='python' avoids a C-parser bug with usecols on Python 3.14
    st_all = pd.read_csv(
        stop_times_path,
        usecols=["trip_id", "stop_id", "departure_time"],
        engine="python",
    )
    st = st_all[st_all["trip_id"].isin(monday_trips)].copy()
    if st.empty:
        return pd.DataFrame(columns=["stop_id", "freq_peak", "freq_allday"])

    def _to_min(t):
        try:
            h, m, _ = str(t).split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return float("nan")

    st["min_of_day"] = st["departure_time"].apply(_to_min)
    peak = st[(st["min_of_day"] >= 420) & (st["min_of_day"] < 540)]
    allday = st[(st["min_of_day"] >= 360) & (st["min_of_day"] < 1320)]
    freq = (
        peak.groupby("stop_id").size().rename("freq_peak")
        .to_frame()
        .join(allday.groupby("stop_id").size().rename("freq_allday"), how="outer")
        .fillna(0).astype(int).reset_index()
    )
    print(f"  Frequency computed for {len(freq):,} stops")
    return freq


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_POIS.parent.mkdir(parents=True, exist_ok=True)

    if OUT_POIS.exists() and OUT_AMENITIES.exists():
        print(f"  ✓ {OUT_POIS.name} already exists — delete to regenerate")
        return

    if not PBF_BELGIUM.exists():
        raise FileNotFoundError(f"PBF not found: {PBF_BELGIUM}\nRun: python 01_download.py")

    if not SECTORS_PATH.exists():
        raise FileNotFoundError(f"sectors.geojson missing — run: python 02_sectors.py --city {CITY}")

    osmium = _require_osmium()
    print(f"  osmium: {osmium}  city: {CITY}")

    print(f"\n─── Step 1: Extract {CITY} from Belgium PBF ───")
    extract_brussels(osmium)

    print("\n─── Step 2: Export POIs to GeoJSON ───")
    export_geojson(osmium)

    print("\n─── Step 3: Categorise & filter ───")
    pois = load_and_categorise()

    print("\n─── Category counts ───")
    for cat, n in pois["category"].value_counts().items():
        flag = "  ⚠  low-confidence" if cat in LOW_CONFIDENCE_CATEGORIES else ""
        print(f"  {cat:<18} {n:>5}{flag}")

    print("\n─── Step 4: Spatial join to sectors ───")
    sectors = gpd.read_file(SECTORS_PATH)
    pois = join_to_sectors(pois, sectors)

    keep = [c for c in ["category", "sector_id", "name", "name:fr", "name:nl", "area_m2", "geometry"]
            if c in pois.columns]
    pois[keep].to_file(OUT_POIS, driver="GeoJSON")
    print(f"\n  ✓ {len(pois):,} POIs → {OUT_POIS}")

    amenity_table = build_amenity_table(pois)
    amenity_table.to_csv(OUT_AMENITIES, index=False)
    print(f"  ✓ Amenity table: {len(amenity_table)} sectors × {len(amenity_table.columns) - 1} categories")

    transit_type = CITY_CONFIG[CITY]["transit"]
    print(f"\n─── Step 5: {transit_type.upper()} transit ───")
    transit = process_stib_gtfs()
    if transit is not None:
        transit.to_file(OUT_TRANSIT, driver="GeoJSON")
        print(f"  ✓ {len(transit):,} transit stops → {OUT_TRANSIT.name}")

    print(f"\nNext: python 04_graph.py --city {CITY}")


if __name__ == "__main__":
    main()
