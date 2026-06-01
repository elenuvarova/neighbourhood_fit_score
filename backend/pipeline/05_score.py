"""
Week 2 — Scoring Engine

For each of 724 Brussels sectors × 3 scenarios (family, senior, remote):
  1. Walk-time routing from sector centroid via the osmnx walk graph
  2. Plateau+Gaussian decay per category
  3. Nearest / abundance aggregation
  4. Frequency-weighted transit sub-score
  5. Weighted composite score (0–1 normalised)
  6. Hazen percentile rank across all Brussels sectors

Outputs:
  data/processed/scores.csv       — 2 172 rows: sector_id, scenario, score, percentile, breakdown
  data/processed/scores_wide.csv  — 724 rows: sector_id × (score, percentile) per scenario

Run:
  cd backend/pipeline
  python 05_score.py

Prerequisites:
  python 02_sectors.py  →  sectors.geojson
  python 03_pois.py     →  pois_all.geojson, transit_stops.geojson
  python 04_graph.py    →  brussels_walk.graphml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CITY_CONFIG, CRS_LAMBERT, CRS_WGS84,
    DATA_PROCESSED,
    DECAY_PARAMS, SCENARIO_WEIGHTS,
)

import argparse as _ap
_p = _ap.ArgumentParser(); _p.add_argument("--city", default="brussels", choices=list(CITY_CONFIG))
CITY = _p.parse_known_args()[0].city

if CITY == "brussels":
    SECTORS_PATH = DATA_PROCESSED / "sectors.geojson"
    POIS_PATH    = DATA_PROCESSED / "pois_all.geojson"
    TRANSIT_PATH = DATA_PROCESSED / "transit_stops.geojson"
    GRAPH_PATH   = DATA_PROCESSED / "brussels_walk.graphml"
    OUT_SCORES   = DATA_PROCESSED / "scores.csv"
    OUT_WIDE     = DATA_PROCESSED / "scores_wide.csv"
else:
    _city_dir    = DATA_PROCESSED / CITY
    SECTORS_PATH = _city_dir / "sectors.geojson"
    POIS_PATH    = _city_dir / "pois_all.geojson"
    TRANSIT_PATH = _city_dir / "transit_stops.geojson"
    GRAPH_PATH   = _city_dir / f"{CITY}_walk.graphml"
    OUT_SCORES   = _city_dir / "scores.csv"
    OUT_WIDE     = _city_dir / "scores_wide.csv"

# 12 departures in the 2-hour peak window (7–9 h) = every 10 min = "good" service
TRANSIT_FREQ_BASELINE = 12


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

def decay_vec(t_arr: np.ndarray, t_p: int, t_max: int) -> np.ndarray:
    """Vectorised plateau+Gaussian decay.  Returns array in [0, 1].

    f(t) = 1              if t ≤ t_p
           exp(-β(t-t_p)²) if t_p < t < t_max   (β = 4.605 / (t_max-t_p)²)
           0              if t ≥ t_max
    """
    out = np.zeros(len(t_arr), dtype=float)
    plateau  = t_arr <= t_p
    gaussian = (t_arr > t_p) & (t_arr < t_max)
    out[plateau] = 1.0
    if gaussian.any():
        beta = 4.605 / (t_max - t_p) ** 2
        out[gaussian] = np.exp(-beta * (t_arr[gaussian] - t_p) ** 2)
    return out


# ---------------------------------------------------------------------------
# Category & transit scoring
# ---------------------------------------------------------------------------

def _lookup_times(dist_dict: dict, nodes: np.ndarray) -> np.ndarray:
    """Travel times in seconds for each node; inf when unreachable."""
    return np.fromiter(
        (dist_dict.get(int(n), float("inf")) for n in nodes),
        dtype=float,
        count=len(nodes),
    )


def score_category(
    dist_dict: dict, nodes: np.ndarray, t_p: int, t_max: int, rule: str
) -> float:
    t_s = _lookup_times(dist_dict, nodes)
    reachable = t_s < t_max * 60
    if not reachable.any():
        return 0.0
    d = decay_vec(t_s[reachable] / 60.0, t_p, t_max)
    return float(d.max() if rule == "nearest" else min(d.sum(), 1.0))


def score_transit(
    dist_dict: dict,
    nodes: np.ndarray,
    freq_peak: np.ndarray,
    t_p: int,
    t_max: int,
) -> float:
    """Nearest-rule transit score weighted by stop frequency."""
    t_s = _lookup_times(dist_dict, nodes)
    reachable = t_s < t_max * 60
    if not reachable.any():
        return 0.0
    t_min = t_s[reachable] / 60.0
    d  = decay_vec(t_min, t_p, t_max)
    ff = np.minimum(freq_peak[reachable] / TRANSIT_FREQ_BASELINE, 1.0)
    # freq boosts 40–100 % of decay score; a stop with zero frequency still scores 0.4×decay
    return float((d * (0.4 + 0.6 * ff)).max())


# ---------------------------------------------------------------------------
# Composite sector score
# ---------------------------------------------------------------------------

def compute_score(
    dist_dict: dict,
    pois_by_cat: dict[str, np.ndarray],
    transit_nodes: np.ndarray | None,
    transit_freq: np.ndarray | None,
    scenario: str,
) -> tuple[float, dict[str, float]]:
    weights = SCENARIO_WEIGHTS[scenario]
    sub: dict[str, float] = {}

    for cat, _w in weights.items():
        t_p, t_max, rule = DECAY_PARAMS.get(cat, (5, 20, "nearest"))

        if cat == "transit":
            sub[cat] = (
                score_transit(dist_dict, transit_nodes, transit_freq, t_p, t_max)
                if transit_nodes is not None else 0.0
            )
        else:
            nodes = pois_by_cat.get(cat)
            sub[cat] = (
                score_category(dist_dict, nodes, t_p, t_max, rule) if nodes is not None else 0.0
            )

    total_w = sum(weights.values())
    raw = sum(sub[cat] * w for cat, w in weights.items())
    return round(raw / total_w, 6) if total_w else 0.0, sub


# ---------------------------------------------------------------------------
# Hazen percentile
# ---------------------------------------------------------------------------

def hazen_pct(series: pd.Series) -> pd.Series:
    """Hazen plotting position: (rank - 0.5) / n × 100."""
    n = len(series)
    return (((series.rank(method="average") - 0.5) / n) * 100).round(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if OUT_SCORES.exists():
        print(f"✓ {OUT_SCORES.name} already exists — delete to regenerate")
        return

    for p in [SECTORS_PATH, POIS_PATH, GRAPH_PATH]:
        if not p.exists():
            raise FileNotFoundError(f"Missing {p.name} — run steps 02–04 first")

    # ── Load ──────────────────────────────────────────────────────────────
    print("─── Loading data ───")
    sectors = gpd.read_file(SECTORS_PATH).reset_index(drop=True)
    pois    = gpd.read_file(POIS_PATH)
    transit = gpd.read_file(TRANSIT_PATH) if TRANSIT_PATH.exists() else None
    n_transit = len(transit) if transit is not None else 0
    print(f"  {len(sectors)} sectors  |  {len(pois):,} POIs  |  {n_transit:,} transit stops")
    print("  Loading walk graph (may take ~20 s) …")
    G = ox.load_graphml(GRAPH_PATH)
    # GraphML stores all attributes as strings — cast travel_time weights to float
    for _, _, data in G.edges(data=True):
        for key in ("travel_time", "travel_time_senior", "length"):
            if key in data and isinstance(data[key], str):
                data[key] = float(data[key])
    print(f"  Graph: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    # ── Snap to graph nodes ───────────────────────────────────────────────
    print("\n─── Snapping to graph nodes ───")

    # Sector centroids: compute in Lambert (metric) then reproject to WGS84 for snap
    sector_cents = sectors.to_crs(CRS_LAMBERT).geometry.centroid.to_crs(CRS_WGS84)
    sector_gnodes = ox.distance.nearest_nodes(G, X=sector_cents.x.values, Y=sector_cents.y.values)

    # POIs are already Point geometries in WGS84 (after 03_pois.py)
    pois_wgs = pois.to_crs(CRS_WGS84)
    poi_gnodes = ox.distance.nearest_nodes(
        G, X=pois_wgs.geometry.x.values, Y=pois_wgs.geometry.y.values
    )
    pois = pois.assign(graph_node=poi_gnodes)
    pois_by_cat: dict[str, np.ndarray] = {
        cat: grp["graph_node"].values for cat, grp in pois.groupby("category")
    }

    transit_nodes: np.ndarray | None = None
    transit_freq:  np.ndarray | None = None
    if transit is not None:
        t_wgs = transit.to_crs(CRS_WGS84)
        t_gnodes = ox.distance.nearest_nodes(
            G, X=t_wgs.geometry.x.values, Y=t_wgs.geometry.y.values
        )
        transit_nodes = np.asarray(t_gnodes)
        transit_freq  = transit["freq_peak"].fillna(0).values.astype(float)

    print(f"  Snapped: {len(sector_gnodes)} sector centroids, "
          f"{len(poi_gnodes):,} POIs, "
          f"{len(transit_nodes) if transit_nodes is not None else 0:,} transit stops")

    # ── Score each sector ─────────────────────────────────────────────────
    print(f"\n─── Scoring {len(sectors)} sectors × 3 scenarios ───")
    print("  Running Dijkstra (2 passes/sector, ~5–10 min) …")

    rows = []
    for idx, row in sectors.iterrows():
        sector_id = row["id"]
        src = int(sector_gnodes[idx])

        if (idx + 1) % 100 == 0 or (idx + 1) == len(sectors):
            print(f"  {idx + 1}/{len(sectors)} …", end="\r", flush=True)

        # One Dijkstra per walk-speed variant; family + remote share default speed
        dist_default = nx.single_source_dijkstra_path_length(
            G, src, weight="travel_time", cutoff=1800
        )
        dist_senior = nx.single_source_dijkstra_path_length(
            G, src, weight="travel_time_senior", cutoff=1800
        )

        for scenario, dist in (
            ("family",  dist_default),
            ("remote",  dist_default),
            ("senior",  dist_senior),
        ):
            score, sub = compute_score(dist, pois_by_cat, transit_nodes, transit_freq, scenario)
            rows.append({
                "sector_id": sector_id,
                "scenario":  scenario,
                "score":     score,
                "breakdown": json.dumps({k: round(v, 4) for k, v in sub.items()}),
            })

    print(f"\n  ✓ {len(rows)} sector-scenario scores computed")

    # ── Hazen percentiles ─────────────────────────────────────────────────
    df = pd.DataFrame(rows)
    df["percentile"] = df.groupby("scenario")["score"].transform(hazen_pct)

    # ── Write outputs ─────────────────────────────────────────────────────
    df[["sector_id", "scenario", "score", "percentile", "breakdown"]].to_csv(
        OUT_SCORES, index=False
    )
    print(f"  ✓ {OUT_SCORES.name}: {len(df)} rows")

    wide = df.pivot(index="sector_id", columns="scenario", values=["score", "percentile"])
    wide.columns = [f"{col[1]}_{col[0]}" for col in wide.columns]
    wide.reset_index().to_csv(OUT_WIDE, index=False)
    print(f"  ✓ {OUT_WIDE.name}: {len(wide)} rows")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n─── Score summary ───")
    for scen in ("family", "senior", "remote"):
        s = df[df["scenario"] == scen]["score"]
        print(f"  {scen:<8}  mean={s.mean():.3f}  σ={s.std():.3f}  "
              f"p5={s.quantile(0.05):.3f}  p95={s.quantile(0.95):.3f}")

    print("\nNext: python 06_api_seed.py  (Week 3 — Express endpoint seed)")


if __name__ == "__main__":
    main()
