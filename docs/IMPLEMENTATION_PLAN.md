# Neighbourhood Fit Score — План реализации

_Дата: 2026-06-01. Связанные документы: `COMPETITIVE_RESEARCH.md` (рынок), `GO_TO_MARKET.md` (монетизация/клин), `BUILD_SPEC.md` (точная data-спека + scoring v1 + AI-слой). Этот файл — архитектура, скоуп, роадмап._

---

## 0. TL;DR (для занятых)

- **Стратегическая ставка:** не «какой score у района», а **«для КОГО, ПОЧЕМУ и как улучшить»**. Это 4 глагола (*explain / switch / improve / compare*), которые ни один конкурент не делает вместе, в EU, где fit-score вообще нет.
- **Архитектура (ВЫБРАНО 2026-06-01):** единый **Python FastAPI** бэкенд (заменяет Express), React/Vite фронт сохраняется. Тяжёлый geo (osmnx/geopandas) — в **оффлайн-пайплайне** (seed в Postgres); рантайм FastAPI держим лёгким (Overpass HTTP + numpy haversine + shapely). Один backend-язык + открытый путь к live-оценке произвольного адреса в v2. _Цена решения: переписываем Express/Sequelize-слой на FastAPI/SQLModel — осознанно принято._
- **MVP-герой:** Brussels, **3 сценария** (Family / Senior / Remote Work), с полным **«почему»** и **«как улучшить»**. Один город, ~700 секторов, всё precomputed.
- **Сроки:** ~6–8 недель соло-темпа до публичного MVP.

---

## 1. Архитектурное решение (ВЫБРАНО: единый Python/FastAPI)

Решение принято 2026-06-01: **заменяем Express на единый Python FastAPI бэкенд**, React/Vite фронт сохраняем. Один backend-язык (геолибы — first-class), и открытый путь к live-оценке произвольного адреса в v2. Принцип из ресерча сохраняется: **тяжёлый geopandas/osmnx не влезает в free-tier рантайм (512 МБ) и не деплоится — он живёт в оффлайн-пайплайне**, рантайм FastAPI остаётся лёгким.

```text
┌─────────────────────────────────────────────────────────────┐
│ OFFLINE (ноутбук или GitHub Actions, запускается вручную)    │
│                                                              │
│  Python pipeline (тяжёлые либы ТОЛЬКО здесь):                │
│   1. Geofabrik extract (Belgium) → osmium фильтр POI         │
│   2. OSMnx walking graph Brussels                            │
│   3. Statbel статистические секторы (полигоны + население)   │
│   4. per-sector × per-scenario fit scores                    │
│      (network nearest-distance + decay; E2SFCA для underserved)│
│   5. «improvement» дельты                                    │
│   6. Экспорт → seed (JSON / SQL / alembic seed)              │
└────────────────────────┬────────────────────────────────────┘
                         │ (seed загружается в БД)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ RUNTIME — единый FastAPI (лёгкий)                            │
│                                                              │
│  Postgres / SQLite  ← SQLModel (или SQLAlchemy)              │
│   • sectors • sector_scores • pois • improvements            │
│                                                              │
│  FastAPI (uvicorn):                                          │
│   • рантайм-зависимости: fastapi, requests (Overpass/ORS),   │
│     numpy (haversine/scoring), shapely (point-in-polygon),   │
│     anthropic (нарратив) — БЕЗ geopandas/osmnx               │
│   • GET /api/score?address=...&scenario=...                  │
│   • GET /api/sectors.geojson  • GET /api/compare             │
│   • POST /api/explain (Claude)                               │
│   • отдаёт built React (StaticFiles) в проде                 │
│                                                              │
│  React/Vite + MapLibre GL + OpenFreeMap (keyless)            │
└─────────────────────────────────────────────────────────────┘
```

