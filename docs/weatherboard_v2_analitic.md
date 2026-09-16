# ТЗ v2: Аналитический слой поверх данных погодной станции

**Объект:** SQLite `weather.db`, сырые данные с `GET /client?command=record`  
**Станция:** `192.168.8.101`, MAC `D8:BC:38:A6:E6:14`  
**Версия документа:** 2.0 (учтены замечания ревью: circular stats, math-функции SQLite, WAL, retry-safe PK, наблюдаемость, безопасность `/ask`)  

> **Актуализация (weather-5, 15.09):** свод правок v2.0→v2.1 (правки агента + отзыв DeepSeek, согласован владельцем) и roadmap этапов — `network/weather-roadmap.md` §2–3. При расхождении приоритет у roadmap.
**Исполнитель:** ИИ-агент  
**Связанные документы:**

-   `weatherstation.md` — разведка железа и API
    
-   `weather-ui-spec.md` — ТЗ на UI (отдельный документ)
    

* * *

## 0\. Принципы

1.  **Raw никогда не пересчитывается на лету.** Один раз посчитали — записали.
    
2.  **Тяжёлые окна материализуются.** Вьюхи — только для дешёвых окон.
    
3.  **Круговые величины (ветер) считаются векторно.** Никаких арифметических средних.
    
4.  **Retry-safe схема.** Уникальность ts — через индекс, не через PK.
    
5.  **Один писатель на таблицу, но WAL для параллельного чтения.**
    
6.  **Наблюдаемость важнее фич.** Станция не хранит историю — потеря замера невосстановима.
    
7.  **LLM-доступ к БД — только read-only через валидатор.**
    

* * *

## 1\. Схема БД v2

### 1.1. Raw-таблица

sql

PRAGMA journal\_mode\=WAL;
PRAGMA synchronous\=NORMAL;
PRAGMA busy\_timeout\=5000;
PRAGMA foreign\_keys\=ON;
CREATE TABLE weather (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   \-- surrogate, retry-safe
  ts INTEGER NOT NULL,                    \-- epoch UTC, ставит коллектор
  indoor\_temp\_c REAL,
  indoor\_hum\_pct REAL,
  outdoor\_temp\_c REAL,
  outdoor\_hum\_pct REAL,
  pressure\_abs\_mmhg REAL,
  pressure\_rel\_mmhg REAL,
  wind\_max\_daily\_ms REAL,
  wind\_ms REAL,
  gust\_ms REAL,
  wind\_dir\_deg REAL,
  wind\_avg2\_ms REAL,
  wind\_dir\_avg2\_deg REAL,
  wind\_avg10\_ms REAL,
  wind\_dir\_avg10\_deg REAL,
  rain\_rate\_mmh REAL,
  rain\_hour\_mm REAL,
  rain\_day\_mm REAL,
  rain\_week\_mm REAL,
  rain\_month\_mm REAL,
  rain\_year\_mm REAL,
  rain\_total\_mm REAL,
  light\_wm2 REAL,
  uvi REAL,
  battery\_raw TEXT,
  \-- L1 (заполняется коллектором, см. §3)
  dew\_point\_c REAL,
  wind\_chill\_c REAL,
  heat\_index\_c REAL,
  wind\_dir\_card TEXT,
  wind\_dir\_avg2\_card TEXT,
  wind\_dir\_avg10\_card TEXT,
  p\_delta\_mmhg REAL,
  wind\_run\_m REAL,
  rain\_rate\_calc\_mmh REAL,
  rain\_event INTEGER DEFAULT 0,
  \-- служебное
  schema\_version INTEGER DEFAULT 2
);
CREATE UNIQUE INDEX idx\_weather\_ts ON weather(ts);
CREATE INDEX idx\_weather\_id ON weather(id);

### 1.2. Метаданные

sql

CREATE TABLE wmeta (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated\_at INTEGER
);
\-- Ключи: units, station\_mac, station\_ip, tz, tz\_offset\_seconds,
\--        firmware\_version, gdd\_tbase\_c, schema\_version

### 1.3. Версионирование схемы

sql

CREATE TABLE schema\_migrations (
  version INTEGER PRIMARY KEY,
  applied\_at INTEGER NOT NULL,
  description TEXT
);
INSERT INTO schema\_migrations(version, applied\_at, description)
VALUES (2, strftime('%s','now'), 'epoch UTC, surrogate PK, L1 columns, WAL');

