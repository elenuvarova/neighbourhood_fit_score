"""
Extract OSM POIs for Brussels from the Geofabrik Belgium PBF.
Categorise by tag, convert polygons to representative points,
spatial-join to sectors, and compute per-sector counts.

Also extracts STIB GTFS transit stops with departure frequency.

Outputs:
  data/processed/pois_all.geojson        — all POIs with category + sector_id
  data/processed/sector_amenities.csv    — wide table: sector_id × category counts
  data/processed/transit_stops.geojson  — STIB stops with freq_peak / freq_allday

Run:
  cd backend/pipeline
  python 03_pois.py

Notes:
  - Belgium PBF is ~500 MB; pyrosm reads only the Brussels bbox → ~1-3 min.
  - Parks need area ≥ 0.5 ha (PARK_MIN_HA) to count for scoring.
  - Low-confidence categories (coworking, gp, bench) are flagged in output.
"""
from __future__ import annotations

import csv
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyrosm
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    BRUSSELS_BBOX, CRS_LAMBERT, CRS_WGS84,
    DATA_PROCESSED, DATA_RAW,
    LOW_CONFIDENCE_CATEGORIES, OSM_CATEGORY_TAGS, PARK_MIN_HA,
    make_pyrosm_filter,
)

SECTORS_PATH = DATA_PROCESSED / "sectors.geojson"
OUT_POIS = DATA_PROCESSED / "pois_all.geojson"
OUT_AMENITIES = DATA_PROCESSED / "sector_amenities.csv"
OUT_TRANSIT = DATA_PROCESSED / "transit_stops.geojson"

# OSM tag fields pyrosm preserves in the output GeoDataFrame
_TAG_COLS = ["amenity", "shop", "leisure", "landuse", "natural",
             "healthcare", "office", "building", "highway", "name", "name:fr", "name:nl"]


# ---------------------------------------------------------------------------
# Category assignment
# ---------------------------------------------------------------------------

def assign_category(row: pd.Series) -> str | None:
    """Map one OSM feature's tags to a scoring category (or None to drop)."""
    amenity   = str(row.get("amenity")   or "")
    shop      = str(row.get("shop")      or "")
    leisure   = str(row.get("leisure")   or "")
    landuse   = str(row.get("landuse")   or "")
    natural_  = str(row.get("natural")   or "")
    healthcare = str(row.get("healthcare") or "")
    office    = str(row.get("office")    or "")
    building  = str(row.get("building")  or "")

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
# OSM POI extraction via pyrosm
# ---------------------------------------------------------------------------

def _load_pois_from_pbf(pbf_path: Path) -> gpd.GeoDataFrame:
    """Use pyrosm to load all relevant POIs within Brussels bbox."""
    bbox = list(BRUSSELS_BBOX)  # [minx, miny, maxx, maxy]
    print(f"  Loading PBF (Brussels bbox {bbox}) …")
    osm = pyrosm.OSM(str(pbf_path), bounding_box=bbox)

    custom_filter = make_pyrosm_filter()
    print(f"  Filter keys: {list(custom_filter.keys())}")

    # Main POIs (amenity, shop, leisure, office, healthcare, building)
    print("  Extracting POIs …", flush=True)
    pois = osm.get_pois(custom_filter=custom_filter)
    if pois is None or len(pois) == 0:
        raise RuntimeError("pyrosm returned no POIs — check PBF file and bbox")
    print(f"  Raw POIs: {len(pois):,}")

    # Natural features (wood)
    try:
        natural_gdf = osm.get_natural(custom_filter={"natural": ["wood"]})
        if natural_gdf is not None and len(natural_gdf) > 0:
            print(f"  Natural (wood): {len(natural_gdf):,}")
            pois = pd.concat([pois, natural_gdf], ignore_index=True)
    except Exception as e:
        print(f"  ⚠  Could not load natural features: {e}")

    # Landuse features (forest)
    try:
        landuse_gdf = osm.get_landuse(custom_filter={"landuse": ["forest"]})
        if landuse_gdf is not None and len(landuse_gdf) > 0:
            print(f"  Landuse (forest): {len(landuse_gdf):,}")
            pois = pd.concat([pois, landuse_gdf], ignore_index=True)
    except Exception as e:
        print(f"  ⚠  Could not load landuse features: {e}")

    return gpd.GeoDataFrame(pois, geometry="geometry", crs=CRS_WGS84)


