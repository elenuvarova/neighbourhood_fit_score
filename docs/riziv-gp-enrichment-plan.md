# RIZIV-INAMI GP Enrichment Plan

> **Status:** research + plan only (not implemented). Produced by a multi-agent
> research pass, verified against the actual pipeline code and data.
> **Goal:** raise Brussels GP coverage in `pois_all.geojson` from **230 (OSM)**
> toward the real **~2,000**, to correct the systematic under-scoring of the
> **Senior** scenario (GP = 27/100 weight — the largest single weight).

**Verified against code/data before writing:** `pois_all.geojson` holds exactly
230 `category=gp` features (58 with `sector_id=null`); schema is
`{category, sector_id, name, name:fr, name:nl, area_m2}` in CRS84; `05_score.py`
reads only `pois_all.geojson` grouped by `category`; both `05` and `07`
short-circuit if their output CSV already exists.

---

## 1. Recommended data source

**RIZIV-INAMI Silverpages** — public healthcare-provider directory.
- URL: `https://webappsa.riziv-inami.fgov.be/silverpages/` (data via `GET /silverpages/Home/SearchHcw/`)
- **Why:** the only authoritative, region-complete source whose records **already
  carry WGS84 coordinates per work address** → ~2,000 real GPs with **zero
  geocoding** (no 2,000-call Nominatim run).
- **Licence is the weak point:** the UI is marked "©INAMI tous droits réservés,"
  not an open/bulk export. Technical path is high-confidence; **republish rights
  must be settled before shipping** (see §7). Licence-clean fallback in §6.

## 2. Data shape & isolating Brussels GPs

**Endpoint:** `GET https://webappsa.riziv-inami.fgov.be/silverpages/Home/SearchHcw/`
- Headers: `HX-Request: true` + Chrome-like `User-Agent` (plain curl → 403; use Python `requests`/`urllib`).
- Returns server-rendered HTML, one `<map-marker>` per registered work address.

**Per-marker fields:** `data-id` (16-digit NIHDI number; **last 3 digits = competence code**),
`data-latitude`/`data-longitude` (EPSG:4326), `data-title` (`LASTNAME, Firstname`),
`data-description` (HTML-entity-encoded, `</BR>`-separated: profession, qualification + code,
practice name, street, "postcode MUNICIPALITY").

**Query fields** (prefix `Form.`): `Form.Profession`, `Form.Qualification`, `Form.Location`,
`Form.Name`, `Form.NihdiNumber`, + lat/lng bbox. No antiforgery token for GET.

**Isolate GPs:** `Form.Profession=10` (Médecin) **AND** `Form.Qualification ∈ {003, 004}`
(accredited GP; 004≈84%, 003≈16% → ~99% of accredited GPs). Optionally add `005/006`
for GPs-in-training. Exclude `009` (plain doctor) and `010+` (specialists).
*Live-verified:* postcode 1000 + Qual=004 → 132 rows vs 1576 unfiltered médecins.

**Isolate Brussels:** loop `Form.Location` over the 19 BCR postcodes
(1000, 1020, 1030, 1040, 1050, 1060, 1070, 1080, 1081, 1082, 1083, 1090, 1120,
1130, 1140, 1150, 1160, 1170, 1180, 1190, 1200, 1210). Each postcode returns all
rows on one page (no pagination) → ~20–25 requests total. No NIS field needed —
sectors are assigned by point-in-polygon (§3e), same as OSM POIs.

## 3. Step-by-step integration

Keeps the **single-artifact contract**: everything lands back in `pois_all.geojson`
as `category='gp'` points, so `04`, `05`, `07` need **zero changes**.

1. **`config.py`** — add `DOWNLOAD_SOURCES["riziv_gp"] = (None, "riziv_gp.geojson")`
   (url=None → `RIZIV_GP_URL` env or manual drop). Optionally add `DEDUP_RADIUS_M = 45`.
   Later, drop `'gp'` from `LOW_CONFIDENCE_CATEGORIES` once coverage is proven.
2. **`01_download.py`** — add a `riziv_gp` branch to `_print_manual_instructions()`.
3. **NEW `backend/pipeline/03b_gp_riziv.py`** (runs **after** `03_pois.py`):
   - **(a) Fetch/parse:** loop the ~22 BCR postcodes with `Profession=10`,
     `Qualification∈{003,004}`; cache raw HTML under `DATA_RAW/riziv_cache/`
     (idempotent, respectful). Parse `<map-marker>` → rows.
   - **(b) Geometry:** `gpd.points_from_xy(lon, lat, crs=CRS_WGS84)` — **no geocoding**.
     Drop markerless providers.
   - **(c) Dedup:** see §4.
   - **(d) Sector assignment:** reuse `03_pois.py`'s `join_to_sectors()` — reproject to
     EPSG:31370, `sjoin(predicate="within")`, rename `id→sector_id`, back to WGS84.
     Tolerate `sector_id=null` (matches the 58 existing null OSM GPs).
   - **(e) Append + rewrite in place:** normalise to
     `[category, sector_id, name, name:fr, name:nl, area_m2, geometry]`
     (`category='gp'`, `name=data-title`, `area_m2=0.0`); add `source='riziv'` as the
     already-enriched guard; concat onto existing features, rewrite `pois_all.geojson`.
     Skip if any feature already has `source='riziv'`.
   - **(f)** Refresh `sector_amenities.csv` via the same `build_amenity_table()` logic.
