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

const CITIES = [
  { id: "brussels", label: "Brussels", center: [4.352, 50.846], zoom: 11 },
  { id: "antwerp",  label: "Antwerp",  center: [4.402, 51.221], zoom: 12, soon: true },
  { id: "paris",    label: "Paris",    center: [2.349, 48.864], zoom: 12, soon: true },
  { id: "london",   label: "London",   center: [-0.118, 51.509], zoom: 11, soon: true },
]

const FILTER_THRESHOLDS = [
  { value: 50, label: "Decent" },
  { value: 60, label: "Good" },
  { value: 75, label: "Great" },
]

// Brussels NIS commune codes
const COMMUNES = {
  "21001": "Anderlecht",        "21002": "Auderghem",
  "21003": "Berchem-Ste-Agathe","21004": "Brussels",
  "21005": "Etterbeek",         "21006": "Evere",
  "21007": "Forest",            "21008": "Ganshoren",
  "21009": "Ixelles",           "21010": "Jette",
  "21011": "Koekelberg",        "21012": "Molenbeek",
  "21013": "Saint-Gilles",      "21014": "Saint-Josse",
  "21015": "Schaerbeek",        "21016": "Uccle",
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
  sport: "Sports",            dog_park: "Dog parks",
}

const MAP_LAYERS = [
  { cat: "school",   color: "#60a5fa", label: "Schools" },
  { cat: "park",     color: "#4ade80", label: "Parks" },
  { cat: "pharmacy", color: "#f87171", label: "Pharmacies" },
  { cat: "transit",  color: "#a78bfa", label: "Transit" },
  { cat: "cafe",     color: "#fb923c", label: "Cafés" },
  { cat: "sport",    color: "#facc15", label: "Sport" },
]

// True when the user has asked the OS to minimise motion. Checked at call time
// so a mid-session preference change is respected.
function prefersReducedMotion() {
  return typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
}

// Escape text before interpolating it into popup HTML. POI / sector names come
// from our own DB today, but escaping hardens against future untrusted sources.
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]))
}

