"""
Build the OSMnx walking graph for Brussels + 2 km buffer.
Adds travel_time (default adult 1.33 m/s) and travel_time_senior (1.0 m/s) to every edge.

Output:
  data/processed/brussels_walk.graphml   (~50-150 MB, reusable)
  data/processed/graph_stats.json

Run:
  cd backend/pipeline
  python 04_graph.py

Notes:
  - First run queries Overpass API (~2-5 min); cached in data/raw/osmnx_cache/.
  - Requires sectors.geojson from step 02.
  - The +2 km buffer reduces edge effects when scoring border sectors.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pyproj
from shapely.ops import transform

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CRS_LAMBERT, CRS_WGS84, DATA_PROCESSED, DATA_RAW,
    WALK_SPEED_DEFAULT, WALK_SPEED_SENIOR,
)

SECTORS_PATH = DATA_PROCESSED / "sectors.geojson"
GRAPH_PATH = DATA_PROCESSED / "brussels_walk.graphml"
STATS_PATH = DATA_PROCESSED / "graph_stats.json"


def build_brussels_polygon(buffer_m: float = 2000.0):
    """
    Load sector boundaries → union → buffer in Lambert (metres) → WGS84.
    Returns a Shapely polygon suitable for osmnx.graph_from_polygon().
    """
    if not SECTORS_PATH.exists():
        raise FileNotFoundError(
            f"{SECTORS_PATH} not found.\n"
            "Run: python 02_sectors.py first."
        )

    print("  Loading sector boundaries …")
    sectors = gpd.read_file(SECTORS_PATH).to_crs(CRS_LAMBERT)

    # geopandas ≥1.0: union_all(); older: unary_union
    try:
        brussels = sectors.union_all()
    except AttributeError:
        brussels = sectors.unary_union

    # Buffer 2 km in projected metres
    brussels_buffered = brussels.buffer(buffer_m)
    print(f"  Brussels polygon buffered +{buffer_m:.0f} m")

    # Project to WGS84 for osmnx
    proj = pyproj.Transformer.from_crs(CRS_LAMBERT, CRS_WGS84, always_xy=True)
    return transform(proj.transform, brussels_buffered)


def main() -> None:
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    if GRAPH_PATH.exists():
        print(f"  ✓ {GRAPH_PATH.name} already exists — delete to regenerate")
        if STATS_PATH.exists():
            stats = json.loads(STATS_PATH.read_text())
            print(f"  Nodes: {stats.get('nodes', '?'):,}  Edges: {stats.get('edges', '?'):,}")
        return

    # Configure osmnx caching
    cache_dir = DATA_RAW / "osmnx_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.log_console = False

    print("─── Building Brussels boundary ───")
    polygon_wgs84 = build_brussels_polygon(buffer_m=2000.0)

    print("\n─── Downloading walking network from Overpass API ───")
    print("  (first run: 2–5 min; subsequent runs use cache)")
    print("  Cache:", cache_dir)

    G = ox.graph_from_polygon(
        polygon_wgs84,
        network_type="walk",
        simplify=True,
        retain_all=False,
    )

    # Add travel_time to every edge
    print("\n─── Adding travel_time attributes ───")
    for u, v, k, d in G.edges(keys=True, data=True):
        length = d.get("length", 0)
        d["travel_time"]        = round(length / WALK_SPEED_DEFAULT, 1)
        d["travel_time_senior"] = round(length / WALK_SPEED_SENIOR,  1)

    # Stats
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()
    print(f"  Nodes: {n_nodes:,}")
    print(f"  Edges: {n_edges:,}")

    edge_lengths = [d.get("length", 0) for _, _, d in G.edges(data=True)]
    avg_len = sum(edge_lengths) / max(len(edge_lengths), 1)
    print(f"  Avg edge length: {avg_len:.1f} m")

    print(f"\n─── Saving {GRAPH_PATH.name} ───")
    ox.save_graphml(G, filepath=str(GRAPH_PATH))
    size_mb = GRAPH_PATH.stat().st_size / 1_048_576
    print(f"  ✓ {size_mb:.1f} MB")

    stats = {
        "nodes": n_nodes,
        "edges": n_edges,
        "avg_edge_length_m": round(avg_len, 1),
        "walk_speed_default": WALK_SPEED_DEFAULT,
        "walk_speed_senior": WALK_SPEED_SENIOR,
        "buffer_m": 2000,
        "crs_source": CRS_LAMBERT,
    }
    STATS_PATH.write_text(json.dumps(stats, indent=2))
    print(f"  ✓ Stats written to {STATS_PATH.name}")

    print("\nWeek 1 complete!")
    print("Next: python 05_score.py  (Week 2 — Family scenario scoring)")


if __name__ == "__main__":
    main()
