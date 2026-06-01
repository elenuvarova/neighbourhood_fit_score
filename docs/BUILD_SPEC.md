# Neighbourhood Fit Score — Build Spec (инженерно-готовая)

_Дата: 2026-06-01. Источник: 3 глубоких ресерч-агента (Brussels data-spec; scoring model v1; AI-слой). Всё верифицировано по источникам июнь-2026; неподтверждённое помечено **[VERIFY]** — проверить чтением файла один раз. Дополняет `IMPLEMENTATION_PLAN.md`._

> **Контекст:** offline Python-пайплайн считает per-sector × per-scenario оценки + нарративы, сидит их в Postgres/SQLite; рантайм — лёгкий FastAPI (чистые lookups). 3 сценария MVP: Family / Senior / Remote Work. Город: Brussels-Capital, **724 статистических сектора, 19 коммун** (верифицировано).

---

## Часть 0. Три «ловушки», которые экономят дни

1. **Остановки STIB/MIVB ПЛОХО размечены в OSM** (в отличие от De Lijn/TEC). → Транзит **берём из STIB GTFS** (`data.stib-mivb.brussels`), не из OSM. OSM — только для NMBS/SNCB-вокзалов и De Lijn/TEC на краю региона.
2. **Зелёных зон НЕТ в UrbIS-Adm.** Они в UrbIS-Topo (топографические vegetation-классы) или в Bruxelles Environnement INSPIRE. → Для scoring primary-источник = **OSM `leisure=park`/`garden`/`landuse=forest`**, UrbIS — cross-check.
3. **Код муниципалитета НЕ выводится из кода сектора** (с 2019). → Всегда тащим `CD_MUNTY_REFNIS` отдельно; фильтр Brussels = `CD_MUNTY_REFNIS ∈ 21001..21019`.

---

## Часть 1. Data-спека пайплайна (Brussels)

### 1.1 Statbel статистические секторы (границы + население)

| Что | Деталь |
|---|---|
| Границы, страница | `statbel.fgov.be/en/open-data/statistical-sectors-2024` |
| Файл (рекоменд.) | `sh_statbel_statistical_sectors_31370_20240101.sqlite.zip` (SpatiaLite, geopandas читает нативно) |
| CRS | **EPSG:31370** (Belgian Lambert 72) |
| Join-ключ / PK | **`CD_SECTOR`** |
| Носители | `CD_MUNTY_REFNIS` (муниципалитет), `CD_RGN_REFNIS`, `TX_SECTOR_DESCR_FR/NL`, `MS_AREA_HA` |
| Фильтр Brussels | `CD_MUNTY_REFNIS ∈ {21001..21019}` → **724 сектора** |
| Население, страница | `statbel.fgov.be/en/open-data/population-statistical-sector-2024` |
| Файл | `OPENDATA_SECTOREN_2024.zip` (TXT, разделитель **[VERIFY]**) / `.xlsx` |
| Поля | join по `CD_SECTOR` **[VERIFY заголовок]**, население `POPULATION` **[VERIFY]** |

### 1.2 UrbIS (datastore.brussels) — опционально для MVP

- WFS: `https://geoservices-vector.irisnet.be/geoserver/urbisvector/wfs?service=WFS&version=2.0.0&request=GetCapabilities`. Лицензия **CC0**, CRS **31370**.
- Слои: `urbisvector:Region`, `:Municipalities`, `:StatisticalSectors`, `:StreetAxes` (оси дорог), `:Railways`, `:Addresses`, `:Buildings`.
- Зелень — в **UrbIS-Topo** (UUID `10ded91e-6a63-11ed-9d77-010101010000`) или Bruxelles Environnement `espaces_verts_region_bruxelloise`. Для MVP не обязателен (берём OSM).

### 1.3 OSM POI → категории (главный артефакт пайплайна)

Источник: **Geofabrik Belgium PBF** (`download.geofabrik.de/europe/belgium-latest.osm.pbf`), `osmium extract` по bbox Brussels +2 км буфер, затем `osmium tags-filter`. Матчить на nodes+ways+relations (`nwr`); ways/relations → representative point.