// Count a number up from 0 to `target` over `duration` ms with an ease-out
// curve, via requestAnimationFrame. Respects reduced-motion (returns the final
// value instantly) and cleans up / restarts whenever the target changes.
function useCountUp(target, duration = 600) {
  const safeTarget = Number.isFinite(target) ? target : 0
  const [value, setValue] = useState(() =>
    prefersReducedMotion() ? safeTarget : 0
  )
  useEffect(() => {
    if (prefersReducedMotion()) { setValue(safeTarget); return }
    let raf
    let start = null
    const tick = (ts) => {
      if (start === null) start = ts
      const t = Math.min(1, (ts - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3) // ease-out cubic
      setValue(Math.round(safeTarget * eased))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [safeTarget, duration])
  return value
}

// Inline count-up text for a single number (e.g. percentile, compare scores).
function CountUp({ value, duration = 600 }) {
  return <>{useCountUp(value, duration)}</>
}

function scoreColor(score) {
  if (score >= 70) return "#4ade80"
  if (score >= 50) return "#facc15"
  if (score >= 30) return "#f97316"
  return "#f87171"
}

// Lighten a #rrggbb hex toward white by `amount` (0..1). Used for gradient top
// stops so we never depend on CSS color-mix inside SVG.
function lighten(hex, amount = 0.4) {
  const n = parseInt(hex.slice(1), 16)
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255
  const mix = (c) => Math.round(c + (255 - c) * amount)
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`
}

// Read URL params once on module load (before React initialises)
function readUrlParams() {
  const p = new URLSearchParams(window.location.search)
  const s = p.get("scenario")
  return {
    sector: p.get("sector"),
    scenario: s && ["family", "senior", "remote"].includes(s) ? s : null,
  }
}

// ── Components ──────────────────────────────────────────────────────────────

function CityPicker({ city, onChange }) {
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const ref = useRef(null)
  const btnRef = useRef(null)
  const optionRefs = useRef([])
  const current = CITIES.find(c => c.id === city) ?? CITIES[0]

  // Indices of selectable (non-"soon") cities, for keyboard navigation.
  const selectableIdx = CITIES.map((c, i) => (c.soon ? -1 : i)).filter(i => i >= 0)

  const close = useCallback((restoreFocus = true) => {
    setOpen(false)
    setActiveIdx(-1)
    if (restoreFocus) btnRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return
    // Open onto the current city (or first selectable).
    const start = CITIES.findIndex(c => c.id === city)
    setActiveIdx(start >= 0 && !CITIES[start].soon ? start : selectableIdx[0] ?? -1)
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) close(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // Move DOM focus to the active option so screen readers announce it.
  useEffect(() => {
    if (open && activeIdx >= 0) optionRefs.current[activeIdx]?.focus()
  }, [open, activeIdx])

  const choose = (c) => { if (!c.soon) { onChange(c); close() } }

  const moveActive = (dir) => {
    const pos = selectableIdx.indexOf(activeIdx)
    const nextPos = pos < 0
      ? 0
      : (pos + dir + selectableIdx.length) % selectableIdx.length
    setActiveIdx(selectableIdx[nextPos])
  }

  const onListKey = (e) => {
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); moveActive(1); break
      case "ArrowUp":   e.preventDefault(); moveActive(-1); break
      case "Home":      e.preventDefault(); setActiveIdx(selectableIdx[0]); break
      case "End":       e.preventDefault(); setActiveIdx(selectableIdx[selectableIdx.length - 1]); break
      case "Enter":
      case " ":         e.preventDefault(); if (activeIdx >= 0) choose(CITIES[activeIdx]); break
      case "Escape":    e.preventDefault(); close(); break
      case "Tab":       close(false); break
      default: break
    }
  }

  return (
    <div className="city-picker" ref={ref}>
      <button
        ref={btnRef}
        className="city-btn"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`City: ${current.label}. Change city`}
      >
        {current.label} <span className="city-caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <ul
          className="city-dropdown"
          role="listbox"
          aria-label="Select city"
          aria-activedescendant={activeIdx >= 0 ? `city-opt-${CITIES[activeIdx].id}` : undefined}
          onKeyDown={onListKey}
        >
          {CITIES.map((c, i) => (
            <li
              key={c.id}
              id={`city-opt-${c.id}`}
              ref={el => (optionRefs.current[i] = el)}
              role="option"
              aria-selected={c.id === city}
              aria-disabled={c.soon || undefined}
              tabIndex={-1}
              className={`city-option${c.id === city ? " active" : ""}${c.soon ? " soon" : ""}`}
              onClick={() => choose(c)}
            >
              {c.label}
              {c.soon && <span className="soon-badge">soon</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ScenarioTabs({ scenario, onChange }) {
  return (
    <div className="scenario-tabs" role="tablist" aria-label="Scenario">
      {SCENARIOS.map(s => (
        <button
          key={s.id}
          role="tab"
          aria-selected={scenario === s.id}
          tabIndex={scenario === s.id ? 0 : -1}
          className={`tab-btn${scenario === s.id ? " active" : ""}`}
          onClick={() => onChange(s.id)}
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}

// Categories a given scenario actually scores, plus dog_park which the backend
// always allows. Chips for categories absent from the scenario weights are
// hidden — selecting them would always return "no matches" with no explanation.
// `weights` is the full {scenario: {cat: weight}} map fetched from the backend.
// Until it loads (or if the fetch failed) it is null/empty: fall back to every
// known category so the app still works offline-ish.
function scenarioCategories(scenario, weights) {
  const w = weights?.[scenario]
  if (!w) return Object.keys(CATEGORY_LABELS)
  return Object.keys(CATEGORY_LABELS).filter(
    cat => cat in w || cat === "dog_park"
  )
}

function PreferenceFilter({ scenario, weights, weightsLoaded, filterCats, onToggle, onClear, filterMatching, minScore, onMinScore }) {
  const active  = filterCats.size > 0
  const count   = filterMatching?.length ?? null
  const loading = active && filterMatching === null
  const cats    = scenarioCategories(scenario, weights)

  return (
    <div className="filter-section">
      <div className="filter-header">
        <h2 className="section-title">Find by amenity</h2>
        {active && (
          <div className="filter-header-right">
            <span className="filter-count">{loading ? "…" : `${count} sectors`}</span>
            <button className="filter-clear" onClick={onClear}>clear</button>
          </div>
        )}
      </div>
      <div className="filter-chips">
        {!weightsLoaded && (
          <span className="filter-loading">Loading preferences…</span>
        )}
        {weightsLoaded && cats.map(cat => {
          const isActive = filterCats.has(cat)
          return (
            <button
              key={cat}
              className={`filter-chip${isActive ? " active" : ""}`}
              aria-pressed={isActive}
              onClick={() => onToggle(cat)}
            >
              {CATEGORY_LABELS[cat]}
            </button>
          )
        })}
      </div>
      {active && (
        <div className="filter-threshold">
          <span className="threshold-label" id="min-score-label">Min score:</span>
          <div className="threshold-btns" role="group" aria-labelledby="min-score-label">
            {FILTER_THRESHOLDS.map(t => (
              <button
                key={t.value}
                className={`threshold-btn${minScore === t.value ? " active" : ""}`}
                aria-pressed={minScore === t.value}
                onClick={() => onMinScore(t.value)}
              >
                {t.label} {t.value}+
              </button>
            ))}
          </div>
        </div>
      )}
      {active && !loading && count === 0 && (
        <p className="filter-empty">No sectors match — try a lower threshold.</p>
      )}
    </div>
  )
}

// ── Onboarding tour ─────────────────────────────────────────────────────────

const TOUR_STEPS = [
  {
    icon: "◑",
    title: "Pick your scenario",
    body: "Family, Senior, or Remote Work — the map recolours every Brussels sector based on what matters for your lifestyle.",
  },
  {
    icon: "◎",
    title: "Filter by amenity",
    body: "Select Parks, Cafés, Transit… to highlight only sectors where all of them are within walking distance. Adjust the min-score threshold for strictness.",
  },
  {
    icon: "▣",
    title: "Click any sector",
    body: "Tap a coloured area to see its score, strengths, gaps, and AI-powered improvement suggestions.",
  },
  {
    icon: "↔",
    title: "Compare two areas",
    body: "Hit Compare after selecting a sector and click (or type) a second address to see a side-by-side breakdown.",
  },
]

function TourCard({ step, onNext, onSkip }) {
  if (step === null) return null
  const s = TOUR_STEPS[step]
  const isLast = step === TOUR_STEPS.length - 1
  return (
    <div className="tour-card">
      <div className="tour-dots">
        {TOUR_STEPS.map((_, i) => (
          <span key={i} className={`tour-dot${i === step ? " active" : ""}${i < step ? " done" : ""}`} />
        ))}
      </div>
      <div className="tour-icon" aria-hidden="true">{s.icon}</div>
      <p className="tour-title">{s.title}</p>
      <p className="tour-body">{s.body}</p>
      <div className="tour-actions">
        <button className="tour-skip" onClick={onSkip}>Skip</button>
        <button className="tour-next" onClick={isLast ? onSkip : onNext}>
          {isLast ? "Get started" : "Next →"}
        </button>
      </div>
    </div>
  )
}

function MapLegend() {
  return (
    <div className="map-legend">
      <span className="legend-label">Score</span>
      {[
        { range: "70+",   color: "#4ade80" },
        { range: "50–70", color: "#facc15" },
        { range: "30–50", color: "#f97316" },
        { range: "0–30",  color: "#dc2626" },
      ].map(({ range, color }) => (
        <div key={range} className="legend-item">
          <span className="legend-swatch" style={{ background: color }} />
          <span className="legend-range">{range}</span>
        </div>
      ))}
    </div>
  )
}

function ScoreRing({ score }) {
  const r = 44
  const circ = 2 * Math.PI * r
  const dash = (score / 100) * circ
  const color = scoreColor(score)
  // Lighter top-stop for a subtle gradient stroke; unique id per score band so
  // two rings (e.g. compare) never share a gradient definition.
  const gradId = `ring-grad-${color.replace("#", "")}`
  const lighter = lighten(color, 0.35)
  const display = useCountUp(score, 600)
  return (
    <svg className="score-ring" width="110" height="110" viewBox="0 0 110 110"
         role="img" aria-label={`Fit score ${score} out of 100`}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={lighter} />
          <stop offset="100%" stopColor={color} />
        </linearGradient>
      </defs>
      <circle cx="55" cy="55" r={r} fill="none" stroke="#242938" strokeWidth="9" />
      <circle
        cx="55" cy="55" r={r} fill="none"
        stroke={`url(#${gradId})`} strokeWidth="9"
        strokeDasharray={`${dash} ${circ}`}
        strokeLinecap="round"
        transform="rotate(-90 55 55)"
        style={{
          transition: "stroke-dasharray 0.7s ease",
          filter: `drop-shadow(0 0 5px ${color}66)`,
        }}
      />
      <text x="55" y="61" textAnchor="middle" fill="#f8fafc" fontSize="26" fontWeight="700">
        {display}
      </text>
    </svg>
  )
}

function CategoryBars({ breakdown, scenario, weights }) {
  // Re-key the whole list on sector + scenario so the bars remount and replay
  // their 0 -> value width transition on each new result.
  const sortKey = `${scenario}`
  const w = weights?.[scenario] ?? {}
  // If weights have not arrived (or failed to load), fall back to showing every
  // category present in the breakdown so the section is never empty.
  const haveWeights = Object.keys(w).length > 0
  const items = Object.entries(breakdown)
    .filter(([cat]) =>
      cat in CATEGORY_LABELS && (haveWeights ? cat in w : true))
    .map(([cat, raw]) => ({
      cat,
      score: Math.min(100, Math.round(raw * 100)),
      weight: w[cat] ?? 0,
    }))
    .sort((a, b) => b.weight - a.weight)

  return (
    <div className="category-bars">
      {items.map(({ cat, score }) => (
        <div key={cat} className="bar-row">
          <span className="bar-label">{CATEGORY_LABELS[cat]}</span>
          <div className="bar-track">
            <Bar key={`${sortKey}-${cat}`} score={score} />
          </div>
          <span className="bar-score">{score}</span>
        </div>
      ))}
    </div>
  )
}

// A single category bar that animates its width from 0 to `score` on mount.
// Remounting (via key) on each new sector re-triggers the grow transition.
// The vertical gradient (lighter top) gives the fill a touch of depth.
function Bar({ score }) {
  const [grown, setGrown] = useState(prefersReducedMotion())
  useEffect(() => {
    if (prefersReducedMotion()) return
    const id = requestAnimationFrame(() => setGrown(true))
    return () => cancelAnimationFrame(id)
  }, [])
  const color = scoreColor(score)
  return (
    <div
      className="bar-fill"
      style={{
        width: grown ? `${score}%` : "0%",
        // Solid colour as a base; a lighter top stop adds a touch of depth.
        // Computed in JS (lighten) so we never depend on CSS color-mix.
        backgroundColor: color,
        backgroundImage: `linear-gradient(180deg, ${lighten(color, 0.22)} 0%, ${color} 100%)`,
      }}
    />
  )
}

function NarrativeBlock({ narrative }) {
  if (!narrative) return null
  return <p className="narrative-text">{narrative}</p>
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

function ComparePanel({ cmp, onClose }) {
  const { a, b, deltas, scenario } = cmp
  const scenLabel = SCENARIOS.find(s => s.id === scenario)?.label ?? scenario

  return (
    <div className="compare-panel">
      <div className="compare-header">
        <span className="compare-title">Comparing · {scenLabel}</span>
        <button className="compare-close" onClick={onClose} aria-label="Close comparison">✕</button>
      </div>

      <div className="compare-scores">
        {[
          { side: "a", sec: a.sector, score: a.score, pct: a.percentile },
          { side: "b", sec: b.sector, score: b.score, pct: b.percentile },
        ].map(({ side, sec, score, pct }) => (
          <div key={side} className={`compare-side compare-side-${side}`}>
            <span className="compare-sector-name" lang="fr">{sec.name_fr || sec.id}</span>
            <span className="compare-score" style={{ color: scoreColor(score) }}>
              <CountUp value={score} />
            </span>
            {Number.isFinite(pct) && <span className="compare-pct">top {Math.max(1, 100 - pct)}%</span>}
          </div>
        ))}
      </div>

      <p className="compare-verdict">
        {cmp.tradeoffNarrative || (
          a.score === b.score
            ? "Scores are equal."
            : a.score > b.score
            ? `${a.sector.name_fr || "A"} scores higher (+${a.score - b.score} pts).`
            : `${b.sector.name_fr || "B"} scores higher (+${b.score - a.score} pts).`
        )}
      </p>

      <div className="compare-deltas">
        {deltas.slice(0, 10).map(d => {
          const label = CATEGORY_LABELS[d.category] ?? d.category
          const maxVal = Math.max(d.a, d.b, 1)
          return (
            <div key={d.category} className="delta-row">
              <span className="delta-label">{label}</span>
              <div className="delta-bars">
                <div
                  className={`delta-bar delta-bar-a${d.winner === "a" ? " winner" : ""}`}
                  style={{ width: `${(d.a / maxVal) * 100}%` }}
                  title={`A: ${d.a}`}
                />
                <div
                  className={`delta-bar delta-bar-b${d.winner === "b" ? " winner" : ""}`}
                  style={{ width: `${(d.b / maxVal) * 100}%` }}
                  title={`B: ${d.b}`}
                />
              </div>
              <span className="delta-scores">{d.a} vs {d.b}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ImprovementsList({ improvements, onHighlight }) {
  if (!improvements?.length) return null
  return (
    <div className="improvements">
      <h3 className="section-title">Score boosters</h3>
      {improvements.map(imp => (
        <button key={imp.rank} className="imp-item" onClick={() => onHighlight?.(imp)}>
          <span className="imp-title">{imp.title}</span>
          <span className="imp-meta">
            {imp.from_score} → {imp.to_score}
            <span className="imp-gain">+{imp.score_delta}</span>
          </span>
        </button>
      ))}
      <p className="imp-map-hint">
        <span className="imp-dot" aria-hidden="true" /> yellow markers on the map show suggested locations
      </p>
    </div>
  )
}

// ── POI layer toggles ───────────────────────────────────────────────────────

function MapLayerToggles({ sectorId, mapInst, mapReady, active, onActiveChange }) {
  const activeRef = useRef(active)
  const cache = useRef({})

  useEffect(() => { activeRef.current = active }, [active])



  const _addLayer = useCallback(async (cat, color, sid) => {
    const m = mapInst.current
    if (!m) return
    const key = `${sid}_${cat}`
    let features = cache.current[key]
    if (!features) {
      try {
        const data = await fetch(
          `${API}/api/pois?sector_id=${sid}&categories=${cat}`
        ).then(r => r.json())
        features = (data.pois ?? []).map(p => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [p.lng, p.lat] },
          properties: { name: p.name },
        }))
        cache.current[key] = features
      } catch (e) { console.error(`POI load failed (${cat})`, e); return }
    }
    const srcId = `poi-src-${cat}`
    const layerId = `poi-${cat}`
    if (!m.getSource(srcId))
      m.addSource(srcId, { type: "geojson", data: { type: "FeatureCollection", features } })
    if (!m.getLayer(layerId))
      m.addLayer({
        id: layerId, type: "circle", source: srcId,
        paint: {
          "circle-radius": 5, "circle-color": color,
          "circle-stroke-width": 1.5, "circle-stroke-color": "#fff",
          "circle-opacity": 0.88,
        },
      })
    if (m.getLayer("improvements-circles")) m.moveLayer("improvements-circles")
  }, [mapInst])

  // When sector changes: remove old layers, re-add any that were active
  useEffect(() => {
    const m = mapInst.current
    if (!m || !mapReady) return
    MAP_LAYERS.forEach(({ cat }) => {
      if (m.getLayer(`poi-${cat}`)) m.removeLayer(`poi-${cat}`)
      if (m.getSource(`poi-src-${cat}`)) m.removeSource(`poi-src-${cat}`)
    })
    MAP_LAYERS.forEach(({ cat, color }) => {
      if (activeRef.current.has(cat)) _addLayer(cat, color, sectorId)
    })
  }, [sectorId, mapReady, _addLayer])

  const toggle = async (cat, color) => {
    const m = mapInst.current
    if (!m || !mapReady) return
    if (active.has(cat)) {
      if (m.getLayer(`poi-${cat}`)) m.removeLayer(`poi-${cat}`)
      if (m.getSource(`poi-src-${cat}`)) m.removeSource(`poi-src-${cat}`)
      onActiveChange(prev => { const s = new Set(prev); s.delete(cat); return s })
    } else {
      await _addLayer(cat, color, sectorId)
      onActiveChange(prev => new Set([...prev, cat]))
    }
  }

  return (
    <div>
      <h3 className="section-title">Show on map</h3>
      <div className="layer-toggles">
        {MAP_LAYERS.map(({ cat, color, label }) => (
          <button
            key={cat}
            className={`layer-btn${active.has(cat) ? " active" : ""}`}
            aria-pressed={active.has(cat)}
            onClick={() => toggle(cat, color)}
          >
            <span className="layer-dot" style={{ background: color }} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}

function GroqPanel({ sectorId, scenario }) {
  const [question, setQuestion] = useState("")
  const [answer, setAnswer]     = useState("")
  const [streaming, setStreaming] = useState(false)
  const [groqError, setGroqError] = useState(null)
  const abortRef = useRef(null)

  // Abort any in-flight stream when the panel unmounts (e.g. sector change).
  useEffect(() => () => abortRef.current?.abort(), [])

  const ask = async () => {
    const q = question.trim()
    setAnswer("")
    setGroqError(null)
    setStreaming(true)
    if (abortRef.current) abortRef.current.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      const resp = await fetch(`${API}/api/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sector_id: sectorId, scenario, question: q || null }),
        signal: ctrl.signal,
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || resp.statusText)
      }

      if (!resp.body) throw new Error("No response stream")
      const reader = resp.body.getReader()
      const dec = new TextDecoder()
      let buf = ""
      let done = false

      while (!done) {
        const { value, done: d } = await reader.read()
        done = d
        buf += dec.decode(value ?? new Uint8Array(), { stream: !d })
        // SSE frames are separated by a blank line ("\n\n"); split on the frame
        // boundary so tokens that themselves contain newlines are never split.
        const frames = buf.split("\n\n")
        buf = frames.pop()
        for (const frame of frames) {
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue
            const payload = line.slice(6)
            if (payload === "[DONE]") { done = true; break }
            try {
              const obj = JSON.parse(payload)
              if (obj.error) throw new Error(obj.error)
              if (obj.token) setAnswer(prev => prev + obj.token)
            } catch (_) {}
          }
          if (done) break
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") setGroqError(e.message)
    } finally {
      // Only the most recent request may clear the flag — a stale aborted
      // request must not flip state for the request that replaced it.
      if (abortRef.current === ctrl) setStreaming(false)
    }
  }

  return (
    <div className="grok-panel">
      <h3 className="section-title">Ask Groq</h3>
      <div className="grok-input-row">
        <input
          className="address-input grok-input"
          placeholder="Any question about this neighbourhood…"
          aria-label="Ask a question about this neighbourhood"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === "Enter" && !streaming && ask()}
          disabled={streaming}
        />
        <button className="search-btn grok-btn" onClick={ask} disabled={streaming} aria-busy={streaming || undefined}>
          {streaming ? <span className="btn-spinner" aria-hidden="true" /> : "Ask"}
        </button>
      </div>
      {groqError && <p className="error-msg" role="alert">{groqError}</p>}
      {(answer || streaming) && (
        <div className={`grok-answer${streaming ? " streaming" : ""}`} aria-live="polite">
          {answer}
          {streaming && <span className="grok-cursor" aria-hidden="true" />}
        </div>
      )}
    </div>
  )
}

// Keyboard / screen-reader path to any sector — the map itself is mouse-only.
// Groups sectors by commune (NIS code = first 5 chars of the sector id) and
// drives the same selection handler as a map click.
function SectorPicker({ sectorsGeo, selectedId, onSelect }) {
  const groups = {}
  for (const feat of sectorsGeo?.features ?? []) {
    const id = feat.properties?.id
    if (!id) continue
    const nis = id.slice(0, 5)
    const commune = COMMUNES[nis] ?? "Other"
    ;(groups[commune] ??= []).push({
      id,
      name: feat.properties?.name_fr || id,
    })
  }
  const communeNames = Object.keys(groups).sort((a, b) => a.localeCompare(b))
  for (const c of communeNames) {
    groups[c].sort((a, b) => a.name.localeCompare(b.name, "fr"))
  }

  const disabled = communeNames.length === 0

  return (
    <div className="sector-picker-row">
      <label className="sector-picker-label" htmlFor="sector-picker">
        Or pick a sector from the list
      </label>
      <select
        id="sector-picker"
        className="sector-picker"
        value={selectedId ?? ""}
        disabled={disabled}
        onChange={e => { if (e.target.value) onSelect(e.target.value) }}
      >
        <option value="">{disabled ? "Loading sectors…" : "Choose a sector…"}</option>
        {communeNames.map(commune => (
          <optgroup key={commune} label={commune}>
            {groups[commune].map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </optgroup>
        ))}
      </select>
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
  // Read URL params once before first render
  const urlParams = useRef(readUrlParams())

  const [city, setCity]                   = useState("brussels")
  // Scenario -> {category: weight}, fetched once from the backend. Null until
  // it resolves (or fails); on failure we keep {} so the UI falls back to
  // showing every known category rather than crashing.
  const [weights, setWeights]             = useState(null)
  const weightsLoaded = weights !== null
  const [addressInput, setAddressInput]   = useState("")
  const [scenario, setScenario]           = useState(urlParams.current.scenario ?? "family")
  const [loading, setLoading]             = useState(false)
  const [error, setError]                 = useState(null)
  const [result, setResult]               = useState(null)
  const [mapReady, setMapReady]           = useState(false)
  const [sectorsGeo, setSectorsGeo]       = useState(null)
  const [highlightedImp, setHighlightedImp] = useState(null)
  const [compareAddr, setCompareAddr]     = useState("")
  const [compareMode, setCompareMode]     = useState(false)
  const [compareResult, setCompareResult] = useState(null)
  const [compareLoading, setCompareLoading] = useState(false)
  const [filterCatsByScenario, setFilterCatsByScenario] = useState({ family: new Set(), senior: new Set(), remote: new Set() })
  const [mapActiveLayers, setMapActiveLayers] = useState(new Set())
  const filterCats = filterCatsByScenario[scenario] ?? new Set()
  const [filterMatching, setFilterMatching] = useState(null)
  const [filterMinScore, setFilterMinScore] = useState(60)
  const [tourStep, setTourStep]           = useState(() =>
    localStorage.getItem("nfs-tour-done") ? null : 0
  )

  const mapContainer          = useRef(null)
  const mapInst               = useRef(null)
  const geoCache              = useRef({})
  const isFirstRender         = useRef(true)
  const initialSectorLoaded   = useRef(false)
  const compareModeRef        = useRef(false)
  const resultRef             = useRef(null)
  const scenarioRef           = useRef(urlParams.current.scenario ?? "family")
  const fetchBySectorIdRef    = useRef(null)
  const fetchCompareByRef     = useRef(null)
  const compareResultRef      = useRef(null)
  const latestGeoKey          = useRef("")

  // ── Tour ────────────────────────────────────────────────────────────────
  const advanceTour = useCallback(() => {
    setTourStep(prev => {
      const next = (prev ?? 0) + 1
      if (next >= TOUR_STEPS.length) {
        localStorage.setItem("nfs-tour-done", "1")
        return null
      }
      return next
    })
  }, [])

  const skipTour = useCallback(() => {
    localStorage.setItem("nfs-tour-done", "1")
    setTourStep(null)
  }, [])

  const startTour = useCallback(() => setTourStep(0), [])

  // ── Filter callbacks ────────────────────────────────────────────────────
  const toggleFilterCat = useCallback((cat) => {
    setFilterCatsByScenario(prev => {
      const next = new Set(prev[scenario])
      if (next.has(cat)) next.delete(cat)
      else next.add(cat)
      return { ...prev, [scenario]: next }
    })
  }, [scenario])

  const clearFilter = useCallback(() => {
    setFilterCatsByScenario(prev => ({ ...prev, [scenario]: new Set() }))
    setFilterMatching(null)
  }, [scenario])

  // Drop any selected categories that the current scenario does not score —
  // chips for them are hidden, and the backend would return "no matches".
  // No-op until weights have loaded (we cannot know what is allowed yet).
  useEffect(() => {
    if (!weightsLoaded) return
    const allowed = new Set(scenarioCategories(scenario, weights))
    setFilterCatsByScenario(prev => {
      const set = prev[scenario] ?? new Set()
      const pruned = new Set([...set].filter(c => allowed.has(c)))
      if (pruned.size === set.size) return prev
      return { ...prev, [scenario]: pruned }
    })
  }, [scenario, weights, weightsLoaded])

  // ── City switch ─────────────────────────────────────────────────────────
  const handleCityChange = useCallback((cityConfig) => {
    setCity(cityConfig.id)
    setResult(null)
    setCompareResult(null)
    setCompareMode(false)
    setCompareAddr("")
    setFilterCatsByScenario({ family: new Set(), senior: new Set(), remote: new Set() })
    setFilterMatching(null)
    setSectorsGeo(null)
    setMapActiveLayers(new Set())
    const m = mapInst.current
    if (m) {
      // Tear down the previous city's POI layers — MapLayerToggles is unmounted
      // (no result yet) so its sector-change cleanup never runs for the new city.
      MAP_LAYERS.forEach(({ cat }) => {
        if (m.getLayer(`poi-${cat}`)) m.removeLayer(`poi-${cat}`)
        if (m.getSource(`poi-src-${cat}`)) m.removeSource(`poi-src-${cat}`)
      })
      m.flyTo({ center: cityConfig.center, zoom: cityConfig.zoom, duration: 1200, animate: !prefersReducedMotion() })
    }
  }, [])

  // ── Load scenario weights (once) ──────────────────────────────────────────
  // Drives which amenity chips appear per scenario and the category-bar order.
  // On failure we set {} so scenarioCategories falls back to all categories.
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/weights`)
      .then(r => r.json())
      .then(data => { if (!cancelled) setWeights(data ?? {}) })
      .catch(e => {
        console.error("weights load failed", e)
        if (!cancelled) setWeights({})
      })
    return () => { cancelled = true }
  }, [])

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
  const loadSectorsGeo = useCallback(async (scen, cty) => {
    const key = `${cty}:${scen}`
    latestGeoKey.current = key
    if (geoCache.current[key]) { setSectorsGeo(geoCache.current[key]); return }
    try {
      const data = await fetch(`${API}/api/sectors.geojson?scenario=${scen}&city=${cty}`).then(r => r.json())
      geoCache.current[key] = data
      // Ignore a stale response if the user has since switched city/scenario.
      if (latestGeoKey.current === key) setSectorsGeo(data)
    } catch (e) { console.error("sectors.geojson load failed", e) }
  }, [])

  useEffect(() => { loadSectorsGeo(scenario, city) }, [scenario, city, loadSectorsGeo])

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

    // Soft glow underneath the crisp selection outline (wider, translucent,
    // blurred) — gives the selected sector a clear sense of elevation.
    m.addLayer({
      id: "sector-selected-glow",
      type: "line",
      source: "sectors",
      filter: ["==", ["get", "id"], ""],
      paint: {
        "line-color": "#bfdbfe",
        "line-width": 9,
        "line-opacity": 0.45,
        "line-blur": 4,
      },
    })

    m.addLayer({
      id: "sector-selected",
      type: "line",
      source: "sectors",
      filter: ["==", ["get", "id"], ""],
      paint: { "line-color": "#ffffff", "line-width": 3.2, "line-opacity": 1 },
    })

    m.addLayer({
      id: "sector-compare-b",
      type: "line",
      source: "sectors",
      filter: ["==", ["get", "id"], ""],
      paint: { "line-color": "#a78bfa", "line-width": 3, "line-opacity": 1 },
    })

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 6 })
    m.on("mousemove", "sectors-fill", e => {
      if (m.getLayer("improvements-circles") &&
          m.queryRenderedFeatures(e.point, { layers: ["improvements-circles"] }).length > 0) {
        popup.remove()
        return
      }
      const feat = e.features?.[0]
      if (!feat) return
      const { name_fr, score } = feat.properties
      const scoreStr = score != null ? ` <span class="popup-score">${escapeHtml(score)}</span>` : ""
      const label = escapeHtml(name_fr || feat.properties.id)
      popup.setLngLat(e.lngLat)
        .setHTML(`<div class="map-popup" lang="fr">${label}${scoreStr}</div>`)
        .addTo(m)
    })
    m.on("mouseleave", "sectors-fill", () => {
      popup.remove()
      m.getCanvas().style.cursor = ""
    })
    m.on("click", "sectors-fill", e => {
      const id = e.features?.[0]?.properties?.id
      if (!id) return
      if (compareModeRef.current && resultRef.current?.sector?.id) {
        fetchCompareByRef.current(id)
      } else {
        fetchBySectorIdRef.current(id)
      }
    })
    m.on("mouseenter", "sectors-fill", () => {
      m.getCanvas().style.cursor = compareModeRef.current ? "crosshair" : "pointer"
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, sectorsGeo])

  // ── Filter highlight layers ─────────────────────────────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m || !m.getSource("sectors")) return

    if (m.getLayer("filter-match"))   m.removeLayer("filter-match")
    if (m.getLayer("filter-overlay")) m.removeLayer("filter-overlay")

    if (filterMatching === null) return

    const beforeId = m.getLayer("sector-selected") ? "sector-selected" : undefined

    m.addLayer({
      id: "filter-overlay",
      type: "fill",
      source: "sectors",
      paint: { "fill-color": "#000000", "fill-opacity": 0.62 },
    }, beforeId)

    m.addLayer({
      id: "filter-match",
      type: "fill",
      source: "sectors",
      filter: ["in", ["get", "id"], ["literal", filterMatching]],
      paint: { "fill-color": "#4ade80", "fill-opacity": 0.78 },
    }, beforeId)

    // Keep POI and improvement layers on top of the filter overlay
    MAP_LAYERS.forEach(({ cat }) => {
      if (m.getLayer(`poi-${cat}`)) m.moveLayer(`poi-${cat}`)
    })
    if (m.getLayer("improvements-circles")) m.moveLayer("improvements-circles")
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, filterMatching, sectorsGeo])

  // ── Fetch filter results ────────────────────────────────────────────────
  useEffect(() => {
    if (filterCats.size === 0) {
      setFilterMatching(null)
      return
    }
    setFilterMatching(null) // reset while loading new results
    let cancelled = false
    const cats = [...filterCats].join(",")
    fetch(`${API}/api/filter?scenario=${scenario}&categories=${cats}&min_score=${filterMinScore}&city=${city}`)
      .then(r => r.json())
      .then(data => { if (!cancelled) setFilterMatching(data.matching) })
      .catch(() => { if (!cancelled) setFilterMatching([]) })
    return () => { cancelled = true }
  }, [filterCats, scenario, filterMinScore, city])

  // ── Highlight selected sector ───────────────────────────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m?.getLayer("sector-selected")) return
    const id = result?.sector?.id ?? ""
    m.setFilter("sector-selected", ["==", ["get", "id"], id])
    if (m.getLayer("sector-selected-glow"))
      m.setFilter("sector-selected-glow", ["==", ["get", "id"], id])

    // One-shot selection pulse on the glow line: a brief widen-and-fade back to
    // the resting state. Driven by rAF so it auto-respects reduced motion.
    let raf
    if (id && m.getLayer("sector-selected-glow") && !prefersReducedMotion()) {
      const start = performance.now()
      const dur = 500
      const pulse = (now) => {
        const t = Math.min(1, (now - start) / dur)
        const k = 1 - Math.pow(1 - t, 2) // ease-out
        m.setPaintProperty("sector-selected-glow", "line-width", 18 - 9 * k)
        m.setPaintProperty("sector-selected-glow", "line-opacity", 0.75 - 0.30 * k)
        if (t < 1) raf = requestAnimationFrame(pulse)
      }
      raf = requestAnimationFrame(pulse)
    }

    if (result?.sector?.centroid) {
      m.flyTo({
        center: [result.sector.centroid.lng, result.sector.centroid.lat],
        zoom: Math.max(m.getZoom(), 13),
        duration: 800,
        animate: !prefersReducedMotion(),
      })
    }

    // Cancel an in-flight pulse and restore resting glow if selection changes.
    return () => {
      if (raf) cancelAnimationFrame(raf)
      if (m.getLayer("sector-selected-glow")) {
        m.setPaintProperty("sector-selected-glow", "line-width", 9)
        m.setPaintProperty("sector-selected-glow", "line-opacity", 0.45)
      }
    }
  }, [mapReady, result])

  // ── Highlight compare sector B ──────────────────────────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m?.getLayer("sector-compare-b")) return
    const id = compareResult?.b?.sector?.id ?? ""
    m.setFilter("sector-compare-b", ["==", ["get", "id"], id])
  }, [mapReady, compareResult])

  // ── Fit map to show both sectors when compare loads ─────────────────────
  useEffect(() => {
    const m = mapInst.current
    if (!mapReady || !m || !compareResult) return
    const ca = compareResult.a.sector.centroid
    const cb = compareResult.b.sector.centroid
    if (!ca || !cb) return
    const bounds = new maplibregl.LngLatBounds()
    bounds.extend([ca.lng, ca.lat])
    bounds.extend([cb.lng, cb.lat])
    m.fitBounds(bounds, { padding: 100, maxZoom: 14, duration: 900, animate: !prefersReducedMotion() })
  }, [mapReady, compareResult])

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
      m.moveLayer("improvements-circles")
    } else {
      m.addSource("improvements", { type: "geojson", data: geojson })
      m.addLayer({
        id: "improvements-circles", type: "circle", source: "improvements",
        paint: {
          "circle-radius": ["case", ["get", "highlighted"], 12, 8],
          "circle-color": "#facc15",
          "circle-stroke-width": 2, "circle-stroke-color": "#fff",
          "circle-opacity": 0.9,
        },
      })
      const impPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, className: "imp-popup-wrap" })
      m.on("mouseenter", "improvements-circles", e => {
        m.getCanvas().style.cursor = "pointer"
        const p = e.features?.[0]?.properties
        if (!p) return
        const sign = p.delta > 0 ? "+" : ""
        impPopup.setLngLat(e.lngLat)
          .setHTML(`<div class="map-popup imp-popup">${escapeHtml(p.title)}<span class="popup-score">${sign}${escapeHtml(p.delta)} pts</span></div>`)
          .addTo(m)
      })
      m.on("mouseleave", "improvements-circles", () => {
        m.getCanvas().style.cursor = ""
        impPopup.remove()
      })
    }
    if (highlightedImp?.suggested_lat && highlightedImp?.suggested_lng) {
      m.easeTo({
        center: [highlightedImp.suggested_lng, highlightedImp.suggested_lat],
        zoom: Math.max(m.getZoom(), 14),
        duration: 500,
        animate: !prefersReducedMotion(),
      })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, result, highlightedImp])

  // ── Sync URL state ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!result) return
    const params = new URLSearchParams()
    params.set("sector", result.sector.id)
    params.set("scenario", scenario)
    history.replaceState(null, "", `?${params}`)
  }, [result, scenario])

  // ── Auto-load sector from URL on map ready ──────────────────────────────
  useEffect(() => {
    if (!mapReady || initialSectorLoaded.current) return
    initialSectorLoaded.current = true
    const { sector } = urlParams.current
    if (sector) fetchBySectorIdRef.current(sector)
  }, [mapReady])

  // ── Re-fetch on scenario change ─────────────────────────────────────────
  useEffect(() => {
    if (isFirstRender.current) { isFirstRender.current = false; return }
    const sectorAId = resultRef.current?.sector?.id
    if (!sectorAId) return
    const prevCompare = compareResultRef.current
    ;(async () => {
      await fetchBySectorId(sectorAId)            // refresh primary card (clears compare)
      const aId = prevCompare?.a?.sector?.id
      const bId = prevCompare?.b?.sector?.id
      if (aId && bId) {
        try { await _doCompare(aId, bId, scenarioRef.current) }  // re-run compare under new scenario
        catch (e) { setError(e.message) }
      }
    })()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario])

  // ── API calls ───────────────────────────────────────────────────────────
  const fetchByAddress = async () => {
    const addr = addressInput.trim()
    if (!addr) return
    setLoading(true)
    setError(null)
    // A brand-new primary search must drop any stale comparison.
    setCompareResult(null)
    setCompareMode(false)
    setCompareAddr("")
    try {
      const r = await fetch(
        `${API}/api/score?address=${encodeURIComponent(addr)}&scenario=${scenario}&city=${city}`
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
    setCompareResult(null)
    setCompareMode(false)
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

  const fetchCompare = async () => {
    const addr = compareAddr.trim()
    if (!addr || !result?.sector?.id) return
    setCompareLoading(true)
    setError(null)
    try {
      const geo = await fetch(
        `${API}/api/score?address=${encodeURIComponent(addr)}&scenario=${scenario}&city=${city}`
      )
      if (!geo.ok) throw new Error(await geo.json().then(d => d.detail).catch(() => geo.statusText))
      const geoData = await geo.json()
      const sectorBId = geoData.sector.id
      if (sectorBId === result.sector.id) {
        setError("Same sector — enter a different address to compare.")
        return
      }
      await _doCompare(result.sector.id, sectorBId, scenario)
    } catch (e) {
      setError(e.message)
    } finally {
      setCompareLoading(false)
    }
  }

  const _doCompare = async (sectorAId, sectorBId, scen = scenarioRef.current) => {
    const cmp = await fetch(
      `${API}/api/compare?a=${sectorAId}&b=${sectorBId}&scenario=${scen}`
    )
    if (!cmp.ok) throw new Error(await cmp.json().then(d => d.detail).catch(() => cmp.statusText))
    setCompareResult(await cmp.json())
    setCompareMode(false)
  }

  const fetchCompareById = async (sectorBId) => {
    const sectorAId = resultRef.current?.sector?.id
    if (!sectorAId || sectorAId === sectorBId) return
    setCompareLoading(true)
    setError(null)
    try {
      await _doCompare(sectorAId, sectorBId)
    } catch (e) {
      setError(e.message)
    } finally {
      setCompareLoading(false)
    }
  }

  // Keep refs in sync on every render
  compareModeRef.current     = compareMode
  resultRef.current          = result
  compareResultRef.current   = compareResult
  scenarioRef.current        = scenario
  fetchBySectorIdRef.current = fetchBySectorId
  fetchCompareByRef.current  = fetchCompareById

  // ── Render ──────────────────────────────────────────────────────────────
  const communeName   = result ? COMMUNES[result.sector.municipality] : null
  const scenarioLabel = SCENARIOS.find(s => s.id === scenario)?.label
  const cityLabel     = CITIES.find(c => c.id === city)?.label ?? "the city"
  const topPct        = result && Number.isFinite(result.percentile)
    ? Math.max(1, Math.round(100 - result.percentile)) : null

  return (
    <div className="app">
      <a className="skip-link" href="#results">Skip to results</a>
      <aside className="panel">
        {/* Header */}
        <div className="panel-header">
          <span className="logo-dot" aria-hidden="true" />
          <h1 className="logo-text">Neighbourhood Fit</h1>
          <CityPicker city={city} onChange={handleCityChange} />
          <button className="tour-trigger" onClick={startTour} title="Feature tour" aria-label="Feature tour">?</button>
        </div>

        {/* Scenario tabs */}
        <ScenarioTabs scenario={scenario} onChange={setScenario} />

        {/* Preference filter */}
        <PreferenceFilter
          scenario={scenario}
          weights={weights}
          weightsLoaded={weightsLoaded}
          filterCats={filterCats}
          onToggle={toggleFilterCat}
          onClear={clearFilter}
          filterMatching={filterMatching}
          minScore={filterMinScore}
          onMinScore={setFilterMinScore}
        />

        {/* Search */}
        <div className="search-row">
          <input
            className="address-input"
            placeholder="Address or neighbourhood…"
            aria-label="Search address or neighbourhood"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "search-error" : undefined}
            value={addressInput}
            onChange={e => setAddressInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !loading && fetchByAddress()}
          />
          <button className="search-btn" onClick={fetchByAddress} disabled={loading} aria-busy={loading || undefined}>
            {loading ? <span className="btn-spinner" aria-hidden="true" /> : "Go"}
          </button>
        </div>

        {/* Keyboard / screen-reader path to a sector (the map is mouse-only) */}
        <SectorPicker
          sectorsGeo={sectorsGeo}
          selectedId={result?.sector?.id}
          onSelect={id => {
            if (compareModeRef.current && resultRef.current?.sector?.id) {
              fetchCompareByRef.current(id)
            } else {
              fetchBySectorIdRef.current(id)
            }
          }}
        />

        {error && <p className="error-msg" role="alert" id="search-error">{error}</p>}

        {!result && !loading && tourStep === null && (
          <p className="hint">
            {filterCats.size > 0
              ? "Matching sectors are highlighted green. Click one to explore."
              : "Enter an address above or click any sector on the map."}
          </p>
        )}

        <TourCard step={tourStep} onNext={advanceTour} onSkip={skipTour} />

        {loading && <div className="skeleton-block" />}

        {result && !loading && (
          <main className="results" id="results" key={result.sector.id} aria-label="Neighbourhood results">
            <h2 className="sector-name">
              <span lang="fr">{result.sector.name_fr || result.sector.id}</span>
              {communeName && <span className="sector-muni"> · {communeName}</span>}
            </h2>

            <div className="score-card">
              <ScoreRing score={result.score} />
              <div className="score-meta">
                <p className="score-scenario">{scenarioLabel} Fit Score</p>
                {topPct != null && <p className="score-pct">Top <CountUp value={topPct} />% in {cityLabel}</p>}
                {result.sector.population > 0 && (
                  <p className="score-pop">
                    ~{result.sector.population.toLocaleString()} residents
                  </p>
                )}
              </div>
            </div>

            <NarrativeBlock narrative={result.narrative} />
            <WhyPanel pros={result.pros} cons={result.cons} />
            <GroqPanel key={result.sector.id} sectorId={result.sector.id} scenario={scenario} />

            {!compareMode && !compareResult && (
              <button className="compare-trigger" onClick={() => setCompareMode(true)}>
                Compare with another address ↔
              </button>
            )}
            {compareMode && !compareResult && (
              <div className="compare-active">
                <p className="compare-hint">
                  {compareLoading ? "Loading…" : "Click any sector on the map, or type an address:"}
                </p>
                <div className="compare-input-row">
                  <input
                    className="address-input"
                    placeholder="Second address…"
                    aria-label="Second address to compare"
                    value={compareAddr}
                    onChange={e => setCompareAddr(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && !compareLoading && fetchCompare()}
                  />
                  <button
                    className="search-btn"
                    onClick={fetchCompare}
                    disabled={compareLoading || !compareAddr.trim()}
                    aria-label="Run comparison"
                    aria-busy={compareLoading || undefined}
                  >
                    {compareLoading ? <span className="btn-spinner" aria-hidden="true" /> : "↔"}
                  </button>
                  <button
                    className="compare-cancel"
                    onClick={() => { setCompareMode(false); setCompareAddr("") }}
                    aria-label="Cancel compare"
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}
            {compareResult && (
              <ComparePanel
                cmp={compareResult}
                onClose={() => { setCompareResult(null); setCompareMode(false); setCompareAddr("") }}
              />
            )}

            <MapLayerToggles sectorId={result.sector.id} mapInst={mapInst} mapReady={mapReady} active={mapActiveLayers} onActiveChange={setMapActiveLayers} />

            <ImprovementsList
              improvements={result.improvements}
              onHighlight={imp => setHighlightedImp(
                imp.rank === highlightedImp?.rank ? null : imp
              )}
            />

            <h3 className="section-title">Category breakdown</h3>
            <CategoryBars breakdown={result.breakdown} scenario={scenario} weights={weights} />
            <DisclosureFooter disclosure={result.disclosure} />
          </main>
        )}
      </aside>

      {/* Map */}
      <div
        className="map-wrap"
        ref={mapContainer}
        role="application"
        aria-label="Map of Brussels sectors coloured by fit score"
      >
        <p className="sr-only">
          This map is interactive with a mouse. Keyboard and screen-reader users
          can select any sector using the sector list or the address search in
          the panel.
        </p>
        <MapLegend />
      </div>
    </div>
  )
}