def _to_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Convert mixed geometry to points.
    Polygons (parks, schools, etc.) → representative_point.
    Records area in m² for area-gated categories (parks).
    """
    gdf = gdf.copy()

    # Compute area in LAMBERT (accurate metres), then reproject geometry to point
    gdf_proj = gdf.to_crs(CRS_LAMBERT)
    gdf["area_m2"] = gdf_proj.geometry.area

    mask_poly = ~gdf.geometry.geom_type.isin(["Point", "MultiPoint"])
    if mask_poly.any():
        gdf.loc[mask_poly, "geometry"] = (
            gdf_proj.loc[mask_poly].geometry.representative_point().to_crs(CRS_WGS84)
        )

    return gdf


def categorise(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add 'category' column; drop uncategorised rows."""
    tag_cols = [c for c in _TAG_COLS if c in gdf.columns]
    gdf = gdf.copy()
    gdf["category"] = gdf[tag_cols].apply(assign_category, axis=1)
    before = len(gdf)
    gdf = gdf[gdf["category"].notna()].copy()
    print(f"  Categorised: {len(gdf):,} / {before:,} features matched")
    return gdf


def apply_area_gates(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop parks smaller than PARK_MIN_HA."""
    park_mask = gdf["category"] == "park"
    area_col = "area_m2" in gdf.columns
    if park_mask.any() and area_col:
        min_m2 = PARK_MIN_HA * 10_000
        before = park_mask.sum()
        too_small = park_mask & (gdf["area_m2"] < min_m2)
        gdf = gdf[~too_small].copy()
        dropped = too_small.sum()
        print(f"  Parks: dropped {dropped} < {PARK_MIN_HA} ha; kept {before - dropped}")
    return gdf


# ---------------------------------------------------------------------------
# Spatial join to sectors
# ---------------------------------------------------------------------------

def join_to_sectors(pois: gpd.GeoDataFrame, sectors: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Assign each POI to a sector via spatial join (point-in-polygon)."""
    # Work in Lambert for spatial accuracy
    pois_proj = pois.to_crs(CRS_LAMBERT)
    sectors_proj = sectors[["id", "geometry"]].to_crs(CRS_LAMBERT)

    joined = gpd.sjoin(pois_proj, sectors_proj, how="left", predicate="within")
    joined = joined.rename(columns={"id": "sector_id"}).drop(columns=["index_right"], errors="ignore")

    inside = joined["sector_id"].notna().sum()
    outside = joined["sector_id"].isna().sum()
    print(f"  Spatial join: {inside:,} inside sectors, {outside:,} outside/unmatched")

    return joined.to_crs(CRS_WGS84)


# ---------------------------------------------------------------------------
# STIB GTFS transit
# ---------------------------------------------------------------------------

def process_stib_gtfs() -> gpd.GeoDataFrame | None:
    """
    Load STIB GTFS, compute peak and all-day departure frequency per stop.
    Returns a GeoDataFrame of stops with freq_peak / freq_allday columns.
    """
    gtfs_zip = DATA_RAW / "stib_gtfs.zip"
    gtfs_dir = DATA_RAW / "stib_gtfs"

    if not gtfs_zip.exists() and not gtfs_dir.exists():
        print("  ⚠  STIB GTFS not found — transit stops skipped")
        print("     Download from: https://opendata.stib-mivb.be/")
        print("     Save as: data/raw/stib_gtfs.zip")
        return None

    if not gtfs_dir.exists():
        print(f"  Extracting {gtfs_zip.name} …")
        gtfs_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(gtfs_zip) as z:
            z.extractall(gtfs_dir)

    # Find GTFS files (may be at root or in subdirectory)
    def _find_gtfs_file(name: str) -> Path | None:
        for p in [gtfs_dir / name, *gtfs_dir.glob(f"**/{name}")]:
            if p.exists():
                return p
        return None

    stops_path = _find_gtfs_file("stops.txt")
    stop_times_path = _find_gtfs_file("stop_times.txt")
    trips_path = _find_gtfs_file("trips.txt")
    calendar_path = _find_gtfs_file("calendar.txt")

    if stops_path is None:
        print("  ⚠  stops.txt not found in GTFS archive")
        return None

    print(f"  Loading GTFS stops …")
    stops = pd.read_csv(stops_path)
    print(f"  Stops: {len(stops):,}")

    # Compute departure frequency if stop_times is available
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
        print("  ⚠  Frequency computation skipped (missing stop_times/trips/calendar)")

    # Spatial join to sectors
    sectors = gpd.read_file(SECTORS_PATH)
    stops_proj = stops_gdf.to_crs(CRS_LAMBERT)
    sectors_proj = sectors[["id", "geometry"]].to_crs(CRS_LAMBERT)
    joined = gpd.sjoin(stops_proj, sectors_proj, how="left", predicate="within")
    joined = joined.rename(columns={"id": "sector_id"}).drop(columns=["index_right"], errors="ignore")
    print(f"  Transit stops in sectors: {joined['sector_id'].notna().sum():,}")

    return joined.to_crs(CRS_WGS84)


def _compute_frequency(
    stop_times_path: Path,
    trips_path: Path,
    calendar_path: Path,
) -> pd.DataFrame:
    """
    Compute departures/hour per stop for a representative Monday.
    Returns DataFrame with stop_id, freq_peak (7-9h), freq_allday (6-22h).
    """
    print("  Computing transit frequency …")

    calendar = pd.read_csv(calendar_path)
    # Pick service_ids active on Monday
    monday_services = set(
        calendar[calendar["monday"] == 1]["service_id"].astype(str)
    )
    if not monday_services:
        print("  ⚠  No Monday services in calendar — using all services")
        monday_services = set(calendar["service_id"].astype(str))

    trips = pd.read_csv(trips_path, usecols=["trip_id", "service_id"])
    trips["service_id"] = trips["service_id"].astype(str)
    monday_trips = set(trips[trips["service_id"].isin(monday_services)]["trip_id"])

    # Load stop_times (may be large — read in chunks)
    chunks = []
    for chunk in pd.read_csv(
        stop_times_path,
        usecols=["trip_id", "stop_id", "departure_time"],
        chunksize=500_000,
    ):
        chunk = chunk[chunk["trip_id"].isin(monday_trips)]
        chunks.append(chunk)

    if not chunks:
        print("  ⚠  No stop_times matched Monday trips")
        return pd.DataFrame(columns=["stop_id", "freq_peak", "freq_allday"])

    st = pd.concat(chunks, ignore_index=True)

    # Parse HH:MM:SS (GTFS allows hours ≥ 24)
    def _to_minutes(t: str) -> float:
        try:
            h, m, _ = str(t).split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return float("nan")

    st["min_of_day"] = st["departure_time"].apply(_to_minutes)

    # Peak: 7:00–9:00 (420–540 min) → departures per 2-hour window
    peak = st[(st["min_of_day"] >= 420) & (st["min_of_day"] < 540)]
    allday = st[(st["min_of_day"] >= 360) & (st["min_of_day"] < 1320)]  # 6h–22h = 16h

    freq_peak = peak.groupby("stop_id").size().rename("freq_peak")
    freq_allday = allday.groupby("stop_id").size().rename("freq_allday")

    freq = freq_peak.to_frame().join(freq_allday, how="outer").fillna(0).astype(int).reset_index()
    print(f"  Frequency computed for {len(freq):,} stops")
    return freq


# ---------------------------------------------------------------------------
# Per-sector aggregate table
# ---------------------------------------------------------------------------

def build_amenity_table(pois: gpd.GeoDataFrame) -> pd.DataFrame:
    """Wide table: sector_id × category → count."""
    counts = (
        pois[pois["sector_id"].notna()]
        .groupby(["sector_id", "category"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    if OUT_POIS.exists() and OUT_AMENITIES.exists():
        print(f"  ✓ {OUT_POIS.name} and {OUT_AMENITIES.name} already exist — delete to regenerate")
        return

    if not SECTORS_PATH.exists():
        raise FileNotFoundError("Run 02_sectors.py first — sectors.geojson missing")

    pbf_path = DATA_RAW / "belgium-latest.osm.pbf"
    if not pbf_path.exists():
        raise FileNotFoundError(
            f"PBF not found: {pbf_path}\n"
            "Run: python 01_download.py"
        )

    print("─── Extracting OSM POIs ───")
    raw_pois = _load_pois_from_pbf(pbf_path)

    print("\n─── Converting geometries to points ───")
    raw_pois = _to_points(raw_pois)

    print("\n─── Assigning categories ───")
    pois = categorise(raw_pois)

    print("\n─── Applying area gates ───")
    pois = apply_area_gates(pois)

    # Category summary
    print("\n─── Category counts ───")
    counts = pois["category"].value_counts()
    for cat, n in counts.items():
        flag = "⚠  low-confidence" if cat in LOW_CONFIDENCE_CATEGORIES else ""
        print(f"  {cat:<18} {n:>5}  {flag}")

    print("\n─── Spatial join to sectors ───")
    sectors = gpd.read_file(SECTORS_PATH)
    pois = join_to_sectors(pois, sectors)

    print(f"\n─── Saving {OUT_POIS.name} ───")
    keep_cols = ["category", "sector_id", "name", "name:fr", "name:nl",
                 "area_m2", "geometry"]
    keep = [c for c in keep_cols if c in pois.columns]
    pois[keep].to_file(OUT_POIS, driver="GeoJSON")
    print(f"  ✓ {len(pois):,} POIs written")

    print(f"\n─── Building sector amenity table ───")
    amenity_table = build_amenity_table(pois)
    amenity_table.to_csv(OUT_AMENITIES, index=False)
    print(f"  ✓ {OUT_AMENITIES.name}: {len(amenity_table)} sectors × {len(amenity_table.columns) - 1} categories")

    print("\n─── Processing STIB GTFS ───")
    transit = process_stib_gtfs()
    if transit is not None:
        transit.to_file(OUT_TRANSIT, driver="GeoJSON")
        print(f"  ✓ {len(transit):,} transit stops written")

    print("\nNext: python 04_graph.py")


if __name__ == "__main__":
    main()