| Категория | OSM-теги (primary) | + fallback / заметки |
|---|---|---|
| Школы | `amenity=school` | `building=school` (если нет amenity); `isced:level` |
| Childcare/сады | `amenity=kindergarten` | + `amenity=childcare` (BE: crèche часто childcare) |
| Playgrounds | `leisure=playground` | |
| Парки/зелень | `leisure=park` | + `leisure=garden`, `landuse=forest`, `natural=wood`, `leisure=nature_reserve`; считать по **краю полигона** (walk-access), размер-гейт ≥0.5 га |
| Библиотеки | `amenity=library` | |
| Аптеки | `amenity=pharmacy` | + `healthcare=pharmacy` (дедуп <20 м) |
| Клиники/больницы | `amenity=hospital`, `amenity=clinic` | + `healthcare=hospital/clinic` |
| GP/врачи | `amenity=doctors` | + `healthcare:speciality=general`; иначе все doctors как GP-eligible |
| Супермаркеты | `shop=supermarket` | + `shop=convenience/greengrocer/bakery` (Delhaize/Colruyt/Carrefour/Okay) |
| Кафе | `amenity=cafe` | отделять от `pub/bar` |
| Рестораны | `amenity=restaurant` | + `fast_food` (опц.) |
| Коворкинги | `amenity=coworking_space` | + `office=coworking`; **разрежено в OSM → флаг low-confidence для Remote** |
| Лавки/отдых | `amenity=bench` | + `leisure=picnic_table`; очень плотно → кап вклада |
| Транзит | **из STIB GTFS, не OSM** (см. 1.4) | OSM: только NMBS `railway=station+train=yes` + De Lijn/TEC на краю |
| Велоинфра | `highway=cycleway` | + on-road `cycleway:left/right=*`, `amenity=bicycle_parking`, `bicycle_rental` (Villo!) — мерить длину + точки |
| Спорт | `leisure=sports_centre/fitness_centre/pitch/swimming_pool` | |
| Community | `amenity=community_centre` | + `social_centre` (buurthuis/maison de quartier) |
| Vets/pet | `amenity=veterinary` | + `shop=pet` |
| Dog parks | `leisure=dog_park` | разрежено |

**Дедуп-правила:** (1) healthcare amenity↔healthcare ключи мерджить по близости+имени; (2) PTv2 — оставлять `platform`, дропать `stop_position`; (3) polygon→representative_point; (4) тащить `name`/`name:fr`/`name:nl` (двуязычно).

### 1.4 STIB GTFS (транзит)

- Источник: `data.stib-mivb.brussels/explore/` (`gtfs-files-production`). Содержит `stops/routes/trips/stop_times/calendar/shapes`. Лицензия — STIB Open Data + атрибуция **[VERIFY строка]**.
- **Частота как сигнал качества:** даже без `frequencies.txt` считаем `departures/hour` на остановку = count(`stop_times` ⨝ `trips` ⨝ `calendar` на репрезентативный будний день), bucket peak (07:00–09:00) + all-day. Сильнее, чем бинарное «есть остановка» — особенно для Senior/Remote.

### 1.5 Walk-граф (osmnx)

```python
G = ox.graph_from_polygon(brussels_buffer_2km, network_type="walk", simplify=True)
for u,v,k,d in G.edges(keys=True, data=True):
    d["travel_time"] = d["length"] / 1.33   # сек; 1.33 м/с = 4.8 км/ч
```

- Буфер **+2 км** за границу Brussels (edge effects), в EPSG:31370.
- Senior-вариант: скорость **1.0 м/с** (60 м/мин).
- Per-sector: снап representative/pop-weighted точки сектора к ближайшему узлу → Dijkstra по `travel_time` → изохроны 5/10/15 мин.

### 1.6 Объёмы и хранение

- Geofabrik BE ~500 МБ → отфильтрованный Brussels POI ~десятки МБ. Граф ~150–250k рёбер. **Полный прогон < ~10–15 мин на ноутбуке.** Итоговая БД — несколько МБ.
- Таблицы: `sectors` (724), `sector_amenities` (724×~22 кат.), `sector_transit`, **`sector_scores` (724×3 = 2172 строк)** — последнюю отдаёт FastAPI чистым indexed-lookup.

---

## Часть 2. Scoring-модель v1 (точные параметры)

### 2.1 Decay-функция (плато + Gaussian, β выводится — нет произвольного knob)

```
f(t) = 1                          если t ≤ t_p
     = exp( −β · (t − t_p)² )      если t_p < t < t_max
     = 0                          если t ≥ t_max
β = 4.605 / (t_max − t_p)²        (residual ε=0.01 на t_max)
```
Пример (аптека t_p=5, t_max=15): на 8 мин f=0.66; на 12 мин f=0.10.