**Что это меняет в репозитории (миграция Express → FastAPI):**
- Папку `backend/` (Node) заменяем на Python-сервис: `backend/app/main.py` (FastAPI), `backend/app/models.py` (SQLModel — эквивалент таблиц из §4), `backend/app/scoring.py`, `backend/app/geocode.py`, `backend/pipeline/` (оффлайн osmnx/geopandas-скрипты), `backend/requirements.txt` (или `pyproject.toml`).
- `Dockerfile` переписываем на `python:3.12-slim` базу: stage-1 собирает React (`node:20-alpine`), stage-2 ставит Python-зависимости, runtime — uvicorn, копирует `frontend/dist` → отдаётся через FastAPI `StaticFiles`.
- `db.js` (выбор диалекта по `DATABASE_URL`) → его логика переезжает в Python: `create_engine(DATABASE_URL)` если `postgres://`, иначе `sqlite:///./data.sqlite`. Тот же принцип «один env-var, два диалекта».
- `render.yaml` — `runtime: docker` остаётся; имена сервисов `ai-workshop-db` / `ai-workshop-web` не трогаем (или переименовываем осознанно один раз).
- Фронт (`frontend/`) **не меняется** структурно — только содержимое `App.jsx` → продуктовые экраны (§6).

**Сохранённый плюс:** «любой адрес» в MVP всё равно работает через precompute: геокод → point-in-polygon (shapely) → precomputed score сектора. Live-расчёт собственной изохроны на лету подключается в v2 без смены инфры (ORS API или self-host Valhalla).

**Хостинг:** лёгкий FastAPI влезает в Render free (512 МБ) — geopandas в рантайме нет. БД: ⚠️ Render free Postgres удаляется через 30 дней → план перейти на **Supabase free** (PostGIS + pgvector, не истекает) или платный Postgres; seed позволяет восстановиться мгновенно.

---

## 2. MVP Scope (по MoSCoW)

**Core job:** _Пользователь вводит адрес/район в Brussels, выбирает сценарий (например, Family), и получает оценку 0–100 + понятное «почему» + «что улучшило бы район»._

**Walking skeleton (тонкий сквозной путь):**
`1. Ввести адрес Brussels → 2. Выбрать сценарий (Family/Senior/Remote) → 3. Увидеть score + «почему» (плюсы/минусы) + топ-3 улучшения на карте.`

### Must have (= собственно MVP)
- [ ] **Поиск по адресу Brussels** (Nominatim, кэш) → определение статистического сектора.
- [ ] **3 сценария:** Family, Senior, Remote Work (переключатель). _Почему эти три: Family/Senior — социально-полезные и data-rich; Remote Work — не существует ни у кого (дифференциатор), и хорошо ложится на open data._
- [ ] **Score-карточки** 0–100 на сектор × сценарий + percentile-ранг по Brussels.
- [ ] **Экран «почему»** — структурированные плюсы/минусы из breakdown («+8 playgrounds в 15 мин», «− слабое покрытие аптек»). Шаблонный нарратив (без LLM).
- [ ] **Карта** (MapLibre + OpenFreeMap) с границей сектора + POI-слоями (школы, парки, аптеки, транспорт).
- [ ] **Топ-3 «как улучшить»** на сектор × сценарий с количественной дельтой («+1 аптека в 800 м → +9 к Senior»).
- [ ] **Disclosure-блок:** покрытие данных, «потенциальный доступ, не реальный», дата OSM-снимка.
- [ ] **Оффлайн Python-пайплайн**, считающий всё вышеперечисленное в seed БД.

### Should have (следующее, не сейчас)
- [ ] **Trade-off сравнение** двух секторов с объяснением («A: +школы, −20 мин до работы»).
- [ ] **LLM-нарратив** (Claude) вместо шаблонного «почему» — генерит человеческую прозу из breakdown.
- [ ] Ещё сценарии: Home (общий), Student.
- [ ] **Управляемые веса** (слайдеры приоритетов как у mylocationscore).

### Could have (приятно, позже)
- [ ] Сценарии Work, Pet, Small Business.
- [ ] Загрузка CSV для public-sector (свои amenities/жалобы).
- [ ] Воздух (OpenAQ) и шум (END maps) как слои.
- [ ] Экспортируемый PDF-отчёт по району.
- [ ] Второй город (London).