### 1.4. Журнал коллектора (обязательно)

sql

CREATE TABLE collector\_log (
  ts INTEGER PRIMARY KEY,                 \-- epoch UTC
  status TEXT NOT NULL,                   \-- ok | timeout | parse\_error | http\_5xx | http\_other
  latency\_ms INTEGER,
  bytes INTEGER,
  error TEXT
);
CREATE INDEX idx\_collector\_log\_status ON collector\_log(status, ts);

### 1.5. События

sql

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts\_start INTEGER NOT NULL,
  ts\_end INTEGER,
  event\_type TEXT NOT NULL,
  severity TEXT,                          \-- low | mid | high
  value REAL,
  context TEXT,                           \-- JSON-снимок полей
  acknowledged INTEGER DEFAULT 0
);
CREATE INDEX idx\_events\_type\_ts ON events(event\_type, ts\_start);
CREATE INDEX idx\_events\_open ON events(ts\_end) WHERE ts\_end IS NULL;

### 1.6. Прогнозы

sql

CREATE TABLE forecast (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issued\_at INTEGER NOT NULL,
  target\_ts INTEGER NOT NULL,
  source TEXT NOT NULL,                   \-- persistence | zambretti | sager\_day | ml | ensemble
  t\_out\_c REAL,
  p\_rel\_mmhg REAL,
  rh\_out\_pct REAL,
  wind\_ms REAL,
  rain\_mm REAL,
  confidence REAL,
  UNIQUE(issued\_at, target\_ts, source)
);
CREATE INDEX idx\_forecast\_target ON forecast(target\_ts, source);

### 1.7. LLM-сводки и инсайты

sql

CREATE TABLE summaries (
  ts INTEGER PRIMARY KEY,
  period TEXT NOT NULL,                   \-- hour | day | week | month
  text TEXT NOT NULL,
  model TEXT,
  tokens INTEGER
);
CREATE TABLE insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,                     \-- correlation | pattern | anomaly | drift
  text TEXT NOT NULL,
  confidence REAL,
  data TEXT                               \-- JSON
);

* * *

## 2\. Материализованные агрегаты

### 2.1. Часовые

sql

CREATE TABLE v\_hourly (
  hour\_epoch INTEGER PRIMARY KEY,         \-- начало часа UTC
  t\_out\_avg REAL, t\_out\_min REAL, t\_out\_max REAL,
  t\_out\_slope REAL,                       \-- линейная регрессия, Python
  t\_in\_avg REAL,
  rh\_out\_avg REAL, rh\_in\_avg REAL,
  p\_rel\_avg REAL, p\_rel\_min REAL, p\_rel\_max REAL,
  p\_tendency\_3h REAL,
  wind\_avg REAL, wind\_max REAL, gust\_max REAL,
  wind\_dir\_vector\_x REAL, wind\_dir\_vector\_y REAL,  \-- для кругового среднего
  wind\_dir\_mode TEXT,                     \-- кардинал, 16 румбов
  rain\_mm REAL, rain\_rate\_max REAL, rain\_tips INTEGER,
  solar\_avg REAL, solar\_max REAL, uvi\_max REAL,
  sun\_minutes INTEGER,                    \-- минут с light > 50
  n\_samples INTEGER                       \-- контроль полноты
);

### 2.2. Суточные

sql

CREATE TABLE v\_daily (
  day\_epoch INTEGER PRIMARY KEY,          \-- 00:00 UTC (или локальные сутки — параметр в wmeta)
  t\_out\_min REAL, t\_out\_max REAL, t\_out\_avg REAL,
  t\_out\_min\_time INTEGER, t\_out\_max\_time INTEGER,
  t\_out\_amp REAL,
  p\_min REAL, p\_max REAL, p\_amp REAL,
  wind\_avg REAL, wind\_max REAL, gust\_max REAL,
  wind\_run\_km REAL,
  wind\_dir\_mode TEXT,
  rain\_mm REAL, rain\_hours INTEGER, rain\_max\_rate REAL,
  solar\_sum\_wh\_m2 REAL,
  uvi\_max REAL, sun\_hours REAL,
  gdd\_day REAL,
  frost\_flag INTEGER, hard\_freeze\_flag INTEGER,
  fog\_flag INTEGER, thunder\_flag INTEGER,
  degree\_days\_heat REAL, degree\_days\_cool REAL,
  n\_samples INTEGER
);