### 2.2 Категорийные пороги (мин) + правило подсчёта

| Категория | t_p | t_max | Правило | Якорь |
|---|---|---|---|---|
| Аптека | 5 | 15 | nearest | 15-min health |
| GP | 5 | 15 | nearest | primary-care 15-min |
| Нач. школа | 5 | 15 | nearest | CNU walkshed |
| Childcare | 5 | 15 | nearest | daily family |
| Playground | 3 | 10 | nearest | child-scale |
| Парк (≥0.5 га) | 5 | 15 | nearest | WHO 300м/ANGSt |
| Супермаркет | 5 | 15 | nearest | 15-min grocery |
| Bakery/conv. | 3 | 12 | abundance | daily |
| Транзит | 4 | 12 | nearest (взвеш. частотой) | |
| Кафе/бар | 5 | 15 | abundance | Walk Score variety |
| Ресторан | 5 | 15 | abundance | |
| Ритейл | 5 | 15 | abundance | |
| Библиотека | 7 | 20 | nearest | weekly |
| Спорт/gym | 7 | 20 | nearest | weekly |
| Больница | — | 30 | nearest | occasional |

- **nearest:** `raw_c = max_p f_c(t_p)` ∈ [0,1].
- **abundance:** top-10 POI, rank-веса геометрические r=0.75 (`Σr=3.75`), `raw_c = min(1, Σ r_k·f(t_k) / 3.75)`.

### 2.3 Веса сценариев (структура IMD; сумма 100%)

| Домен → категории | Family | Senior | Remote |
|---|---|---|---|
| Education (school, childcare) | 25 | 0 | 0 |
| Provisioning (supermkt, pharmacy, bakery) | 20 | 18* | 20 |
| Health (GP, hospital×0.3) | 12 | 35 | 5 |
| Green & play (park, playground) | 23 | 15** | 18 |
| Mobility (transit) | 10 | 17 | 13 |
| Amenity/variety (café, restaurant, retail, library, gym) | 10 | 15 | 24*** |

\*Senior provisioning = supermkt+bakery. \*\*Senior green&rest = park+library. \*\*\*Remote — café+library в приоритете. Senior использует скорость 60 м/мин (медленнее → ниже sub-scores, кодирует сниженную мобильность).

**Sensitivity (чтобы веса = «principled»):** Dirichlet-пертурбация ±5pp × 1000, репортить median Spearman ρ и rank-churn; one-at-a-time elasticity +10pp на домен.

### 2.4 Композит

1. `sub_c = 100·raw_c` (абсолютный, threshold-interpretable).
2. `RAW = Σ_c w_c·sub_c` ∈ [0,100].
3. Внутри — z-score по 724 секторам (не показываем).
4. Наружу — **percentile Hazen** `(rank−0.5)/S`, ties = средний ранг, **вселенная = только Brussels** (явно в UI).
5. **Overall Fit** = percentile от среднего трёх RAW.
6. **Гранулярность спроса:** 100 м сетка внутри сектора, pop-weighted (если нет building-pop — равномерно по residential).

### 2.5 E2SFCA (underserved-модуль; capacity-сервисы: GP, аптека, childcare, школа, супермаркет)

- Зоны/веса (Luo & Qi 2009): **0–5 мин:1.0; 5–10:0.68; 10–15:0.22**, cutoff 15 мин walk.
- Step1: `R_j = S_j / Σ P_i·W_k` (S_j = места/врачи/койки, иначе 1).
- Step2: `A_i = Σ R_j·W_k` → pop-weighted до сектора `A_sector`.
- `SPAR = A_sector / Ā` (региональное среднее=1). **Underserved (сервис)** если SPAR<0.5. **Флаг сектора** = underserved в ≥1 сервисе с весом ≥10% или ≥2 любых.
- Gap для improve = `(0.5·Ā − A_sector)·pop` единиц supply.

### 2.6 «Как улучшить» (maximal-coverage siting)

1. Топ-3 категории по `shortfall_c = w_c·(100−sub_c)`.
2. Лучшее место виртуального POI: `x* = argmax_x Σ_i P_i·[f_c(t(x,i)) − current_best_c(i)]₊` по узлам walkable-сетки (не центроид!).
3. Пересчёт `sub_c`, `RAW`, percentile; для capacity — `SPAR`.
4. Ранжировать по **Δpercentile**; показывать Δscore + «снимает underserved-флаг?».
   _«Аптека у {street} поднимет Family с 64-го до 71-го перцентиля и снимет дефицит аптек.»_

