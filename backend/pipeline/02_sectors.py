"""
Load Statbel statistical sectors (2024) for Brussels Capital Region.
Join population data. Output GeoJSON in WGS84.

Output:
  data/processed/sectors.geojson   (724 sectors, EPSG:4326)

Run:
  cd backend/pipeline
  python 02_sectors.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    BRUSSELS_REFNIS, CRS_LAMBERT, CRS_WGS84,
    DATA_PROCESSED, DATA_RAW,
)

OUT = DATA_PROCESSED / "sectors.geojson"


# ---------------------------------------------------------------------------
# Sector boundaries
# ---------------------------------------------------------------------------

def _find_sqlite(directory: Path) -> Path | None:
    candidates = [
        p for p in directory.glob("*.sqlite")
        if "statbel" in p.name.lower() or "sector" in p.name.lower()
    ]
    return candidates[0] if candidates else next(iter(directory.glob("*.sqlite")), None)


def load_sectors() -> gpd.GeoDataFrame:
    sqlite_path = _find_sqlite(DATA_RAW)

    if sqlite_path is None:
        zip_path = DATA_RAW / "statbel_sectors.zip"
        if zip_path.exists():
            print(f"  Extracting {zip_path.name} …")
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(DATA_RAW)
            sqlite_path = _find_sqlite(DATA_RAW)

    if sqlite_path is None:
        raise FileNotFoundError(
            f"No Statbel sectors .sqlite found in {DATA_RAW}\n"
            "Run: python 01_download.py\n"
            "Manual: statbel.fgov.be/en/open-data/statistical-sectors-2024\n"
            "File:   sh_statbel_statistical_sectors_31370_20240101.sqlite.zip"
        )

    print(f"  Loading: {sqlite_path.name}")
    gdf = gpd.read_file(sqlite_path)
    print(f"  Columns: {list(gdf.columns)}")
    print(f"  Total sectors (Belgium): {len(gdf):,}")

    if "CD_MUNTY_REFNIS" not in gdf.columns:
        raise ValueError(
            f"Expected column 'CD_MUNTY_REFNIS' not found.\n"
            f"Available columns: {list(gdf.columns)}\n"
            "Update this script with the correct column name."
        )

    gdf["_refnis_int"] = pd.to_numeric(gdf["CD_MUNTY_REFNIS"], errors="coerce").astype("Int64")
    brussels = gdf[gdf["_refnis_int"].isin(BRUSSELS_REFNIS)].drop(columns=["_refnis_int"]).copy()
    print(f"  Brussels sectors: {len(brussels):,}  (expected 724)")

    if brussels.crs is None:
        brussels = brussels.set_crs(CRS_LAMBERT)
    if str(brussels.crs).upper() != CRS_LAMBERT.upper():
        brussels = brussels.to_crs(CRS_LAMBERT)

    return brussels.to_crs(CRS_WGS84)


# ---------------------------------------------------------------------------
# Population join
# ---------------------------------------------------------------------------

def _read_tabular(path: Path) -> pd.DataFrame | None:
    """Read XLSX, CSV, or TXT with auto-separator detection."""
    if path.suffix == ".xlsx":
        return pd.read_excel(path)
    for sep in ("|", ";", ",", "\t"):
        try:
            df = pd.read_csv(path, sep=sep, encoding="latin-1", low_memory=False)
            if len(df.columns) > 1:
                return df
        except Exception:
            continue
    return None


def _find_pop_file(directory: Path) -> Path | None:
    for pattern in ("OPENDATA_SECTOREN*.[xX][lL][sS][xX]",
                    "OPENDATA_SECTOREN*.[cC][sS][vV]",
                    "OPENDATA_SECTOREN*.[tT][xX][tT]"):
        hits = list(directory.glob(pattern))
        if hits:
            return hits[0]
    return None


def join_population(sectors: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    pop_file = _find_pop_file(DATA_RAW)

    if pop_file is None:
        zip_path = DATA_RAW / "statbel_population.zip"
        if zip_path.exists():
            print(f"  Extracting {zip_path.name} …")
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(DATA_RAW)
            pop_file = _find_pop_file(DATA_RAW)

    if pop_file is None:
        print("  ⚠  Population file not found — 'population' column will be null")
        print("     Run: python 01_download.py  or download OPENDATA_SECTOREN_2024.zip manually")
        sectors = sectors.copy()
        sectors["population"] = None
        return sectors

    print(f"  Loading: {pop_file.name}")
    df = _read_tabular(pop_file)
    if df is None:
        print("  ⚠  Could not parse population file — 'population' column will be null")
        sectors = sectors.copy()
        sectors["population"] = None
        return sectors

    df.columns = [c.strip().upper() for c in df.columns]
    print(f"  Columns: {list(df.columns)}")

    # Locate join key (sector code)
    join_key = next(
        (c for c in ["CD_SECTOR", "CDSECTOR", "SECTOR_CD", "SECTOR"] if c in df.columns),
        None,
    )
    if join_key is None:
        print(f"  ⚠  Sector join key not found in {list(df.columns)}")
        sectors["population"] = None
        return sectors

    # Locate population column
    pop_col = next(
        (c for c in ["TOTAL", "MS_POP_TOT", "POPULATION", "POP_TOTAL", "TOTAAL", "POP"] if c in df.columns),
        None,
    )
    if pop_col is None:
        numeric = df.select_dtypes("number").columns.tolist()
        if numeric:
            pop_col = numeric[0]
            print(f"  Using '{pop_col}' as population estimate (verify this is correct)")
        else:
            print(f"  ⚠  No numeric population column found in {list(df.columns)}")
            sectors["population"] = None
            return sectors

    pop = (
        df[[join_key, pop_col]]
        .rename(columns={join_key: "CD_SECTOR", pop_col: "population"})
        .assign(CD_SECTOR=lambda x: x["CD_SECTOR"].astype(str).str.strip())
    )

    out = sectors.merge(pop, on="CD_SECTOR", how="left")
    matched = out["population"].notna().sum()
    print(f"  Joined: {matched}/{len(out)} sectors have population data")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    if OUT.exists():
        print(f"  ✓ {OUT.name} already exists — delete to regenerate")
        _print_stats()
        return

    print("─── Loading sector boundaries ───")
    sectors = load_sectors()

    print("\n─── Joining population ───")
    sectors = join_population(sectors)

    # Standardise output schema
    col_map = {
        "CD_SECTOR":            "id",
        "TX_SECTOR_DESCR_FR":   "name_fr",
        "TX_SECTOR_DESCR_NL":   "name_nl",
        "CD_MUNTY_REFNIS":      "cd_munty_refnis",
        "MS_AREA_HA":           "area_ha",
        "population":           "population",
    }
    present = {k: v for k, v in col_map.items() if k in sectors.columns}
    sectors = sectors.rename(columns=present)

    # Ensure 'id' exists
    if "id" not in sectors.columns:
        raise RuntimeError(
            "Column 'CD_SECTOR' not found in Statbel file.\n"
            f"Available: {list(sectors.columns)}\n"
            "Update col_map in this script."
        )

    # Centroid (computed in projected CRS, stored as WGS84 lon/lat)
    sectors_proj = sectors.to_crs(CRS_LAMBERT)
    centroids = sectors_proj.geometry.centroid.to_crs(CRS_WGS84)
    sectors["centroid_lon"] = centroids.x.round(6)
    sectors["centroid_lat"] = centroids.y.round(6)

    # Keep only documented columns + geometry
    keep = [c for c in ["id", "name_fr", "name_nl", "cd_munty_refnis",
                        "area_ha", "population", "centroid_lon", "centroid_lat"] if c in sectors.columns]
    sectors = sectors[keep + ["geometry"]]

    print(f"\n─── Saving {OUT.name} ───")
    sectors.to_file(OUT, driver="GeoJSON")
    print(f"  ✓ {len(sectors)} sectors written")

    _print_stats()
    print("\nNext: python 03_pois.py")


def _print_stats() -> None:
    sectors = gpd.read_file(OUT)
    print(f"\n  Sectors: {len(sectors)}")
    if "population" in sectors.columns and sectors["population"].notna().any():
        print(f"  Population total: {sectors['population'].sum():,.0f}")
        print(f"  Population range: {sectors['population'].min():.0f} – {sectors['population'].max():.0f}")
    if "area_ha" in sectors.columns:
        print(f"  Area range: {sectors['area_ha'].min():.1f} – {sectors['area_ha'].max():.1f} ha")
    n_communes = sectors["cd_munty_refnis"].nunique() if "cd_munty_refnis" in sectors.columns else "?"
    print(f"  Communes: {n_communes}  (expected 19)")


if __name__ == "__main__":
    main()