### 2.3. Месячные и годовые

sql

CREATE TABLE v\_monthly (
  month\_epoch INTEGER PRIMARY KEY,        \-- 1-е число месяца, 00:00 UTC
  t\_out\_min\_abs REAL, t\_out\_max\_abs REAL, t\_out\_avg REAL,
  p\_min REAL, p\_max REAL,
  wind\_max REAL, gust\_max REAL,
  rain\_mm REAL, rain\_days INTEGER,
  gdd\_month\_sum REAL,
  frost\_days INTEGER, hot\_days INTEGER, cold\_days INTEGER,
  n\_samples INTEGER
);
CREATE TABLE v\_yearly (
  year INTEGER PRIMARY KEY,
  t\_out\_min\_abs REAL, t\_out\_max\_abs REAL, t\_out\_avg REAL,
  rain\_mm REAL, rain\_days INTEGER,
  gdd\_year\_sum REAL,
  frost\_days INTEGER, hot\_days INTEGER, cold\_days INTEGER
);

**Обновление:** cron — `v_hourly` каждый час (материализация за прошлый час), `v_daily` в 00:05 UTC, `v_monthly` 1-го числа, `v_yearly` 1 января. Инкрементально, не пересчёт всех.

* * *

## 3\. L1 — производные поля (в Python при ингесте)

Все формулы считаются **в коллекторе** перед `INSERT`, чтобы не зависеть от версии SQLite и math-функций.

### 3.1. Точка росы (Magnus)

text

α = ln(RH/100) + (17.27·T) / (237.7 + T)
Td = (237.7·α) / (17.27 − α)

Валидно при `RH > 0`. Если `RH = 0` — NULL.

### 3.2. Wind chill (JAG/TI)

Применимо при `T ≤ 10 °C AND v_kmh > 4.8`:

text

Twc = 13.12 + 0.6215·T − 11.37·v^0.16 + 0.3965·T·v^0.16

где `v = wind_ms · 3.6`. Иначе NULL.

### 3.3. Heat index (Rothfusz)

Применимо при `T ≥ 27 °C AND RH ≥ 40%`. Считать в °F, возвращать °C. Иначе NULL.

### 3.4. Кардинальное направление (16 румбов)

python

DIRS \= \["N","NNE","NE","ENE","E","ESE","SE","SSE",
        "S","SSW","SW","WSW","W","WNW","NW","NNW"\]
idx \= int((deg + 11.25) / 22.5) % 16

### 3.5. Δ давления

text

p\_delta\_mmhg = pressure\_rel\_mmhg − pressure\_abs\_mmhg

### 3.6. Wind run

text

dt\_sec = ts − prev\_ts
wind\_run\_m = wind\_ms · dt\_sec

При первом замере или `dt > 300` — NULL (не интегрировать провалы).

### 3.7. Осадки (учёт квантования типпера)

-   `rain_rate_calc_mmh = (rain_total_mm − prev_total) / dt_h`, сглаживать скользящим окном 10 мин.
    
-   `rain_event = 1`, если `rain_total_mm − prev_total_mm ≥ 0.1` (сработал типпер) в окне 10 мин.
    
-   Порог `0.2 мм/ч` из v1 **не использовать** — бессмысленен при дискретности 0.1 мм.
    

### 3.8. GDD

text

gdd = max(0, (Tmax + Tmin)/2 − Tbase)

`Tbase` из `wmeta.gdd_tbase_c` (по умолчанию 10, но для дачи — 5, параметр).

* * *

## 4\. L2 — короткие окна (вьюха)

Только окна 1h, 3h, 6h — они дёшевы. 24h/7d/30d берутся из L3-агрегатов.

sql

CREATE VIEW v\_trends\_short AS
SELECT
  w.ts,
  w.outdoor\_temp\_c \- LAG(w.outdoor\_temp\_c, 60) OVER (ORDER BY w.ts) AS t\_out\_1h\_delta,
  w.outdoor\_temp\_c \- LAG(w.outdoor\_temp\_c, 180) OVER (ORDER BY w.ts) AS t\_out\_3h\_delta,
  w.pressure\_rel\_mmhg \- LAG(w.pressure\_rel\_mmhg, 180) OVER (ORDER BY w.ts) AS p\_tendency\_3h,
  w.outdoor\_hum\_pct \- LAG(w.outdoor\_hum\_pct, 60) OVER (ORDER BY w.ts) AS rh\_out\_1h\_delta,
  w.outdoor\_temp\_c \- w.dew\_point\_c AS dew\_point\_spread,
  ...
