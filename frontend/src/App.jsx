import { useState, useEffect, useRef, useCallback } from "react"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"

const API = import.meta.env.VITE_API_URL ?? ""
const BRUSSELS_CENTER = [4.352, 50.846]
const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty"

const SCENARIOS = [
  { id: "family", label: "Family" },
  { id: "senior", label: "Senior" },
  { id: "remote", label: "Remote Work" },
]

// Brussels NIS commune codes
const COMMUNES = {
  "21001": "Anderlecht",       "21002": "Auderghem",
  "21003": "Berchem-Ste-Agathe","21004": "Brussels",
  "21005": "Etterbeek",        "21006": "Evere",
  "21007": "Forest",           "21008": "Ganshoren",
  "21009": "Ixelles",          "21010": "Jette",
  "21011": "Koekelberg",       "21012": "Molenbeek",
  "21013": "Saint-Gilles",     "21014": "Saint-Josse",
  "21015": "Schaerbeek",       "21016": "Uccle",
  "21017": "Watermael-Boitsfort","21018": "Woluwe-St-Lambert",
  "21019": "Woluwe-St-Pierre",
}

const CATEGORY_LABELS = {
  school: "Schools",          childcare: "Childcare",
  playground: "Playgrounds",  park: "Parks",
  pharmacy: "Pharmacies",     gp: "GPs / Doctors",
  hospital: "Hospitals",      supermarket: "Supermarkets",
  convenience: "Local shops", transit: "Public transport",
  cafe: "Cafés",              restaurant: "Restaurants",
  coworking: "Coworking",     library: "Libraries",
  sport: "Sports",
}

// POI categories available as map layer toggles
const MAP_LAYERS = [
  { cat: "school",   color: "#60a5fa", label: "Schools" },
  { cat: "park",     color: "#4ade80", label: "Parks" },
  { cat: "pharmacy", color: "#f87171", label: "Pharmacies" },
  { cat: "transit",  color: "#a78bfa", label: "Transit" },
  { cat: "cafe",     color: "#fb923c", label: "Cafés" },
  { cat: "sport",    color: "#facc15", label: "Sport" },
]

const SCENARIO_WEIGHTS = {
  family: {
    school: 15, childcare: 10, supermarket: 10, pharmacy: 8, convenience: 2,
    gp: 9, hospital: 3, park: 15, playground: 8, transit: 10,
    cafe: 2, restaurant: 2, library: 3, sport: 3,
  },
  senior: {
    supermarket: 12, convenience: 6, gp: 27, hospital: 8,
    park: 10, library: 5, transit: 17, cafe: 5, restaurant: 5, sport: 5,
  },
  remote: {
    supermarket: 10, pharmacy: 7, convenience: 3, gp: 5,
    park: 14, playground: 4, transit: 13, cafe: 8,
    library: 8, restaurant: 4, sport: 4, coworking: 10,
  },
}

function scoreColor(score) {
  if (score >= 70) return "#4ade80"
  if (score >= 50) return "#facc15"
  if (score >= 30) return "#f97316"
  return "#f87171"
}

// ── Components ──────────────────────────────────────────────────────────────