### 2.7 Валидация (чтобы методолог доверял)

- **Face-validity:** пред-регистрация ~10 известных мест Brussels (Châtelain/Saint-Gilles — топ Remote/Amenity; Watermael-Boitsfort — топ Family green) до прогона; инверсии = баг/находка.
- **External corr:** Spearman ρ с Nature Cities 15-min индексом Brussels (ожид. >0.7); с income Statbel/IBSA (умеренный, НЕ ~1 — иначе просто пере-вывели доход).
- **Sensitivity + ablation** (drop-one-category); **data-quality audit** покрытия OSM по коммунам.
- **Disclosure:** confidence-band на percentile из sensitivity; low-confidence на разрежённых данных; лист ограничений (walk-only, без качества/цены/часов, OSM-полнота, дата снимка).

---

## Часть 3. AI-слой нарратива

### 3.1 Главное решение: нарративы ПРЕДВЫЧИСЛЯЮТСЯ оффлайн

Scores precomputed → нарратив = чистая функция от breakdown → **тоже precompute**. `POST /api/explain` становится **чтением из БД: $0 рантайм-LLM, ~5 мс, нулевая поверхность отказа.** Контракт `{narrative, highlights[]}` фиксирован → источник (template / Claude offline / Claude live) сменяем без правок фронта.

### 3.2 Двухуровневый источник

| Источник | Когда | Стоимость |
|---|---|---|
| **Deterministic template** (правила над breakdown) | **MVP default + вечный fallback при провале валидации** | $0, byte-stable, snapshot-tested |
| **Claude `claude-haiku-4-5` оффлайн Batch** (−50%) | **Fast-follow** (читабельность/вариативность = сам дифференциатор) | **724×3 ≈ 2172 нарратива ≈ $2.5 за полную регенерацию Brussels**, $0 рантайм |

Sonnet 4.6 — только если слепой A/B покажет, что Haiku читается плоско. (Haiku $1/$5 за MTok, Sonnet $3/$15.)

### 3.3 Anti-hallucination (структурно, не «надеемся»)

1. Модель видит **только JSON breakdown**; каждое разрешённое число — в массиве **`facts[]`** атомарных уже-истинных утверждений. Модель **не считает**, только связывает.
2. Closed-vocabulary категорий (enum пайплайна).
3. **Structured output** (GA, `output_config.format` json_schema) → `{narrative, highlights[]}` гарантированно.
4. **Валидатор перед сохранением:** number-whitelist (любое число из output обязано быть во входе), noun/amenity-whitelist, resident-language denylist. Провал → откат на template + лог.
5. Системный промпт-правила (overriding): «только из JSON», «не считай», «оценивай СРЕДУ, не жителей», «не советуй move/buy/avoid». Промпт-блок методологии — **кэшируется**.

### 3.4 v2 RAG над policy-PDF (no new infra)

- Ingest оффлайн: chunk 500–800 ток, 15% overlap, по заголовкам; метаданные (`doc_title`, `page`, `section`, `city`, `source_url`).
- Embeddings: дешёвая мультиязычная модель (FR/NL!), **оффлайн**, в **pgvector** на той же Postgres/Supabase (`hnsw vector_cosine_ops` + B-tree по `city`).
- Retrieval: фильтр по city → top-k 5–8, seed запроса слабой категорией+improve.
- Generation: Haiku + retrieved chunks, **каждое policy-утверждение цитирует chunk** (structured `{answer, citations[]}`), иначе «нет основания».
- Это единственное место с per-call стоимостью (~$0.002–0.004/ответ) — за auth + rate-limit. **MVP лишь держит дверь открытой (быть на Supabase/Postgres).**

### 3.5 Рекоменд. AI-слой MVP

- `/api/explain` = чтение precomputed `{narrative, highlights[]}` из БД ($0, ~5 мс).
- MVP-источник = **template** (+ вечный fallback).
- Fast-follow = **Haiku 4.5 оффлайн Batch** (~$2.5 на весь Brussels), grounded по `facts[]`.
- **Structured output + grounding-валидатор** до сохранения; кэш методолог-блока.
- **На Supabase/Postgres** → v2 RAG (pgvector) без новой инфры.

---

## Что проверить чтением файла (один раз, [VERIFY])