FROM weather w;

**Требование:** SQLite ≥ 3.25 (оконные функции). Если версия ниже — та же логика в Python-материализаторе.

Классификация барической тенденции:

| `p_tendency_3h` | Класс |
| --- | --- |
| \> +1.5 | rapid\_rise |
| +0.5…+1.5 | rising |
| −0.5…+0.5 | steady |
| −1.5…−0.5 | falling |
| < −1.5 | rapid\_fall |

* * *

## 5\. Круговые величины (ветер) — обязательные правила

**Запрещено** арифметическое усреднение направлений. Все операции — векторные.

### 5.1. Среднее направление

python

x \= sum(sin(radians(θᵢ)) for θᵢ in window)
y \= sum(cos(radians(θᵢ)) for θᵢ in window)
θ\_avg \= degrees(atan2(x, y)) % 360

### 5.2. Мода (mode) по румбам

Биннинг по 16 румбам, румб с максимальным числом попаданий. При равенстве — первый по часовой.

### 5.3. Поворот ветра (rotation)

Кратчайшая дуга:

python

d \= ((θ₂ − θ₁ + 540) % 360) − 180   \# результат в \[−180, +180\]

### 5.4. Где применяется

-   `v_hourly.wind_dir_vector_x/y` — хранится для последующих агрегатов.
    
-   `v_hourly.wind_dir_mode` — кардинал.
    
-   `v_daily.wind_dir_mode` — считается из `v_hourly` (mode от mode, или через суммарные векторы).
    

* * *

## 6\. L4 — события

### 6.1. Типы

| event\_type | Условие | Severity |
| --- | --- | --- |
| `FROST` | `outdoor_temp_c ≤ 0` | high |
| `HARD_FREEZE` | `outdoor_temp_c ≤ −10` | high |
| `FOG` | `dew_point_spread < 1 AND rh_out > 95` | mid |
| `STORM_APPROACH` | `p_tendency_3h < −1.5 AND wind_max_1h > 8` | high |
| `THUNDER_RISK` | `p_tendency_3h < −1.0 AND t_out > 20 AND rh > 70` | mid |
| `HEAVY_RAIN` | `rain_1h > 5` | mid |
| `DOWNPOUR` | `rain_rate_calc_mmh > 20` | high |
| `STRONG_WIND` | `gust_max_1h > 15` | mid |
| `HURRICANE_GUST` | `gust > 25` | high |
| `HEATWAVE` | `t_out_24h_max > 35` | high |
| `DRY_SPELL` | `rain_7d = 0 AND solar_7d > mean` | low |
| `CALM` | `wind_max_24h < 1` | low |
| `RAPID_TEMP_DROP` | `t_out_1h_delta < −5` | mid |
| `RAPID_TEMP_RISE` | `t_out_1h_delta > +5` | mid |
| `PRESSURE_CRASH` | `p_tendency_3h < −3` | high |
| `SENSOR_STUCK` | вариация за 6h < ε для живых полей | mid |
| `SENSOR_DRIFT` | расхождение со счётчиками станции > 5% | mid |
| `SENSOR_MISSING` | нет замеров > 10 мин | mid |
| `BATTERY_LOW` | `battery_raw` не матчит `ok` (regex, case-insensitive) | mid |
| `SENSOR_ANOMALY` | флаг из AI-детектора (§9) | varies |

### 6.2. Особые правила

-   `SENSOR_STUCK` — **только для «живых» полей** (`outdoor_temp_c`, `rh_out`, `wind_ms`, `light_wm2`). Не применять к `rain_total_mm`, `rain_year_mm`, `uvi` — они статичны зимой/ночью по природе.
    
-   `SENSOR_DRIFT` — раз в сутки сверять `rain_day/week/month/year` со своими агрегатами. Расхождение > 5% → событие с обоими значениями в `context`.
    
-   `BATTERY_LOW` — хранить `battery_raw` целиком, сравнивать через `re.search(r'\bok\b', raw, re.I)`. Точное сравнение строк запрещено (хрупко).
    

* * *

## 7\. L5 — классические прогнозы

### 7.1. Persistence

Прогноз = последнее значение. Baseline для оценки ML.

### 7.2. Zambretti

Вход: `p_tendency_3h`, `wind_dir_card`, сезон. Выход: одна из ~25 формулировок. Работает 24/7.