function ScenarioTabs({ scenario, onChange }) {
  return (
    <div className="scenario-tabs">
      {SCENARIOS.map(s => (
        <button
          key={s.id}
          className={`tab-btn${scenario === s.id ? " active" : ""}`}
          onClick={() => onChange(s.id)}
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}

function ScoreRing({ score }) {
  const r = 44
  const circ = 2 * Math.PI * r
  const dash = (score / 100) * circ
  const color = scoreColor(score)
  return (
    <svg className="score-ring" width="110" height="110" viewBox="0 0 110 110">
      <circle cx="55" cy="55" r={r} fill="none" stroke="#242938" strokeWidth="9" />
      <circle
        cx="55" cy="55" r={r} fill="none"
        stroke={color} strokeWidth="9"
        strokeDasharray={`${dash} ${circ}`}
        strokeLinecap="round"
        transform="rotate(-90 55 55)"
        style={{ transition: "stroke-dasharray 0.7s ease" }}
      />
      <text x="55" y="61" textAnchor="middle" fill="#f8fafc" fontSize="26" fontWeight="700">
        {score}
      </text>
    </svg>
  )
}

function CategoryBars({ breakdown, scenario }) {
  const weights = SCENARIO_WEIGHTS[scenario] ?? {}
  const items = Object.entries(breakdown)
    .filter(([cat]) => cat in weights && cat in CATEGORY_LABELS)
    .map(([cat, raw]) => ({ cat, score: Math.round(raw * 100), weight: weights[cat] }))
    .sort((a, b) => b.weight - a.weight)

  return (
    <div className="category-bars">
      {items.map(({ cat, score }) => (
        <div key={cat} className="bar-row">
          <span className="bar-label">{CATEGORY_LABELS[cat]}</span>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{ width: `${score}%`, backgroundColor: scoreColor(score) }}
            />
          </div>
          <span className="bar-score">{score}</span>
        </div>
      ))}
    </div>
  )
}

function WhyPanel({ pros, cons }) {
  if (!pros?.length && !cons?.length) return null
  return (
    <div className="why-panel">
      {pros?.length > 0 && (
        <div>
          <p className="why-title pro">Strengths</p>
          <ul>
            {pros.map((p, i) => <li key={i} className="why-item pro">{p}</li>)}
          </ul>
        </div>
      )}
      {cons?.length > 0 && (
        <div>
          <p className="why-title con">Gaps</p>
          <ul>
            {cons.map((c, i) => <li key={i} className="why-item con">{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

function ImprovementsList({ improvements, onHighlight }) {
  if (!improvements?.length) return null
  return (
    <div className="improvements">
      <p className="section-title">How to improve</p>
      {improvements.map(imp => (
        <button
          key={imp.rank}
          className="imp-item"
          onClick={() => onHighlight?.(imp)}
        >
          <span className="imp-title">{imp.title}</span>
          <span className="imp-meta">
            {imp.from_score} → {imp.to_score}
            <span className="imp-gain">+{imp.score_delta}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

// ── POI layer toggles ───────────────────────────────────────────────────────

function MapLayerToggles({ sectorId, mapInst, mapReady }) {
  const [active, setActive] = useState(new Set())
  const cache = useRef({})

  useEffect(() => {
    const m = mapInst.current
    if (!m) return
    MAP_LAYERS.forEach(({ cat }) => {
      if (m.getLayer(`poi-${cat}`)) m.removeLayer(`poi-${cat}`)
      if (m.getSource(`poi-src-${cat}`)) m.removeSource(`poi-src-${cat}`)
    })
    setActive(new Set())
  }, [sectorId, mapInst])

  const toggle = async (cat, color) => {
    const m = mapInst.current
    if (!m || !mapReady) return
    const key = `${sectorId}_${cat}`
    const layerId = `poi-${cat}`
    const srcId = `poi-src-${cat}`

    if (active.has(cat)) {
      if (m.getLayer(layerId)) m.removeLayer(layerId)
      if (m.getSource(srcId)) m.removeSource(srcId)
      setActive(prev => { const s = new Set(prev); s.delete(cat); return s })
      return
    }

    let features = cache.current[key]
    if (!features) {
      try {
        const data = await fetch(
          `${API}/api/pois?sector_id=${sectorId}&categories=${cat}`
        ).then(r => r.json())
        features = (data.pois ?? []).map(p => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [p.lng, p.lat] },
          properties: { name: p.name },
        }))
        cache.current[key] = features
      } catch (_) { return }
    }

    if (!m.getSource(srcId)) {
      m.addSource(srcId, { type: "geojson", data: { type: "FeatureCollection", features } })
    }
    if (!m.getLayer(layerId)) {
      m.addLayer({
        id: layerId,
        type: "circle",
        source: srcId,
        paint: {
          "circle-radius": 5,
          "circle-color": color,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#fff",
          "circle-opacity": 0.88,
        },
      })
    }
    setActive(prev => new Set([...prev, cat]))
  }

  return (
    <div>
      <p className="section-title">Show on map</p>
      <div className="layer-toggles">
        {MAP_LAYERS.map(({ cat, color, label }) => (
          <button
            key={cat}
            className={`layer-btn${active.has(cat) ? " active" : ""}`}
            onClick={() => toggle(cat, color)}
          >
            <span className="layer-dot" style={{ background: color }} />
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}

function DisclosureFooter({ disclosure }) {
  if (!disclosure) return null
  return (
    <details className="disclosure">
      <summary>Data &amp; methodology</summary>
      <p>{disclosure.note}</p>
      <dl className="method-grid">
        <dt>Walk speed</dt>
        <dd>4.8 km/h (standard) · 3.6 km/h (senior)</dd>
        <dt>Decay</dt>
        <dd>Plateau + Gaussian — full score within t_p, zero at t_max</dd>
        <dt>Scenarios</dt>
        <dd>Weighted sub-scores → Hazen percentile across 724 sectors</dd>
        <dt>Limitations</dt>
        <dd>OSM completeness varies; private facilities not included; hours not modelled</dd>
      </dl>
      <p className="muted">Source: {disclosure.source} ({disclosure.data_date})</p>
    </details>
  )
}

// ── Main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [addressInput, setAddressInput]   = useState("")
  const [scenario, setScenario]           = useState("family")
  const [loading, setLoading]             = useState(false)
  const [error, setError]                 = useState(null)
  const [result, setResult]               = useState(null)
  const [mapReady, setMapReady]           = useState(false)
  const [sectorsGeo, setSectorsGeo]       = useState(null)
  const [highlightedImp, setHighlightedImp] = useState(null)

  const mapContainer  = useRef(null)
  const mapInst       = useRef(null)
  const geoCache      = useRef({})
  const isFirstRender = useRef(true)

  // ── Init map ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapContainer.current) return
    const m = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: BRUSSELS_CENTER,
      zoom: 11,
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right")
    m.on("load", () => {
      mapInst.current = m
      setMapReady(true)
    })
    return () => m.remove()
  }, [])

  // ── Load sectors GeoJSON ────────────────────────────────────────────────
  const loadSectorsGeo = useCallback(async (scen) => {
    if (geoCache.current[scen]) { setSectorsGeo(geoCache.current[scen]); return }
    try {
      const data = await fetch(`${API}/api/sectors.geojson?scenario=${scen}`).then(r => r.json())
      geoCache.current[scen] = data
      setSectorsGeo(data)
    } catch (_) {}
  }, [])

  useEffect(() => { loadSectorsGeo(scenario) }, [scenario, loadSectorsGeo])

  // ── Add / update sectors layer ──────────────────────────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m || !sectorsGeo) return

    if (m.getSource("sectors")) {
      m.getSource("sectors").setData(sectorsGeo)
      return
    }

    m.addSource("sectors", { type: "geojson", data: sectorsGeo })

    m.addLayer({
      id: "sectors-fill",
      type: "fill",
      source: "sectors",
      paint: {
        "fill-color": [
          "case",
          ["==", ["get", "score"], null], "#374151",
          ["interpolate", ["linear"], ["get", "score"],
            0, "#dc2626", 30, "#f97316", 50, "#facc15", 70, "#4ade80", 100, "#16a34a"],
        ],
        "fill-opacity": 0.55,
      },
    })

    m.addLayer({
      id: "sectors-outline",
      type: "line",
      source: "sectors",
      paint: { "line-color": "#ffffff", "line-width": 0.5, "line-opacity": 0.35 },
    })

    m.addLayer({
      id: "sector-selected",
      type: "line",
      source: "sectors",
      filter: ["==", ["get", "id"], ""],
      paint: { "line-color": "#ffffff", "line-width": 3, "line-opacity": 1 },
    })

    m.on("click", "sectors-fill", e => {
      const id = e.features?.[0]?.properties?.id
      if (id) fetchBySectorId(id)
    })
    m.on("mouseenter", "sectors-fill", () => { m.getCanvas().style.cursor = "pointer" })
    m.on("mouseleave", "sectors-fill", () => { m.getCanvas().style.cursor = "" })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, sectorsGeo])

  // ── Highlight selected sector ───────────────────────────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m?.getLayer("sector-selected")) return
    const id = result?.sector?.id ?? ""
    m.setFilter("sector-selected", ["==", ["get", "id"], id])
    if (result?.sector?.centroid) {
      m.flyTo({
        center: [result.sector.centroid.lng, result.sector.centroid.lat],
        zoom: Math.max(m.getZoom(), 13),
        duration: 800,
      })
    }
  }, [mapReady, result])

  // ── Improvement markers layer ───────────────────────────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m) return

    const improvements = result?.improvements ?? []
    const geojson = {
      type: "FeatureCollection",
      features: improvements
        .filter(i => i.suggested_lat && i.suggested_lng)
        .map(i => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [i.suggested_lng, i.suggested_lat] },
          properties: { rank: i.rank, title: i.title, delta: i.score_delta,
                        highlighted: highlightedImp?.rank === i.rank },
        })),
    }

    if (m.getSource("improvements")) {
      m.getSource("improvements").setData(geojson)
    } else {
      m.addSource("improvements", { type: "geojson", data: geojson })
      m.addLayer({
        id: "improvements-circles",
        type: "circle",
        source: "improvements",
        paint: {
          "circle-radius": ["case", ["get", "highlighted"], 12, 8],
          "circle-color": "#facc15",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#fff",
          "circle-opacity": 0.9,
        },
      })
    }
    // Fly to highlighted improvement
    if (highlightedImp?.suggested_lat && highlightedImp?.suggested_lng) {
      m.easeTo({
        center: [highlightedImp.suggested_lng, highlightedImp.suggested_lat],
        zoom: Math.max(m.getZoom(), 14),
        duration: 500,
      })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, result, highlightedImp])

  // ── Re-fetch on scenario change ─────────────────────────────────────────
  useEffect(() => {
    if (isFirstRender.current) { isFirstRender.current = false; return }
    if (result?.sector?.id) fetchBySectorId(result.sector.id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario])

  // ── API calls ───────────────────────────────────────────────────────────
  const fetchByAddress = async () => {
    const addr = addressInput.trim()
    if (!addr) return
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(
        `${API}/api/score?address=${encodeURIComponent(addr)}&scenario=${scenario}`
      )
      if (!r.ok) throw new Error(await r.json().then(d => d.detail).catch(() => r.statusText))
      setResult(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const fetchBySectorId = async (sectorId) => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/api/sector/${sectorId}?scenario=${scenario}`)
      if (!r.ok) throw new Error(await r.json().then(d => d.detail).catch(() => r.statusText))
      setResult(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────
  const communeName = result ? COMMUNES[result.sector.municipality] : null
  const scenarioLabel = SCENARIOS.find(s => s.id === scenario)?.label
  const topPct = result ? Math.max(1, Math.round(100 - result.percentile)) : null

  return (
    <div className="app">
      <aside className="panel">
        {/* Header */}
        <div className="panel-header">
          <span className="logo-dot" />
          <span className="logo-text">Neighbourhood Fit</span>
          <span className="logo-city">Brussels</span>
        </div>

        {/* Scenario tabs */}
        <ScenarioTabs scenario={scenario} onChange={setScenario} />

        {/* Search */}
        <div className="search-row">
          <input
            className="address-input"
            placeholder="Address or neighbourhood…"
            value={addressInput}
            onChange={e => setAddressInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && fetchByAddress()}
          />
          <button className="search-btn" onClick={fetchByAddress} disabled={loading}>
            {loading ? "…" : "Go"}
          </button>
        </div>

        {error && <p className="error-msg">{error}</p>}

        {!result && !loading && (
          <p className="hint">Enter an address above or click any sector on the map.</p>
        )}

        {loading && <div className="skeleton-block" />}

        {result && !loading && (
          <div className="results">
            {/* Sector name */}
            <div className="sector-name">
              {result.sector.name_fr || result.sector.id}
              {communeName && <span className="sector-muni"> · {communeName}</span>}
            </div>

            {/* Score card */}
            <div className="score-card">
              <ScoreRing score={result.score} />
              <div className="score-meta">
                <p className="score-scenario">{scenarioLabel} Fit Score</p>
                <p className="score-pct">Top {topPct}% in Brussels</p>
                {result.sector.population > 0 && (
                  <p className="score-pop">
                    ~{result.sector.population.toLocaleString()} residents
                  </p>
                )}
              </div>
            </div>

            {/* Pros / cons */}
            <WhyPanel pros={result.pros} cons={result.cons} />

            {/* POI layer toggles */}
            <MapLayerToggles
              sectorId={result.sector.id}
              mapInst={mapInst}
              mapReady={mapReady}
            />

            {/* Improvements */}
            <ImprovementsList
              improvements={result.improvements}
              onHighlight={imp => setHighlightedImp(
                imp.rank === highlightedImp?.rank ? null : imp
              )}
            />

            {/* Category breakdown */}
            <p className="section-title">Category breakdown</p>
            <CategoryBars breakdown={result.breakdown} scenario={scenario} />

            {/* Disclosure */}
            <DisclosureFooter disclosure={result.disclosure} />
          </div>
        )}
      </aside>

      {/* Map */}
      <div className="map-wrap" ref={mapContainer} />
    </div>
  )
}