4. **Run order** (delete stale outputs so short-circuits don't block):
   `01 → 02 → 03 → 03b → 04 → (rm scores.csv scores_wide.csv) → 05 → (rm improvements.csv) → 07 → 06/08`.

**Unchanged:** `02`, `04`, `05`, `06`, `07`, `08`. No new deps (`geopandas`, `shapely`,
`pyproj`, `requests` already present; `geopy` only if a geocoding fallback is built).

## 4. Deduplication — MERGE (spatial), not replace

1. Split `pois_all.geojson` into `osm_gp` (category=='gp') and the rest.
2. Reproject `osm_gp` + `riziv_gp` to EPSG:31370 (metric).
3. For each RIZIV GP, find nearest OSM GP; **drop if ≤ `DEDUP_RADIUS_M = 45 m`**.
4. **Append only surviving RIZIV GPs.** Result = 230 OSM + non-duplicate RIZIV.
5. **RIZIV-vs-RIZIV collapse (flagged, default ON):** many GPs share identical coords
   (group practices / maisons médicales). For the `"nearest"` rule, count is irrelevant —
   snap to a ~15–20 m grid. Keep behind a flag because the future E2SFCA
   `CAPACITY_CATEGORIES` step (`gp` is in it) **will** need per-doctor counts.

**Why merge over replace:** strictly additive + idempotent (a bad parse can never delete
the 230 verified OSM GPs); OSM GPs keep real names/tags for the map; safe to re-run.

## 5. Expected impact on Senior scores

Grounded in the actual `scores.csv` / `pois_all.geojson`:
- **Current:** Senior GP sub-score mean **0.454**; **24% of sectors (174/724) score 0 on GP**
  at senior walk speed — much of the Senior bottom tail is a **missing-data artifact**, not a real GP desert.
- **230 → ~2,000 is ~9× density** → nearest-GP distance ~÷3.
- **Conservative model:** Senior GP sub-score mean 0.454 → ~0.95; gp=0 share 24% → ~0%;
  **Senior composite mean +0.13–0.15** (0.642 → ~0.775).
- **Re-ranking:** ~46% of sectors move >10 percentile points; previously-gp=0 sectors gain up
  to ~+40 pts; sectors that ranked high *only* on a rare OSM GP advantage drop ~16 pts relative.
  GP compresses as a Senior differentiator; ranking re-sorts toward transit/supermarket/hospital.
- **Caveat:** exact numbers require a clean `05_score.py` re-run on the enriched file.

## 6. Fallbacks (best-first)

1. **opendata.brussels `medecins-generalistes-vbx`** (CSV/GeoJSON/API, geocoded, **CC BY**) —
   licence-clean + geocoded, ingestible by the `03b` parser. **Gap:** City-of-Brussels commune
   only (~100 records, 1 of 19 communes). Use as a licence-safe booster + QA for Silverpages counts.
2. **OSM tagging improvement** (ODbL, our licence) — `healthsites.io`/HDX HOT are OSM-derived and
   add nothing over `03_pois.py`; real upside is contributing missing GPs back to OSM (slow).
3. **Google Places API** — paid; ToS forbids storing coords in a shipped static dataset. Count-validation only.
4. **RIZIV static PDF lists** — authoritative but PDF + not geocoded → reintroduces the 2,000-address Nominatim path. Last resort.
5. **CoBRHA** (gold registry) — authenticated/GDPR-gated eHealth XML; effectively closed.
6. **doctoranytime/Qare scraping** — highest licence/GDPR risk; avoid.

## 7. Open questions / risks

- **Licence (highest risk):** Silverpages is "tous droits réservés." Resolve whether scraping +
  republishing coordinates is permitted, or fall back to the CC BY opendata.brussels layer for
  anything published. **Decide before shipping the enriched dataset publicly.**
- **Endpoint stability:** the htmx `SearchHcw` partial is undocumented/unversioned. Cache raw
  responses, pin a parser, monitor per-postcode row counts.
- **Markers > rows / shared coords:** one doctor can have multiple work addresses; many share
  coords. `"nearest"` unaffected; the E2SFCA capacity step needs per-doctor counts → grid-collapse flag.
- **Markerless providers:** GPs with no registered work address have no coords and are dropped →
  RIZIV gives *practice* coverage, ~2,000 is a target not a guarantee.
- **Competence-code completeness:** only postcode 1000 was live-verified; spot-check a few more before a full run.
- **Validation gate:** after the run, confirm total GP count lands in 1,500–2,200 and the gp=0 share
  collapses; far fewer → treat as a parse/filter regression, not ground truth.

**Confidence:** high on pipeline integration + impact direction (both code-verified) and on the
technical extraction path; **medium on licence/republish rights** — the one item to settle before shipping.

**Touch-points:** `backend/pipeline/{config.py, 01_download.py, 03_pois.py, 05_score.py, 07_improvements.py}`;
new file `backend/pipeline/03b_gp_riziv.py`; artifact rewritten in place
`backend/pipeline/data/processed/pois_all.geojson`.