### Won't have (явно отложено)
- ❌ **Живая оценка произвольной точки** (своя изохрона на лету) — отложено: precompute по секторам покрывает 95% ценности за 5% сложности; live-движок — это v2 FastAPI.
- ❌ **RAG над policy-PDF** — отложено: высокая ценность для public-sector, но не тестирует основную ставку; добавляется без новой инфры (pgvector) позже.
- ❌ **Auth / аккаунты / сохранённые поиски** — отложено: MVP анонимный.
- ❌ **PostGIS** — отложено: для MVP геометрия секторов мала, point-in-polygon делается в JS (turf) или предвычисленным lookup; PostGIS не нужен.
- ❌ **Мульти-город из коробки** — отложено: один город доказывает ставку.

### RICE для спорных позиций

| Фича | Reach | Impact | Confidence | Effort | Score | Вердикт |
|---|---|---|---|---|---|---|
| «Как улучшить» | 3 | 3 | 0.8 | 2 | 3.6 | **Must** (уникальный ров) |
| LLM-нарратив | 3 | 2 | 0.7 | 1 | 4.2 | Should (дёшево, но шаблон в MVP достаточен) |
| Trade-off сравнение | 2 | 3 | 0.7 | 2 | 2.1 | Should |
| Управляемые веса | 2 | 2 | 0.6 | 2 | 1.2 | Should |
| Live произвольная точка | 3 | 2 | 0.5 | 3 | 1.0 | Won't (v2) |
| RAG policy | 1 | 3 | 0.5 | 3 | 0.5 | Won't (v2) |

**Cut rationale:** Ставка MVP — что **«почему + как улучшить» на переключаемых сценариях** бьёт голую цифру у всех конкурентов. Live-движок, RAG и мульти-город ценны, но не тестируют эту ставку — они ждут v2.

---

## 3. Методология scoring (ядро доверия)

Это то, что отделяет нас от «оценки по звёздочкам». Считается **оффлайн**, на сектор.

### 3.1 Бэкбон: network nearest-distance + distance-decay

Для каждого статистического сектора (берём центроид + при желании несколько точек выборки внутри):

1. **Walk-time до ближайшего** объекта каждой категории по уличному графу (OSMnx + NetworkX), скорость ~80 м/мин. _Не прямые буферы — они завышают доступность на 25–40%._
2. **Decay-функция** на категорию: полный балл если ≤5 мин, линейный спад до 0 на категорийном максимуме (например, аптека 15 мин, школа 20 мин, парк 10 мин).
3. **Abundance-категории** (кафе, магазины): считаем несколько объектов в радиусе с убывающей отдачей (как Walk Score для ресторанов).
4. **Sub-score категории** → 0–100.

### 3.2 Композит сценария = взвешенная сумма sub-scores

Веса (из дока, уточнённые; **публикуются в UI** — это senior-сигнал):

```
Family Fit =
  25% школы / childcare
  20% парки + playgrounds
  15% безопасная пешеходная среда (road exposure inverse)
  15% healthcare / аптеки
  10% транспорт
  10% зелёные зоны
   5% повседневные amenities

Senior Fit =
  25% healthcare / аптеки
  20% транспорт
  20% rest stops / лавки / парки
  15% продукты / повседневное
  10% низкая road exposure
  10% walkability

Remote Work Fit =
  25% тишина / низкая road exposure
  20% кафе / библиотеки / коворкинги
  20% парки для перерывов
  15% повседневные amenities
  10% транспорт
  10% walkability
```

### 3.3 Нормализация (показываем оба)

- **Percentile-ранг** против всех секторов Brussels — для comparison-вью («топ 10% по зелени»).
- **Абсолютные пороги** (15-мин стандарт) — для underserved-флага («не проходит порог 800 м до аптеки»).
- Внутри композита — z-scores для объединения разнородных индикаторов; наружу показываем percentile.

### 3.4 Underserved-модуль (E2SFCA) — для «улучшить» и public-sector

Объединяет supply (amenities) со spros (население сектора из Statbel) → выявляет demand-pressure разрывы (много людей, мало объектов). Академически защищённое определение «underserved». Это база для improvement-предложений.

### 3.5 «Как улучшить» (количественно)

