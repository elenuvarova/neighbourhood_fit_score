# Neighbourhood Fit Score — Юридический аудит и compliance

_Дата: 2026-06-01. Источник: ресерч-агент (legal/licensing). **Это не юридическая консультация** — два пункта 🔴 требуют юриста перед запуском. Для коммерческого EU-продукта (freemium + B2B API/embed)._

---

## 0. Два launch-gate и одна операционная правка

- 🔴 **ODbL share-alike на B2B-слое** — продавать/отдавать **сырые OSM POI-данные** = передача Derivative Database → копилефт. **Scores/нарративы = Produced Work** (только атрибуция, коммерция ОК, НЕ форсит open). → Платный API отдаёт только вычисленные значения, не сырой POI-датасет.
- 🔴 **Fair-housing оптика** — низкие оценки коррелируют с мигрантскими/бедными секторами → риск обвинений в redlining. → Только environment-инпуты, anti-discriminatory-use clause в B2B.
- 🟠 **Nominatim public = нарушение ToS для коммерции** → мигрировать на self-host / коммерческий / UrbIS-адреса (CC0).

---

## 1. Лицензии источников (можно ли коммерчески + обязательства)

| Источник | Лицензия | Коммерция? | Атрибуция | Share-alike |
|---|---|---|---|---|
| **OpenStreetMap** | ODbL 1.0 | ✅ | ✅ «© OpenStreetMap contributors» + ссылка ODbL | ⚠️ на *базе данных*, см. 1.1 |
| **Statbel** (секторы+население) | CC BY 4.0 | ✅ (явно) | ✅ «Statbel» | ❌ |
| **STIB/MIVB GTFS** | STIB Open Data terms | ✅ (явно) | ✅ + цвета линий/лого если показываешь | ❌ |
| **UrbIS** (datastore.brussels) | **CC0 1.0** | ✅ без условий | не требуется (courtesy) | ❌ |
| **Nominatim** (public) | данные ODbL; **service policy** | 🔴 фактически НЕТ на public | per OSM | n/a |
| **OpenFreeMap** (public) | MIT код; OSM/OpenMapTiles данные | ✅ без rate-limit | ✅ (авто в MapLibre) | n/a |

### 1.1 ODbL — центральный вопрос (заражает ли share-alike наши scores?)

**Короткий ответ: НЕТ — scores и коммерческий продукт НЕ форсятся в open, ЕСЛИ публикуем только scores/нарративы/отрисованную карту (Produced Work) и НЕ раздаём публично исходную POI-базу как переиспользуемую БД.** Share-alike привязан к *Derivative Database* и триггерится только при **публичной передаче** этой БД (conveyance-gated, не use-gated).

| Что отдаём | ODbL-класс | Обязательство |
|---|---|---|
| Scores 0–100, percentile, breakdown, pros/cons, нарративы | **Produced Work** | только атрибуция ✅ |
| Отрисованная карта / choropleth в UI | **Produced Work** | только атрибуция ✅ |
| Внутренняя `pois`/`sector_amenities` БД (server-side, не отдаётся) | Derivative DB, **не conveyed** | нет обязательств ✅ |
| 🔴 B2B API, отдающий сырые POI-списки/координаты | Derivative DB **conveyed** | share-alike: получатель под ODbL |
| 🔴 «Data licensing» tier с продажей POI-датасета | conveyance Derivative DB | ODbL share-alike |

**Правила для нашего B2B-плана:**
1. **Платный API = только Produced-Work payload** (scores, percentile, breakdown, нарративы, improve-дельты). ✅ Совпадает с precomputed-моделью BUILD_SPEC.
2. **`GET /api/pois` — точка риска.** Если в платном/embed продукте отдаёт существенные OSM POI-данные → conveyance Derivative DB. Митигейт: (a) POI-маркеры только как внутренний слой отрисованной карты (Produced Work), не как data-feed; (b) если надо отдавать POI — лицензировать этот срез под ODbL+атрибуция (это **не** заражает scores).

**Атрибуция (обязательно):** «© OpenStreetMap contributors» читаемо без взаимодействия, рядом с produced work, + ссылка на `openstreetmap.org/copyright`. На карте — авто в MapLibre; **+ на любой score-странице/отчёте**, чьи числа существенно из OSM.

> ⚠️ Юрист: mainstream-чтение (scores = Produced Work) надёжно, но **перед запуском data-licensing tier взять короткое заключение**, что конкретный API-payload — Produced Work, не conveyed Derivative DB.

### 1.2–1.6 кратко