### 7.3. Sager

Работает **только днём** (нужна облачность, которую мы аппроксимируем через `solar`). Ночью — пометить `applicable=false`, не выдавать прогноз. Альтернатива ночью — Zambretti + persistence.

### 7.4. Short-term trend extrapolation

Линейная регрессия по 6h окну для T и P, прогноз на 1–3 ч. Считать в Python при материализации.

* * *

## 8\. L6 — AI-слой

### 8.1. ML-прогноз (P3, через 30+ дней истории)

-   Модель: LightGBM или Prophet (CPU-only).
    
-   Вход: 7–30 дней, окно 24–72 ч.
    
-   Выход: почасовой прогноз `t_out`, `p_rel`, `rh_out`, `wind_ms`, `rain_1h` на 1–24 ч.
    
-   Метрики: MAE, RMSE, сравнение с persistence.
    
-   Куда: `forecast` с `source='ml'`.
    

### 8.2. Детектор аномалий

-   Isolation Forest на всех числовых полях + L1.
    
-   Выход: `anomaly_score ∈ [0,1]`.
    
-   Куда: колонка в отдельной `anomalies` \+ событие `SENSOR_ANOMALY` при score > порога.
    

### 8.3. NLP-сводки (P2, можно раньше ML)

Раз в день LLM генерирует текст по `v_daily` + `events` + `forecast`.  
Пример:

> «Сегодня: Tmin −2.3 °C в 06:12, Tmax +8.1 °C в 14:40. Дождь 2.4 мм. Ветер СЗ, порывы 7.8 м/с. Давление падало 762→758 мм рт.ст. — к вечеру вероятен дождь.»

Куда: `summaries`.

### 8.4. Инсайты (корреляции)

Раз в неделю — корреляционный анализ по 30+ дням. Кандидаты:

-   `p_tendency_3h` → `rain_1h` через 6–8 ч.
    
-   `wind_dir` → `rh_out` через 2 ч.
    
-   `solar_sum` → `t_out` на следующий день.
    

Куда: `insights`.

* * *

## 9\. REST API и `/ask` (безопасность)

### 9.1. Эндпоинты

text

GET  /now                → L1+L2 для последнего замера
GET  /history?from=&to=  → raw + derived
GET  /daily?from=&to=    → v\_daily
GET  /events?type=&from= → events
GET  /forecast           → forecast
GET  /summary?period=day → summaries.text
GET  /insights           → insights
POST /ask                → LLM + SQL

### 9.2. Требования к `/ask`

-   SQLite-коннект в **read-only**: `file:weather.db?mode=ro`.
    
-   `PRAGMA query_only=ON`.
    
-   Whitelist таблиц и вьюх: `weather`, `v_hourly`, `v_daily`, `v_monthly`, `events`, `forecast`, `summaries`, `insights`, `wmeta`. **Запрещены** `sqlite_master`, любые `ATTACH`, `PRAGMA` кроме `query_only`.
    
-   Принудительный `LIMIT 10000`.
    
-   Таймаут запроса — 5 с.
    
-   Логирование сгенерированного SQL в отдельную таблицу `ask_log` для аудита.
    
-   Парсер-валидатор SQL перед выполнением (никаких `;`, `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ATTACH`).
    

### 9.3. Аутентификация

-   LAN-only + basic auth на обратном прокси (nginx).
    
-   `/ask` — отдельный токен, никогда не выставлять наружу.
    

* * *

## 10\. Наблюдаемость и отказоустойчивость

### 10.1. Heartbeat и метрики

-   Таблица `collector_log` — каждая попытка опроса (успех/ошибка).
    
-   Prometheus-метрики (опционально): `weather_gap_seconds`, `weather_consecutive_errors`, `weather_db_size_bytes`, `weather_last_success_ts`.
    

### 10.2. systemd-юнит

ini

\[Unit\]
Description\=Weather station collector
After\=network-online.target
\[Service\]
Type\=simple
ExecStart\=/usr/bin/python3 /home/vitele/weather-dash/collector.py
Restart\=always
RestartSec\=10
WatchdogSec\=180
StandardOutput\=journal
StandardError\=journal
\[Install\]
WantedBy\=multi-user.target

### 10.3. Бэкапы

bash

sqlite3 weather.db "VACUUM INTO 'backup/weather-$(date +%F).db'"

-   Раз в сутки, cron.
    