Для категорий с наибольшим взвешенным дефицитом:
1. Симулируем добавление ближайшего недостающего объекта (виртуальный POI в центроиде разрыва).
2. Пересчитываем sub-score и композит.
3. Дельта → ранжированный список: _«+1 аптека в 800 м → Senior 62→71 (+9)»_.

### 3.6 Disclosure (first-class)

Per-sector индикатор покрытия OSM (cross-check с UrbIS), явное заявление про MAUP (показываем на одной единице — статистический сектор), буфер extract на 2–3 км за границу города (edge effects), оговорка «потенциальный доступ, не реальный; приватные amenities и часы работы исключены».

---

## 4. Data model (SQLModel)

Определяется как SQLModel/SQLAlchemy-классы. Тот же `DATABASE_URL` → SQLite локально / Postgres на Render (`create_engine`, как было в `db.js`). Схема (псевдокод полей):

```text
// Sector — статистический сектор Brussels
Sector {
  id: STRING (pk)            // Statbel sector code
  name: STRING
  municipality: STRING
  population: INTEGER
  geometry: JSON             // GeoJSON polygon (для карты + point-in-polygon)
  centroidLat: FLOAT
  centroidLng: FLOAT
  osmCoverage: FLOAT         // 0..1 индикатор полноты (disclosure)
}

// SectorScore — оценка сектора по сценарию
SectorScore {
  id: INTEGER (pk)
  sectorId: STRING (fk)
  scenario: STRING           // 'family' | 'senior' | 'remote'
  score: INTEGER             // 0..100
  percentile: INTEGER        // 0..100 ранг по Brussels
  breakdown: JSON            // { schools: 84, parks: 71, pharmacies: 55, ... }
  pros: JSON                 // ['+8 playgrounds в 15 мин', ...]
  cons: JSON                 // ['− слабое покрытие аптек', ...]
}

// Poi — точки для слоёв карты
Poi {
  id: INTEGER (pk)
  sectorId: STRING (fk, nullable)
  category: STRING           // 'school'|'park'|'pharmacy'|'transit'|...
  name: STRING
  lat: FLOAT
  lng: FLOAT
}

// Improvement — предложения «как улучшить»
Improvement {
  id: INTEGER (pk)
  sectorId: STRING (fk)
  scenario: STRING
  rank: INTEGER
  title: STRING              // '+1 аптека в 800 м'
  category: STRING
  scoreDelta: INTEGER        // +9
  fromScore: INTEGER         // 62
  toScore: INTEGER           // 71
  suggestedLat: FLOAT        // где разрыв (для карты)
  suggestedLng: FLOAT
}
```

Выбор диалекта по `DATABASE_URL` переезжает из `db.js` в Python (`create_engine`): `postgres://` → Postgres (+ `connect_args` для SSL на Render), иначе SQLite. Geometry хранится как GeoJSON в JSON-колонке (point-in-polygon делает shapely в рантайме); PostGIS для MVP не нужен.

---

## 5. API (FastAPI)

```text
GET  /api/health                      → { status, db }              (порт логики из Express)
GET  /api/hello                       → демо                         (можно убрать)

GET  /api/score?address=<str>&scenario=<str>
     → геокод (Nominatim, кэш) → point-in-polygon → сектор
     → { sector, score, percentile, breakdown, pros, cons, improvements[], disclosure }

GET  /api/sector/:id?scenario=<str>   → то же по id сектора (клик по карте)

GET  /api/sectors.geojson             → FeatureCollection всех секторов
     (для choropleth-слоя; score можно подмешать ?scenario=)

GET  /api/pois?sectorId=<id>&categories=school,park,pharmacy
     → точки для слоёв карты

GET  /api/compare?a=<id>&b=<id>&scenario=<str>   (Should)
     → { a, b, deltas[], tradeoffNarrative }

POST /api/explain                     (Should, Claude)
     body: { breakdown, scenario } → { narrative }   // человеческая проза
```

**Геокодинг:** Nominatim, 1 req/s, валидный User-Agent, **кэшировать каждый результат в БД** (таблица `GeocodeCache { query, lat, lng }`), чтобы не долбить лимит.