- **Statbel CC BY 4.0** — коммерция явно разрешена, только атрибуция «Statbel». **STIB** — коммерция ОК, атрибуция + не вредить репутации + цвета/лого линий если показываешь (подтвердить строку атрибуции [VERIFY]). **UrbIS CC0** — самый низкий риск, атрибуция не требуется.
- 🔴 **Nominatim public:** max 1 req/s, кэшировать, явно «commercial → switch to commercial provider or self-host». Оффлайн-геокодинг 724 точек — ок. **Live user-search на public Nominatim в коммерции = нарушение.** → self-host Nominatim / коммерческий (LocationIQ/Geoapify) / **UrbIS Addresses (CC0, Brussels-полный) — лучший фит** для адрес→точка в Brussels.
- **OpenFreeMap** — free, без ключа/лимита, коммерция ОК, self-hostable (MIT). Атрибуция «OpenFreeMap © OpenMapTiles Data from OpenStreetMap». Для коммерции — план self-host (убрать зависимость от донат-инстанса).

---

## 2. GDPR (лёгкий, потому что scoring агрегатный + нарративы оффлайн)

**Два плана данных — держать раздельно:**
- **План A (scoring-инпуты):** агрегат на уровне сектора, без индивидов (Statbel pop, OSM POI, GTFS) — **не персональные данные**, нет обязательств на пайплайн.
- **План B (юзеры):** waitlist-email, аккаунты, поисковые запросы с адресами — **персональные данные**, продукт = controller.

**Адрес-поиск = персональные данные?** Часто да (адрес+личность — идентификатор; геокод точки может выделить домохозяйство). Трактовать searched-адреса+геокоды как PII когда связаны с user/session/IP.

**Минимальный compliant-сетап:**
1. **Lawful basis:** consent для маркетинга/waitlist; contract/legitimate-interest для выдачи запрошенного score (LIA на логирование).
2. **Минимизация (сильнейший митигейт):** **не хранить адреса юзеров, привязанные к личности.** Кэшировать геокоды только по нормализованной строке адреса (`GeocodeCache {query, lat, lng}`) **без связи с user/IP** → это операционный lookup, не PII-хранилище. MVP анонимный → не логировать полные адреса против IP.
3. **Privacy policy** (до сбора любого email): controller, цели, basis, retention, субпроцессоры, права, жалоба в Бельгийский DPA (APD/GBA).
4. **Cookie/consent banner:** при non-essential cookies/аналитике/embed. Cookieless MVP (OpenFreeMap без cookies, без 3rd-party аналитики) может избежать баннера; embed на чужих сайтах + любые пиксели → нужен consent-mode.
5. **DPA/субпроцессоры:** Render, **Supabase (EU-регион)**, Anthropic, геокодер. EU-регион + SCC.
6. **Anthropic — благоприятно + архитектура почти снимает вопрос:** commercial API **не тренируется** на инпутах по умолчанию, retention 7 дней (удаляемо), ZDR доступен, DPA есть. **Все 2172 нарратива генерятся ОФФЛАЙН из breakdown без PII → user-PII никогда не доходит до Anthropic в рантайме.** Держать так (не класть адреса юзеров в промпты).

---

## 3. Defamation / property-value / «trade libel»

Риск реален но low-to-moderate, управляем фреймингом. Бельгия: defamation гражданско-уголовный + **commercial-denigration** route (cease-and-desist в Enterprise Court). **Но истина+добросовестность+легитимная инфо-цель — защита**; фактический, sourced, прозрачный score о *среде* (не о названном бизнесе/лице) защитим. Сектор — не юрлицо, которое можно опорочить; экспозиция — denigration против *идентифицируемого бизнеса* (не делаем).

**Как защищаются инкумбенты:** AreaVibes (subjective-aid + humility), Rightmove/Zoopla («general interest only, not advice, no warranty»), Niche (прозрачная методология).

**Наш фрейминг (все три инстинкта верны):** ✅ оценивать среду, не жителей (resident-language denylist уже в дизайне); ✅ объективные открытые данные + прозрачная методология; ✅ informational/no-warranty; + persona-relative «потенциальный, не реальный доступ».

**Дисклеймер (drop-in, доработать с юристом):**
> **Informational only.** Neighbourhood Fit Scores вычисляются автоматически из открытых данных (OpenStreetMap, Statbel, STIB/MIVB, UrbIS) по опубликованной методологии. Они описывают *потенциальную пешую доступность amenities* для выбранной персоны в пределах статистического района — это **не** оценка недвижимости/здания/бизнеса/жителя, **не** valuation, **не** совет покупать/арендовать/переезжать/избегать. Относительны к другим секторам Brussels, отражают срез данных на момент, могут быть неполны, исключают цену, качество, часы работы, приватные amenities, безопасность. **Без гарантий** точности; **без ответственности** за решения. Проверяйте независимо.

---

## 4. Discrimination / fair-housing оптика (риск #2)

