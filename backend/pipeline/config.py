"""Shared configuration for the Neighbourhood Fit Score pipeline."""
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PIPELINE_DIR = Path(__file__).parent
DATA_RAW = PIPELINE_DIR / "data" / "raw"
DATA_PROCESSED = PIPELINE_DIR / "data" / "processed"

# ---------------------------------------------------------------------------
# Brussels geography
# ---------------------------------------------------------------------------
# All 19 Brussels communes (CD_MUNTY_REFNIS range)
BRUSSELS_REFNIS = set(range(21001, 21020))

# WGS84 bounding box: Brussels + ~2 km buffer
# (lon_min, lat_min, lon_max, lat_max)
BRUSSELS_BBOX = (4.23, 50.77, 4.50, 50.93)

# CRS identifiers
CRS_LAMBERT = "EPSG:31370"   # Belgian Lambert 72 — native Statbel CRS
CRS_WGS84 = "EPSG:4326"

# Walk speeds (m/s)
WALK_SPEED_DEFAULT = 1.33    # 4.8 km/h — standard adult
WALK_SPEED_SENIOR = 1.00     # 3.6 km/h — WHO/ANGSt senior reference

# Park area gate for scoring (hectares)
PARK_MIN_HA = 0.5

# ---------------------------------------------------------------------------
# Download sources
# NOTE: Statbel URLs are best-guess — verify at statbel.fgov.be/en/open-data
# Set STIB_GTFS_URL env var or download GTFS manually (see 01_download.py).
# ---------------------------------------------------------------------------
DOWNLOAD_SOURCES: dict[str, tuple[str | None, str]] = {
    # (url_or_None, local_filename)
    "osm_pbf": (
        "https://download.geofabrik.de/europe/belgium-latest.osm.pbf",
        "belgium-latest.osm.pbf",
    ),
    "statbel_sectors": (
        "https://statbel.fgov.be/sites/default/files/files/opendata/"
        "statistische%20sectoren/sh_statbel_statistical_sectors_31370_20240101.sqlite.zip",
        "statbel_sectors.zip",
    ),
    "statbel_population": (
        "https://statbel.fgov.be/sites/default/files/files/opendata/"
        "bevolking%20naar%20wijk/OPENDATA_SECTOREN_2024.zip",
        "statbel_population.zip",
    ),
    "stib_gtfs": (
        None,   # set STIB_GTFS_URL env var, or download manually
        "stib_gtfs.zip",
    ),
}

# ---------------------------------------------------------------------------
# OSM tag → scoring category mapping
# Each category maps to a list of {tag_key: tag_value} dicts (OR logic).
# ---------------------------------------------------------------------------
OSM_CATEGORY_TAGS: dict[str, list[dict[str, str]]] = {
    "school":       [{"amenity": "school"}, {"building": "school"}],
    "childcare":    [{"amenity": "kindergarten"}, {"amenity": "childcare"}],
    "playground":   [{"leisure": "playground"}],
    "park":         [
        {"leisure": "park"}, {"leisure": "garden"}, {"leisure": "nature_reserve"},
        {"landuse": "forest"}, {"natural": "wood"},
    ],
    "library":      [{"amenity": "library"}],
    "pharmacy":     [{"amenity": "pharmacy"}, {"healthcare": "pharmacy"}],
    "hospital":     [
        {"amenity": "hospital"}, {"amenity": "clinic"},
        {"healthcare": "hospital"}, {"healthcare": "clinic"},
    ],
    "gp":           [{"amenity": "doctors"}, {"healthcare": "doctor"}],
    "supermarket":  [{"shop": "supermarket"}],
    "convenience":  [{"shop": "convenience"}, {"shop": "greengrocer"}, {"shop": "bakery"}],
    "cafe":         [{"amenity": "cafe"}],
    "restaurant":   [{"amenity": "restaurant"}, {"amenity": "fast_food"}],
    "coworking":    [{"amenity": "coworking_space"}, {"office": "coworking"}],
    "bench":        [{"amenity": "bench"}, {"leisure": "picnic_table"}],
    "sport":        [
        {"leisure": "sports_centre"}, {"leisure": "fitness_centre"},
        {"leisure": "pitch"}, {"leisure": "swimming_pool"},
    ],
    "community":    [{"amenity": "community_centre"}, {"amenity": "social_centre"}],
    "veterinary":   [{"amenity": "veterinary"}, {"shop": "pet"}],
    "dog_park":     [{"leisure": "dog_park"}],
}