Заголовки/разделитель `OPENDATA_SECTOREN_2024` (`CD_SECTOR`/`POPULATION`/area); region REFNIS код Brussels в вин-2024 (`04000`); имя vegetation feature-class в UrbIS-Topo; наличие `frequencies.txt` и атрибуция STIB.

---

_Дефолтные параметры v1 (все decay-пороги, веса, E2SFCA-радиусы, AI-настройки) сведены в таблицах выше — инженер реализует без доп. решений. 9 допущений scoring-модели (A1–A9: одна скорость, walk-only, 0.5 га гейт, N=10, Brussels-only percentile, равномерный pop-fallback, walk-time E2SFCA-банды, S=1 без capacity, gap-tie-break) — помечены для ревью._

---

## Часть 4. Качество данных и confidence (на этом стоит достоверность)

**Главный вывод:** OSM в Brussels силён для *публично-видимых/гражданских/транзитных* объектов и слаб для *приватных/профессиональных/микро-мебели*. У Бельгии есть отличные официальные реестры почти по каждой категории → можно вычислить per-sector coverage-ratio и обогатить OSM. **Не отгружать ни одну категорию как сырой OSM-count без confidence-слоя.**

### 4.1 Безопасность сценариев по данным: Family > Remote > Senior

- **Family — САМЫЙ безопасный** (credibility-якорь запуска). Школы (High), childcare (Low в OSM **но** реестр ONE/Kind&Gezin → enrich до High), playgrounds (Medium), парки (High public), аптеки (High), транзит (High GTFS). У каждого слабого инпута есть сильный реестр.
- **Remote — средний, один хрупкий инпут.** Кафе (Medium-High но **central density bias** — систематически завышает центр), библиотеки (validate), парки (High), продукты (Medium-High), **коворкинг (Low, разрежён, без реестра)**. → Down-weight/drop коворкинг; **явно раскрыть bias кафе**.
- **Senior — РИСКОВАННЕЙШИЙ.** **GP (Low, недомаплен)**, аптека (High), клиники (Medium), транзит (High), **лавки (Low, без реестра вообще)**, парки (High). Два из шести инпутов — худшие по OSM-покрытию. → Enrich GP из RIZIV; **лавки либо drop, либо Senior как «indicative» с видимым low-confidence band**.

### 4.2 Методология confidence (отгружать)

Per (sector × category): `coverage = matched_OSM / register_count` (spatial join ~50–100 м + fuzzy-name). Тир: **High ≥80% / Medium 50–80% / Low <50%**. **Suppression:** если coverage<40% или N<3 в реестре → исключить категорию из агрегата, аннотация «insufficient data». Сценарий несёт **худший тир включённых категорий** как headline-badge. Always-visible per-category coverage-бейдж; confidence-band на percentile; раскрыть biases (central density, приватная зелень не покрыта, GP/childcare обогащены из реестров).

### 4.3 Официальные реестры BE/Brussels (enrich/validate)

| Категория | Реестр | Источник |
|---|---|---|
| Аптеки | **FAMHP** (с Lambert-2008 координатами) | famhp.be; mirror github.com/jy95/pharmacies_BE |
| Школы FR/NL | FWB/ETNIC + Onderwijs Vlaanderen | opendata.brussels, data.gov.be (считать *implantations*) |
| Childcare | **ONE + Kind & Gezin** (заменить OSM) | opendata.brussels milieux-accueil-petite-enfance |
| GP/врачи | **INAMI/RIZIV** (profession code 1) | webappsa.riziv-inami.fgov.be/silverpages (bulk-export ограничен — [VERIFY], data-задача) |
| Больницы/клиники | FPS Public Health + RIZIV | health.belgium.be |
| Супермаркеты/коммерция | hub.brussels trade observatory + **CBE/KBO-BCE** (NACE) | analytics.brussels/open-data; economie.fgov.be CBE open data |
| Библиотеки | City of Brussels + регион FR/NL | opendata.brussels, datastore.brussels |
| Зелень | UrbIS | cirb.brussels (бенчмарк 73/85% vs OSM) |
| Транзит | **STIB GTFS** (+De Lijn/TEC/SNCB) — source of truth | data.stib-mivb.brussels |
| Контекст кварталов | IBSA Monitoring des Quartiers (~118 кварталов, 50+ индикаторов) | monitoringdesquartiers.brussels |

Прямые замеры Brussels есть только для зелени (73/85% PLOS One) и адресов (87.6% vs BEST); рейтинги lavок/коворкинга/GP — синтез европейского паттерна OSM (помечено как оценка).