Опасность: низкие scores коррелируют с бедными/мигрантскими секторами (Brussels ~37% не-бельгийцы, кластеризованы) → атака как steering/redlining. Прецедент: **Rotterdam Act (Wbmgp)** + **_Garib v. Netherlands_ (ECtHR 2017)** — суд не нашёл нарушения Art. 2 Prot. 4, но уклонился от Art. 14; критики — indirect discrimination против бедных/мигрантов. **Урок: area-scoring как инпут в то, КТО получает жильё — зона надзора регуляторов/NGO.**

**Митигейты (в основном уже в дизайне):**
1. **Только environment-инпуты, ноль демографии.** Amenities, walk-time, транзит, зелень, население (только для E2SFCA-спроса). **Никогда** доход/этнос/национальность/crime-by-demographic/«desirability». Документировать явно (даже без демо-инпутов scores коррелируют с доходом — признать в методологии, не прятать).
2. **Persona-relative, needs-based фрейминг** — матчинг района к потребностям юзера, противоположность ранжированию «хорошие/плохие люди».
3. **«Improve»-слой как этический north star** — E2SFCA *underserved* переформулирует низкий score в «район недообслужен, вот разрыв» — planning/equity-инструмент, не redlining. Публично на это опираться.
4. **B2B-контракт: anti-discriminatory-use clause** — запрет использовать scores для отбора/скрининга/отказа арендаторам/покупателям. Важнейшая контрактная защита B2B-канала.
5. **Никакого safety/crime/desirability-score в core** (вынесено в Could-later; добавлять — high-risk).

**EU AI Act:**
- **Запрет social scoring (Art. 5(1)(c))? НЕТ** — запрет о «natural persons or groups... based on social behaviour or personal characteristics». Мы скорим **места** из environment-данных → вне запрета (не пересекать: не добавлять resident-level scoring).
- **High-risk (Annex III)? Почти точно нет** для инфо-amenity-score (не биометрия/занятость/eligibility-решения о людях). Edge только если клиент гейтит доступ к жилью → ещё причина для anti-discriminatory clause.
- **Транспарентность (Art. 50)? Да, с 2 авг 2026** — LLM-нарративы как AI-generated публичный текст → метка **«Сгенерировано AI из открытых данных»**. Шаблонные нарративы (MVP-default) — не AI, метки не требуют.

---

## 5. Compliance-чеклист v1 (до публичного запуска)

- [ ] **OSM-атрибуция на каждой странице со scores/картой** («© OpenStreetMap contributors» + ODbL-ссылка) + Statbel, STIB, OpenFreeMap.
- [ ] **Платный API/embed отдаёт только Produced-Work** (scores/percentile/breakdown/нарративы/дельты) — **не сырой OSM POI-датасет**. (🔴 короткое заключение юриста до data-licensing tier.)
- [ ] **User-геокодинг — прочь с public Nominatim** (self-host / коммерческий / UrbIS CC0-адреса). Геокод-кэш не связан с личностью.
- [ ] **Privacy policy + ToS + методология + атрибуция** до сбора email/публики.
- [ ] **Минимизация:** не хранить searched-адреса с личностью/IP; opt-in consent; DPA с Render, Supabase (EU), Anthropic, геокодером.
- [ ] **PII вне LLM-промптов**; метка AI на нарративах.
- [ ] **Scoring — только environment-инпуты** (документировать).
- [ ] **Anti-discriminatory-use clause** в B2B-условиях.
- [ ] **Дисклеймер** «informational / no warranty / not advice / scores areas not properties or people» site-wide + per-score.

## Top-3 риска + митигейты

1. 🔴 **ODbL share-alike на B2B-слое.** Продажа/отдача OSM-derived **POI-данных** → копилефт. **Митигейт:** платный API = Produced-Work payload (scores/нарративы/дельты); POI только внутренний слой карты; если POI отдавать — срез под ODbL+атрибуция (scores не заражает). Заключение юриста до data-licensing.
2. 🔴 **Fair-housing оптика.** **Митигейт:** environment-only инпуты (документ), persona-relative + underserved/improve фрейминг, anti-discriminatory clause, без crime/safety в core, методология признаёт корреляцию с доходом.
3. 🟠 **Nominatim ToS + defamation/denigration.** **Митигейт:** уйти с public Nominatim до коммерции; «оценивать среду не жителей» (denylist в дизайне) + прозрачная методология + no-warranty + не таргетить названный бизнес.

---

_Хорошие новости: UrbIS CC0; Statbel/STIB/OpenFreeMap — коммерция с атрибуцией; GDPR лёгкий (агрегатный scoring + оффлайн-нарративы = ноль user-PII в Anthropic); AI Act social-scoring ban не применим к place-based scoring._