**Point-in-polygon:** `shapely` (`Point.within(shape(sector.geometry))`) против `Sector.geometry` — PostGIS не нужен для ~700 секторов.

---

## 6. Frontend (React/Vite)

Существующий `App.jsx` — smoke-test, заменяется на продуктовые экраны. Без routing-библиотеки (MVP — один экран со state), как и в ограничениях шаблона.

**Экраны/компоненты:**
1. **SearchBar** — ввод адреса + переключатель сценария (Family/Senior/Remote).
2. **ScoreCards** — крупный композит + percentile + breakdown по категориям (radar или bars).
3. **WhyPanel** — плюсы (зелёные) / минусы (красные) из `pros`/`cons`.
4. **ImprovementsList** — топ-3 с дельтами, клик → подсветка точки на карте.
5. **MapView** — MapLibre GL + OpenFreeMap (keyless, без квоты). Слои: граница сектора (choropleth по score), POI-маркеры по категориям, suggested-точки улучшений. Атрибуция OSM обязательна.
6. **DisclosureFooter** — покрытие/уверенность/дата данных/методология (ссылки на Nature Cities, Walk Score).

**Карта:** `maplibre-gl` + стиль OpenFreeMap. Для dev допустимо Leaflet + OSM raster, но в проде — OpenFreeMap (политика OSM tiles запрещает прод-использование public tile-сервера).

---

## 7. AI / нарратив слой

> Детальный дизайн (промпты, схемы, анти-галлюцинация, стоимости) — в `BUILD_SPEC.md` Часть 3.

**Ключевое:** нарративы **предвычисляются оффлайн** (как и scores) → `POST /api/explain` = **чтение из БД ($0 рантайм-LLM, ~5 мс)**. Контракт `{narrative, highlights[]}` фиксирован, источник сменяем.