-   Ротация: 30 дней.
    
-   rsync на NAS.
    
-   **Критично для RPi:** SD-карта умирает, данные невосстановимы без бэкапа.
    

### 10.4. Обнаружение провалов

`SENSOR_MISSING` генерируется, если gap между замерами > 10 мин. Запись в `events`. Алерт в Telegram (опционально).

* * *

## 11\. Миграция с v1

1.  Создать `weather_new` по схеме v2.
    
2.  Перенести строки из старой `weather`, преобразовав `ts`:
    
    python
    
    from datetime import datetime, timezone
    ts\_epoch \= int(datetime.fromisoformat(old\_ts)
                   .replace(tzinfo\=timezone.utc).timestamp())
    
    Если старые записи в локальной TZ без суффикса — использовать `wmeta.tz_offset_seconds` из настроек станции.
    
3.  Пересчитать L1-поля для старых записей (batch-скрипт).
    
4.  `ALTER TABLE weather RENAME TO weather_v1_backup`.
    
5.  `ALTER TABLE weather_new RENAME TO weather`.
    
6.  Записать `schema_migrations(version=2)`.
    

* * *

## 12\. Приоритеты внедрения

| Приоритет | Задача | Сложность | Ценность |
| --- | --- | --- | --- |
| **P0** | Schema v2 + миграция накопленных | низкая | критично |
| **P0** | Коллектор v2 (epoch, L1, collector\_log, systemd) | низкая | критично |
| **P0** | P0-события (FROST, RAIN, WIND, SENSOR\_\*, BATTERY\_LOW) | низкая | высокая |
| **P1** | Материализация `v_hourly`, `v_daily` по cron | средняя | высокая |
| **P1** | `v_trends_short` (1h/3h/6h) | низкая | высокая |
| **P1** | Zambretti + persistence + Sager (день) | средняя | высокая |
| **P2** | REST API + `/ask` с валидатором | средняя | высокая |
| **P2** | NLP-сводки (LLM раз в сутки) | средняя | высокая |
| **P2** | Бэкапы, Prometheus-метрики | низкая | высокая |
| **P3** | ML-прогноз (LightGBM/Prophet) | высокая | высокая |
| **P3** | Детектор аномалий (Isolation Forest) | средняя | средняя |
| **P4** | Инсайты (корреляции) | средняя | средняя |
| **P4** | Кластеризация режимов погоды | высокая | низкая |

**Обязательное условие P3:** ≥ 30 дней непрерывной истории.

* * *

## 13\. Нефункциональные требования (сводно)

| Параметр | Значение |
| --- | --- |
| Интервал опроса | 30 с (настраивается, не чаще 10 с) |
| Latency `client?command=record` | 15–95 мс (замерено) |
| Размер БД | ~130–150 МБ/год при опросе раз в минуту |
| Ретеншен | raw — вечно (или 90 дней), агрегаты — вечно |
| SQLite минимум | 3.25 (оконные функции), 3.35 (math) |
| Часовой пояс | UTC в БД, локальный — только для UI |
| IP станции | DHCP reservation по MAC `D8:BC:38:A6:E6:14` |
| Аутентификация | LAN-only + basic auth, `/ask` — отдельный токен |
| Бэкап | `VACUUM INTO` ежедневно, ротация 30 дней |
| Отказоустойчивость | systemd `Restart=always`, `WatchdogSec=180` |

* * *

## 14\. Что осталось за рамками этого ТЗ

-   **UI/дашборд** — отдельный документ `weather-ui-spec.md` (стек, polling, авторизация, графики).
    
-   **Отправка данных во внешние сервисы** (Wunderground, Weathercloud, OpenWeatherMap) — опционально, если понадобится.
    
-   **Интеграция с Home Assistant / MQTT** — отдельная задача, когда появится запрос.
    

* * *

**Итог v2:**

-   Таймстемп — epoch UTC, surrogate PK + unique index.
    
-   L1 — Python при ингесте, не в SQL.
    
-   Ветер — только векторно.
    
-   Дождь — через приращение типпера, не через rate-порог.
    
-   Окна 24h+ — материализация, не вьюхи.
    
-   Наблюдаемость — `collector_log`, systemd, Prometheus, бэкапы.
    
-   `/ask` — read-only, whitelist, LIMIT, таймаут, валидатор.
    
-   Схема версионируется, миграция с v1 описана.
    

Документ готов к передаче исполнителю.