def make_pyrosm_filter(include_keys: set[str] | None = None) -> dict[str, list[str]]:
    """Build a pyrosm custom_filter from OSM_CATEGORY_TAGS (OR across all values per key)."""
    from collections import defaultdict

    result: dict[str, set[str]] = defaultdict(set)
    for cat, tag_list in OSM_CATEGORY_TAGS.items():
        if include_keys and cat not in include_keys:
            continue
        for tag_dict in tag_list:
            for k, v in tag_dict.items():
                result[k].add(v)
    return {k: sorted(v) for k, v in result.items()}


# ---------------------------------------------------------------------------
# Decay thresholds (minutes) per category: (t_p, t_max, rule)
# Source: BUILD_SPEC §2.2 — Walk Score/15-min-city/WHO-ANGSt anchors
# ---------------------------------------------------------------------------
DECAY_PARAMS: dict[str, tuple[int, int, str]] = {
    "pharmacy":     (5,  15, "nearest"),
    "gp":           (5,  15, "nearest"),
    "school":       (5,  15, "nearest"),
    "childcare":    (5,  15, "nearest"),
    "playground":   (3,  10, "nearest"),
    "park":         (5,  15, "nearest"),
    "supermarket":  (5,  15, "nearest"),
    "convenience":  (3,  12, "abundance"),
    "transit":      (4,  12, "nearest"),
    "cafe":         (5,  15, "abundance"),
    "restaurant":   (5,  15, "abundance"),
    "library":      (7,  20, "nearest"),
    "sport":        (7,  20, "nearest"),
    "hospital":     (0,  30, "nearest"),
}

# ---------------------------------------------------------------------------
# Scenario weights (raw points; normalised to 1.0 in scoring engine)
# Source: BUILD_SPEC §2.3 — IMD domain structure
# ---------------------------------------------------------------------------
SCENARIO_WEIGHTS: dict[str, dict[str, float]] = {
    "family": {
        "school": 15.0, "childcare": 10.0,
        "supermarket": 10.0, "pharmacy": 8.0, "convenience": 2.0,
        "gp": 9.0, "hospital": 3.0,
        "park": 15.0, "playground": 8.0,
        "transit": 10.0,
        "cafe": 2.0, "restaurant": 2.0, "library": 3.0, "sport": 3.0,
    },
    "senior": {
        "supermarket": 12.0, "convenience": 6.0,
        "gp": 27.0, "hospital": 8.0,
        "park": 10.0, "library": 5.0,
        "transit": 17.0,
        "cafe": 5.0, "restaurant": 5.0, "sport": 5.0,
    },
    "remote": {
        "supermarket": 10.0, "pharmacy": 7.0, "convenience": 3.0,
        "gp": 5.0,
        "park": 14.0, "playground": 4.0,
        "transit": 13.0,
        "cafe": 8.0, "library": 8.0, "restaurant": 4.0,
        "sport": 4.0, "coworking": 10.0,
    },
}

# Categories with known low OSM coverage in Brussels — flag in outputs
LOW_CONFIDENCE_CATEGORIES = {"coworking", "bench", "gp", "dog_park"}

# E2SFCA capacity-service categories (Step 2 scoring, Week 5)
CAPACITY_CATEGORIES = {"gp", "pharmacy", "childcare", "school", "supermarket"}