- **MVP:** deterministic **template** (правила над breakdown) — $0, byte-stable, + **вечный fallback** при провале grounding-валидатора.
- **Fast-follow:** **`claude-haiku-4-5` оффлайн Batch** (−50%), grounded строго через массив `facts[]` (модель не считает, только связывает). **~724×3 ≈ 2172 нарратива ≈ $2.5 за полную регенерацию Brussels**, $0 рантайм. Sonnet 4.6 — только если Haiku читается плоско.
- **Анти-галлюцинация:** structured output (GA) + валидатор number/noun-whitelist + resident-language denylist до сохранения; «оцениваем СРЕДУ, не жителей». Ключ через env, server-only.
- **v2 (Won't сейчас):** RAG над city policy PDF — **pgvector на той же Postgres/Supabase**, оффлайн-эмбеддинги (мультиязычная FR/NL), Claude с обязательными цитатами chunk'ов. Никакой новой инфры.

---

## 8. Деплой (после миграции на FastAPI)

- **`render.yaml`** — `runtime: docker` остаётся; имена `ai-workshop-db` / `ai-workshop-web` не трогаем (переименование плодит дубли сервисов). `healthCheckPath: /api/health` работает как раньше.
- **`Dockerfile`** — **переписываем** на 2 stage: (1) `node:20-alpine` собирает `frontend/dist`; (2) `python:3.12-slim` ставит `requirements.txt`, копирует `backend/` + `frontend/dist` → запускает `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. FastAPI отдаёт фронт через `StaticFiles`.
- **Сидинг seed в Postgres:** `backend/seed.py` (или alembic data-migration), запускать как Render **build/release step** или вручную через `render run`. Seed-файл (JSON из Python-пайплайна) коммитится в репо (~700 секторов × 3 сценария — маленький).
- **Env:** `ANTHROPIC_API_KEY` (когда подключим Claude), `NOMINATIM_USER_AGENT`.
- ⚠️ **Render free Postgres удаляется через 30 дней** (изменилось с 90). Для продукта — перейти на платный Postgres или **Supabase free** (PostGIS + pgvector, не истекает, только пауза через 7 дней неактивности). Seed позволяет быстро восстановиться.
- **Лёгкий рантайм важен:** без geopandas/osmnx FastAPI влезает в Render free 512 МБ. Эти либы — только в `backend/pipeline/` (оффлайн), не в `requirements.txt` рантайма (отдельный `requirements-pipeline.txt`).

---

## 9. Роадмап (соло-темп, ~6–8 недель)

| Неделя | Веха | Результат |
|---|---|---|
| **0** | Миграция | Express → FastAPI каркас: `backend/app/main.py`, SQLModel-модели, `create_engine` по `DATABASE_URL`, `/api/health` портирован, Dockerfile на `python:3.12-slim`, фронт через StaticFiles. Локально и на Render зелёный. |
| **1** | Данные | Geofabrik Belgium extract, Statbel секторы Brussels загружены, OSM POI отфильтрованы. `backend/pipeline/` стоит, считает walk-time на сектор. |
| **2** | Scoring | Family-сценарий end-to-end в пайплайне: breakdown + композит + percentile. Seed JSON генерится. |
| **3** | Backend | Seed в БД (`seed.py`). `/api/score` (геокод → shapely point-in-polygon → сектор → score). |
| **4** | Frontend каркас | SearchBar + ScoreCards + WhyPanel на реальных данных Family. MapLibre с границей сектора. |
| **5** | Improve + сценарии | E2SFCA underserved + improvement-дельты в пайплайне. Добавлены Senior + Remote. ImprovementsList на карте. |
| **6** | Disclosure + полировка | Disclosure-блок, POI-слои, percentile-вью, методология-страница. Деплой на Render. |
| **7–8** | Should-слой (опц.) | Trade-off сравнение + Claude-нарратив. Бета-юзеры, обратная связь. |

**Definition of Done для MVP:** любой может ввести адрес Brussels, переключить 3 сценария, увидеть score + «почему» + топ-3 улучшения на карте, с disclosure — на бесплатном Render-деплое.

---

## 10. Риски и как закрыть

| Риск | Влияние | Закрытие |
|---|---|---|
| **OSM неполнота в Brussels** (~73% overlap зелени с UrbIS) | оценки врут | cross-check с UrbIS-полигонами; показывать osmCoverage; «потенциальный доступ» disclaimer |
| **MAUP** (результат зависит от единицы) | критика методологии | считать на одной единице (статистический сектор), заявить явно; v2 — показать на 2 масштабах |
| **Edge effects** на границе города | заниженные оценки на краю | буфер extract на 2–3 км за границу перед расчётом |
| **Render free Postgres истекает (30 дн)** | потеря БД | seed-файл в репо для мгновенного восстановления; план перехода на Supabase free |
| **Nominatim rate-limit** (1 req/s) | блок геокодинга | агрессивный кэш в БД; User-Agent; LocationIQ (5k/день) как апгрейд |
| **Скоуп расползается на 8 сценариев** | не доделать ни одного | жёсткий MoSCoW: 3 сценария, глубина > ширина |
| **«Как улучшить» выглядит наивно** | теряем главный ров | привязать к E2SFCA demand-pressure, не к «просто добавь POI»; показывать дельту честно |

---

## 11. Метрики успеха MVP

- **Активация:** % сессий, дошедших до экрана «почему» (не просто score).
- **Глубина:** среднее число переключений сценария за сессию (доказывает, что persona-switch ценен).
- **Вовлечённость в ров:** % сессий, открывших improvement-список / клик по suggested-точке.
- **Качественно:** 5–10 интервью релокантов/семей Brussels — «помогло ли это решить, где жить?».
- **B2B-сигнал:** хоть один контакт от council/девелопера/агента после demo (валидирует монетизацию).

---

## 12. Связь с исходным видением

Этот план — **MVP-срез** большого видения из `neighbourhood_fit_score_description.md`. Отложенное (live-движок, RAG над policy, Public Realm Planner, 8 сценариев, мульти-город, экспортные отчёты, CSV-загрузка) **не выброшено** — оно в Should/Could/Won't и секвенировано на после того, как MVP докажет ставку «почему + улучшить бьёт голую цифру». Senior-сигналы из дока (прозрачная модель, disclosure, before/after, public-sector workflow) встроены в MVP с первого дня — именно они делают продукт «decision-support system», а не «оценкой по звёздочкам».
