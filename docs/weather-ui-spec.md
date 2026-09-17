# ТЗ: UI-дашборд погодной станции (`weather-ui-spec.md`)

**Объект:** визуальный дашборд поверх `weather.db`  
**Хост:** Debian 12 VM (Hyper-V), x86\_64, 1 vCPU, ~2.6 ГБ RAM, ~7.4 ГБ свободно  
**Сеть:** LAN-only, порт 8089 (биндинг на LAN-интерфейс)  
**Версия документа:** 1.2.4  
**Связанные документы:** `weather-roadmap.md` (этапы A/B/C), `weatherstation.md`, `weatherboard_v2.1_analitic.md`  
**Условие внедрения:** после успешного этапа B (есть `v_hourly`, `v_daily`, `forecast`, стабильный `current` — последняя строка `weather`, патч v1.2.3 §5.1)  
**История:** изменения v1.0→v1.1, v1.1→v1.2, v1.2→v1.2.1 — в архивных версиях документа.

## Changelog v1.2.2 → v1.2.4

| # | Изменение |
| --- | --- |
| 1 | 1.2.4 — синхронизация с реализацией U0–U3 (ревью GLM r1–r3): реальные имена агрегатов, battery-whitelist, cache-политика, влит патч v1.2.3 |
| 2 | §3: POST → 405 c `Connection: close` (следствие r4-1: тело POST не читается — соединение закрывается) |

## Changelog v1.2.1 → v1.2.2

| # | Изменение |
| --- | --- |
| 1 | §5.4: `/api/daily` — формат ответа специфицирован: `{from, to, rows}`, единый конверт с `/api/hourly` |
| 2 | §5.0: уточнена семантика таймаута — per-op inactivity сокета, не wall-clock дедлайн |
| 3 | §5.5: битый `context` → `null` в ответе (не 500) + WARN в лог с `event_id=` |
| 4 | §5.9: перечислены колонки CSV; WHERE для COUNT и SELECT идентичен; BOM — опционально для Excel |
| 5 | §11: verify-скрипт — guard на `jq` и `curl` |
| 6 | §13: счётчик эндпоинтов исправлен 8 → 9 (добавлен `/api/hourly`) |

## 0\. Принципы

1.  **Stdlib-only Python.** Ноль pip-зависимостей. Один `server.py` \+ статика, по стилю как snmp-dash.
    
2.  **LAN-only.** Порт слушает только LAN-интерфейс. Basic auth — второй слой.
    
3.  **Без Node, без CDN.** Vanilla JS + локальный Chart.js. Дача может быть без интернета.
    
4.  **UI не пишет в БД.** Коннект per-request: open rw → `PRAGMA query_only=ON` → `busy_timeout=5000` → запрос → close. Rw-open — для авто-WAL-recovery после некорректной смерти коллектора (фризы VM — норма). Долгоживущие коннекты запрещены.
    
5.  **Graceful degradation.** Коллектор упал — дашборд открывается, показывает последние данные с плашкой «обновлено N минут назад».
    
6.  **Без inline-скриптов и inline-стилей** (требование CSP).
    
7.  **Никакого SQL-конкатенирования.** Значения — placeholders; имена колонок/типов — только из whitelist (`PRAGMA table_info` на старте; для events — фиксированный список типов). Неизвестное значение → 400. *f-string, format(), % в SQL запрещены без исключений.*
    
8.  **Mobile-friendly, desktop-first.**
    

## 1\. Стек

| Слой | Выбор |
| --- | --- |
| HTTP-сервер | `http.server.ThreadingHTTPServer`, `protocol_version = 'HTTP/1.1'`, лимит соединений через subclass (см. §5.0) |
| Роутинг | dict `[path → handler]`, ~30 строк |
| БД | sqlite3, rw + `query_only=ON`, per-request |
| Графики | Chart.js 4.x, локальная копия `static/vendor/chart.min.js` |
| CSS/JS | один `style.css` + `app.js` \+ по одному на экран |
| Auth | Basic, `hmac.compare_digest` |
| Сжатие | gzip статики — пре-компрессия на старте, кэш bytes в RAM |

Не используем: Flask/FastAPI, Jinja2, React/Vue, Prometheus/Grafana, npm.

## 2\. Развёртывание

### 2.1. Структура

text

/home/auditbot/weather-dash/
├── collector.py / materializer.py / weather.db / backups/
└── ui/
    ├── server.py, config.py
    ├── static/  (index/day/month/events/forecast/settings.html,
    │             style.css, app.js, page-\*.js, vendor/chart.min.js)
    └── icons/   (SVG)

⚠️ Сверить юзера/путь (`auditbot` vs `vitele`) до написания verify-скриптов: `id auditbot; ls -ld /home/auditbot/weather-dash`.

### 2.2. systemd + graceful shutdown

ini

\[Service\]
Type\=simple
User\=auditbot
ExecStart\=/usr/bin/python3 /home/auditbot/weather-dash/ui/server.py
Restart\=always
RestartSec\=10
TimeoutStopSec\=30

-   SIGTERM/SIGINT → `threading.Thread(target=server.shutdown, daemon=True).start()` (из потока `serve_forever()` — deadlock).
    
-   После выхода из `serve_forever()` → `server_close()`. `daemon_threads=False`.
    
-   Watchdog не ставим; heartbeat — Uptime Kuma на `/api/health`.
    
-   Зависимости юнита U7 (патч v1.2.3): ZeroTier поднимается до UI —
    
ini

[Unit]
After=network-online.target zerotier-one.service
Wants=network-online.target zerotier-one.service


### 2.3. Сеть

Биндинг на LAN-IP из `config.py`, не `0.0.0.0`. Проверка: `ss -tlnp | grep 8089`. Наружу — только через ZeroTier. Reverse proxy — нет, auth в handler'е.

## 3\. Авторизация

-   Один пользователь `weather`; пароль — `secrets.token_urlsafe(24)` при установке, файл `~/.weather-ui-credentials` (600), путь в `config.py`, в `.gitignore`. Не в репо, не в логи, никогда.
    
-   Basic auth на всё, включая `/api/*` и статику. Без auth — только `/api/health`.
    
-   **Ответ 401 обязан содержать** (иначе браузер не покажет диалог ввода):
    

text

HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="Weather", charset="UTF-8"
Content-Type: text/plain; charset=utf-8

-   `do_POST` → 405 c `Connection: close` (исключений нет, UI полностью read-only; тело POST не читается — соединение закрывается). Закрытие — флагом сокета; заголовок `Connection: close` несёт только финальный 405.
    
-   Rate-limit: 10 неудачных/мин/IP, словарь под `threading.Lock()`, затем 429 + `Retry-After: 60`. **При успешной авторизации счётчик неудач для IP сбрасывается.**
    
-   **IP клиента:** берётся из `handler.client_address[0]` (прямой `REMOTE_ADDR`). На момент v1.2.2 — без учёта `X-Forwarded-For`, так как reverse proxy отсутствует. Если появится nginx/прокси — пересмотреть, явно документировать и, возможно, ограничить доверенные источники.
    
-   Лог — только `user=`, без `Authorization`\-заголовка.
    

## 4\. Экраны

### 4.0. Шапка (все экраны)

Навигация: Сейчас | Сутки | Месяц | События | Прогноз | Настройки (мелко). Справа:

-   **Статус:** 🟢 < 2 мин, 🟡 2–10 мин, 🔴 > 10 мин + «обновлено N назад»;
    
-   **Батарея:** 🟢 `battery_raw` ТОЧНО равен одной из whitelist-строк коллектора (`stage-a/weather_collector.py` `BATTERY_OK_PATTERNS`); 🟡 — нераспознан (fail-safe); 🔴 — активное `BATTERY_LOW`;
    
-   **Версия UI:** `UI_VERSION` — **semver кода, не версия документа** (major — ломает API-контракт, minor — новая фича/экран, patch — фикс). Константа в `app.js`.
    

### 4.1. «Сейчас» (/) — polling 30 с

Карточки: Улица (T, RH, dew point, wind chill), Дом, Давление (rel крупно, abs мелко, тенденция 3 ч стрелкой + класс), Ветер (скорость, порыв, стрелка направления + румб, avg2/avg10), Дождь (rate, час, сутки, месяц, год), Солнце (light, UVI). Внизу: последнее событие за сутки, sparkline T и P за 6 ч. Кнопка «Обновить». Цветовые пороги — как в v1.0.

### 4.2. «Сутки» (/day) — polling 5 мин

`/api/history` за 24 ч. 5 графиков (T\_out+T\_in + ноль; P; ветер 3 линии; дождь столбики + кумулятив; солнце площадь + UVI). Таблица min/max/avg + времена. Времена — TZ дачи.

### 4.3. «Месяц» (/month) — polling 1 ч

Heatmap дни×часы из `/api/hourly`; календарь осадков из `/api/daily`; тренд min/max/avg за 30 дней. Селектор 7/30/90 дней. Оси — TZ дачи.

### 4.4. «События» (/events) — polling 60 с

Таймлайн, группировка по датам в TZ дачи. Фильтры: тип (мультиселект), severity. Клик → карточка с `context` (распарсенный JSON). При `truncated: true` — баннер «показаны не все события, сузьте окно».

### 4.5. «Прогноз» (/forecast) — polling 15 мин

Zambretti крупно + иконка; persistence-таблица (1/3/6/12/24 ч); Sager (ночью — плашка «Не применимо (ночь)»). Если прогноз не сформирован — плашка «Прогноз ещё не сформирован (нужно ≥ 3 ч истории)».

### 4.6. «Настройки» (/settings) — по запросу

`wmeta` \+ версия схемы + 20 записей `collector_log` + `db_health`: если `updated_at` старше 26 ч — жёлтым «Проверка БД: не проводилась (N дн назад)». Экспорт CSV (новая вкладка). Кнопка «Проверить БД» — `PRAGMA quick_check` по требованию (может занять секунды — предупредить в UI), результат в модалке.

## 5\. REST API

### 5.0. Общие правила

-   Auth — на всё, кроме `/api/health`.
    
-   SQL: значения — placeholders; `fields=`/`types=`/`severity=` — CSV, проверка по whitelist. Неизвестное → 400.
    
-   Заголовки: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` — всегда; CSP — на HTML. Кэш статики: ETag (sha256) \+ 304 \+ `Vary: Accept-Encoding`; `Cache-Control`: HTML, JS, CSS → `no-cache` (ревалидация на каждом запросе — релизы без версионирования URL); `.svg`/`.png`/`.ico`/`.woff2` → `public, max-age=86400`; `/api/*` → `no-store`.
    
-   Gzip статики: пре-компрессия на старте (RAM-кэш), отдача при `Accept-Encoding: gzip`.
    
-   **Лимит соединений:** семафор до создания потока, acquire — **неблокирующий** (блокирующий acquire в `process_request` ставит accept-цикл и рубит `/api/health` в перегрузке); 503 — HTTP/1.1-корректный; освобождение — в `finally` потоковой ветки:
    

python

class LimitedThreadingHTTPServer(ThreadingHTTPServer):
    def \_\_init\_\_(self, \*a, max\_concurrent\=20, \*\*kw):
        super().\_\_init\_\_(\*a, \*\*kw)
        self.\_sem \= threading.BoundedSemaphore(max\_concurrent)
    def process\_request(self, request, client\_address):
        if not self.\_sem.acquire(blocking\=False):
            request.sendall(b"HTTP/1.1 503 Service Unavailable\\r\\n"
                            b"Content-Length: 0\\r\\nConnection: close\\r\\n\\r\\n")
            request.close()
            return
        try:
            super().process\_request(request, client\_address)
        except Exception:
            self.\_sem.release()
            raise
    def process\_request\_thread(self, request, client\_address):
        try:
            super().process\_request\_thread(request, client\_address)
        finally:
            self.\_sem.release()

-   **Таймаут 10 с — это per-op inactivity сокета** (через `Handler.timeout = 10`, механизм `StreamRequestHandler.setup()` → `self.connection.settimeout(10)`), **не общий wall-clock дедлайн запроса.** Общего дедлайна нет: стриминг §5.9 легитимен, пока сокет активен (клиент читает байты). Действует и на idle keep-alive между запросами.
    
-   JSON-ответы ≤ 10 МБ (исключение — §5.9).
    
-   Права: юзер UI должен иметь запись в БД, `-wal`, `-shm` и каталог.
    

### 5.1. GET /api/now

Ответ (контракт v1.2.3; таблицы `current` в схеме v2 нет):

json

{  "now": <epoch>,  "current": { "<колонки weather>: значения" },  "status": {"collector\_ok": <bool>, "last\_poll\_ts": <int>, "gap\_s": <int>},  "p\_tendency\_3h": <float|null>}

-   `current` — последняя строка `weather` **плоским словарём** (raw + L1 + L2-short). Вложенная группировка Indoor/Outdoor/… из ранних версий — отменена. Сервер отдаёт все колонки, **кроме** `id` и `schema_version` (явный список, не `SELECT *`).
    
-   `p_tendency_3h` — для карточки давления (§4.1): avg(последние 10 мин) − avg(10 мин 3 ч назад), окна по времени, не LAG-строки.
    
-   `collector_ok`: `gap_s < 180` (3 цикла коллектора).
    
-   Пустая БД → 503 + `{"error": "current is empty, collector has not written yet"}`. UI — плашка «Инициализация…», повтор через 10 с.
    
-   p95 < 50 мс.

### 5.2. GET /api/history?from=&to=&fields=

-   `from`, `to` — epoch UTC, обязательны, `from < to`, **окно ≤ 7 дней** (иначе 400).
    
-   `fields` — whitelist; дефолт: `ts, indoor_temp_c, outdoor_temp_c, outdoor_hum_pct, pressure_rel_mmhg, wind_ms, gust_ms, wind_avg10_ms, wind_dir_deg, rain_rate_calc_mmh, rain_hour_mm, light_wm2, uvi`.
    
-   LIMIT 50000; при обрезании — `"truncated": true`.
    
-   Ответ: `{from, to, fields, rows, truncated}`. p95 < 300 мс (24 ч), < 1 с (7 дней).
    

### 5.3. GET /api/hourly?from=&to=

Окно ≤ 90 дней (≤ 2160 строк), из `v_hourly` (реальные имена этапа B, решение A-2). Поля: `hour_epoch, t_out_avg, t_out_min, t_out_max, p_rel_avg, wind_avg, wind_max, gust_max, wind_dir_mode, rain_mm, solar_avg, uvi_max, n_samples`. Конверт ответа: `{from, to, fields, rows}` — `fields`: список колонок, порядок `rows` соответствует порядку `fields` (решение B-1). p95 < 500 мс.

### 5.4. GET /api/daily?from=&to=

Окно ≤ 365 дней, из `v_daily`. Поля: `day_epoch, t_out_min, t_out_max, t_out_avg, t_out_min_time, t_out_max_time, p_min, p_max, wind_avg, wind_max, gust_max, wind_run_km, wind_dir_mode, rain_mm, rain_hours, solar_sum_wh_m2, uvi_max, gdd_day, frost_flag, hard_freeze_flag, fog_flag, n_samples`.

**Ответ:** единый с `/api/hourly` — `{from, to, fields, rows}`; без `truncated` (≤ 365 строк не упирается в лимит).

### 5.5. GET /api/events?from=&to=&types=&severity=

Окно ≤ 90 дней; `types`/`severity` — CSV → placeholders. **LIMIT 5000**, при обрезании — `"truncated": true` \+ баннер в UI.

**Семантика окна — перекрытие (патч v1.2.3):** попадает событие, начавшееся внутри окна, ЛИБО начавшееся раньше и открытое/закрывшееся после `from`:

sql

WHERE (ts\_start >= ? AND ts\_start <= ?)   OR (ts\_start <  ? AND (ts\_end IS NULL OR ts\_end >= ?))

Клиент показывает `ts_start < from` как «идёт с более раннего времени». Параметр `include_open` отменён.

**Каталог типов (21, патч v1.2.3):** FROST, HARD\_FREEZE, FOG, STORM\_APPROACH, THUNDER\_RISK, HEAVY\_RAIN, DOWNPOUR, STRONG\_WIND, HURRICANE\_GUST, HEATWAVE, DRY\_SPELL, CALM, RAPID\_TEMP\_DROP, RAPID\_TEMP\_RISE, PRESSURE\_CRASH, RAIN\_COUNTER\_RESET, SENSOR\_MISSING, SENSOR\_STUCK, SENSOR\_DRIFT, SENSOR\_ANOMALY, BATTERY\_LOW. Фильтр по типу вне каталога → 400.

**Батарея (§4.0/§4.1, патч v1.2.3):** статус BATTERY\_LOW определяется запросом `/api/events?from=<now-3600>&to=<now>` — любое открытое событие видно независимо от возраста.

**Формат ответа — единый со всеми остальными эндпоинтами** (консистентность для клиентского кода):

json

{
  "from": 1757877600,
  "to":   1757964000,
  "rows": \[
    {
      "id": 42,
      "ts\_start": 1757900000,
      "ts\_end":   1757903600,
      "event\_type": "FROST",
      "severity": "high",
      "value": \-0.3,
      "context": { ... },        // распарсенный JSON-снимок, либо null
      "duration\_s": 3600
    }
  \],
  "truncated": false
}

Поля `context` парсятся из TEXT на сервере перед отдачей (клиент получает уже объект, не строку). `duration_s` \= `ts_end - ts_start` для закрытых событий, `null` для открытых.

**Битый `context`** (не валидный JSON в TEXT) → `null` в ответе (не 500), плюс **WARN в лог** с `event_id=` и фрагментом сырой строки (до 100 символов). Один мусорный ряд не должен валить весь эндпоинт.

### 5.6. GET /api/forecast

`{issued_at, zambretti, persistence: [{horizon_h, t_out_c, p_rel_mmhg, wind_ms, rain_mm} × 5], sager}`.  
**Прогноза ещё нет** (Zambretti требует 3-ч тенденцию): **200** (не 503 — это не ошибка) с `zambretti: null, persistence: [], sager: null`. UI — плашка.

### 5.7. GET /api/meta

`wmeta` объект + последняя `schema_migrations` \+ 20 записей `collector_log` + `db_health` (из wmeta, пишет материализатор).

### 5.8. GET /api/health — без auth

`SELECT 1` + `last_ts`: `{"status": "ok", "db": "ok", "last_ts": ...}` — 200 или 503. **p99 < 10 мс.** Эндпоинт не расширять.

### 5.9. GET /api/export.csv?from=&to=

-   Окно ≤ 31 дня.
    
-   **Перед началом стриминга — `SELECT COUNT(*)`** по тому же диапазону (быстро по `idx_weather_ts`). Если `count > 100000` → **413 Payload Too Large** с телом `{"error": "too many rows", "count": N, "limit": 100000}` и **без** начала передачи CSV. Это гарантирует детерминированное поведение: либо полный файл, либо честная ошибка до отправки байтов.
    
-   **WHERE для COUNT и SELECT — идентичен** (те же `from`, `to`, без изменений между двумя запросами; окно закрыто сверху, дельта невозможна).
    
-   **Колонки CSV:** все поля таблицы `weather` (raw + L1 + L2-short) **в порядке схемы**, первая — `ts`. Список берётся из `PRAGMA table_info(weather)` на старте.
    
-   Стриминг `Transfer-Encoding: chunked`, без `Content-Length` (исключение из лимита 10 МБ). `csv.writer` — прямо в сокет, `lineterminator="\r\n"` (RFC 4180, Excel-совместимо).
    
-   `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="weather-YYYY-MM-DD.csv"`.
    
-   **BOM (UTF-8, `\xEF\xBB\xBF`) — опционально** (некоторые версии Excel требуют для корректной кодировки). Решение — параметр `?bom=1` (по умолчанию без BOM).
    

## 6\. Клиентский JS

-   `app.js`: `apiFetch` (401/429/5xx — retry + баннер; отличает «Инициализация…» по телу 503), форматтеры, `timeAgo`, `colorFor*`.
    
-   **TZ:** при init — `TZ_OFFSET` из `/api/meta`; далее только через хелперы:
    

js

function fmtTs(epoch)  { const d \= new Date((epoch + TZ\_OFFSET) \* 1000);
                         return d.toISOString().replace('T',' ').slice(0,16); }
function fmtTime(e)    { return fmtTs(e).slice(11,16); }
function fmtDate(e)    { return fmtTs(e).slice(0,10); }

`toLocaleTimeString()` без явного `timeZone` — **запрещён**.

-   **Polling:** рекурсивный `setTimeout`, backoff до 5 мин, пауза при `document.hidden`, мгновенный refresh при `visibilitychange` → visible.
    
-   Тёмная тема: CSS-переменные + `prefers-color-scheme` \+ тумблер (localStorage).
    
-   Глобальное — только `window.WEATHER`.
    

## 7\. Производительность

| Параметр | Значение |
| --- | --- |
| Пользователей / запросов в сек | ≤ 5 / ≤ 1 |
| p95: `/api/now` / `/api/history` (24 ч) / `/api/hourly` / `/api/daily` (90 д) | < 50 мс / < 300 мс / < 500 мс / < 500 мс |
| p99 `/api/health` | < 10 мс |
| RAM / CPU UI-процесса | < 50 МБ / < 2% покой, < 10% актив |
| static/ | < 500 КБ |

## 8\. Обработка ошибок

| Ситуация | Поведение |
| --- | --- |
| `current` пуст (первый запуск) | 503 → плашка «Инициализация…», retry 10 с |
| Коллектор не пишет > 2 мин / > 10 мин | 🟡 плашка / 🔴 плашка + значения серым |
| Прогноз не сформирован | 200 с null → плашка «нужно ≥ 3 ч истории» |
| `/api/*` 5xx | Баннер, повтор через 30 с |
| БД недоступна | 503 везде, страница-заглушка |
| Rate-limit | 429 + Retry-After, баннер |
| `export.csv` \> 100k строк | 413 до начала передачи, баннер «сузьте окно» |
| Битый `context` в `events` | `null` в ответе + WARN в лог |
| `SQLITE_READONLY_RECOVERY` | Не должен встречаться (rw-open); если поймали — ERROR в лог |

## 9\. Логирование

stdout → journald. ISO8601 LEVEL msg key=value. INFO — запросы; WARN — > 1 с, 4xx, битый context; ERROR — 5xx, исключения. INFO-строки authed-запросов содержат `user=weather`; 401/429 — только `ip=` (идентифицированного пользователя нет). Authorization-заголовок не логируется никогда. Не логируем: `Authorization`, пароли, содержимое БД (кроме фрагментов для диагностики битого context — до 100 символов).

## 10\. Не входит

`/ask` (этап C, `set_authorizer`), NLP-сводки, уведомления, управление станцией, HTTPS (если понадобится — nginx сверху), мобильное приложение.

## 11\. Порядок внедрения и приёмка

U0 скелет+health+auth+статика → U1 Сейчас → U2 Сутки → U3 Месяц → U4 События → U5 Прогноз → U6 Настройки+экспорт → U7 systemd+Kuma+verify.

`verify_stage_ui.sh`:

bash

#!/usr/bin/env bash
set \-euo pipefail
: "${UI\_PASS:?UI\_PASS not set}"     \# пароль только через env
command \-v curl \>/dev/null 2\>&1 || { echo "curl is required"; exit 1; }
command \-v jq   \>/dev/null 2\>&1 || { echo "jq is required";   exit 1; }
B\="http://localhost:8089"; NOW\=$(date +%s)
\# Health без auth
curl \-sf "$B/api/health" \> /dev/null                                          \# 200
\# Auth: успех / провал / отсутствие
curl \-sf \-u weather:"$UI\_PASS" "$B/api/now" | jq \-e '.ts != null' \> /dev/null
\[ "$(curl \-s \-o /dev/null \-w '%{http\_code}' \-u wrong:wrong "$B/api/now")" \= 401 \]
\[ "$(curl \-s \-o /dev/null \-w '%{http\_code}' "$B/api/now")" \= 401 \]
\# POST запрещён
\[ "$(curl \-s \-o /dev/null \-w '%{http\_code}' \-X POST \-u weather:"$UI\_PASS" "$B/api/now")" \= 405 \]
\# Окна
\[ "$(curl \-s \-o /dev/null \-w '%{http\_code}' \-u weather:"$UI\_PASS" \\
   "$B/api/history?from\=$((NOW-8\*86400))&to=$NOW")" = 400 \]                  # окно > 7 д
curl -sf -u weather:"$UI\_PASS" "$B/api/hourly?from\=$((NOW\-90\*86400))&to\=$NOW" > /dev/null
\# Формат /api/events — единый контракт {from, to, rows, truncated}
curl -sf -u weather:"$UI\_PASS" "$B/api/events?from\=$((NOW\-7\*86400))&to\=$NOW" \\
    | jq -e 'has("from") and has("to") and has("rows") and has("truncated")' > /dev/null
\# Формат /api/daily — единый конверт {from, to, rows}
curl -sf -u weather:"$UI\_PASS" "$B/api/daily?from\=$((NOW\-30\*86400))&to\=$NOW" \\
    | jq -e 'has("from") and has("to") and has("rows")' \> /dev/null
\# +: 6 HTML → 200
\# +: chart.min.js — Cache-Control + ETag + gzip при Accept-Encoding
\# +: nosniff на всех ответах; CSP на /
\# +: ss -tlnp | grep 8089 — только LAN-IP
\# +: Kuma-монитор /api/health — up

## 12\. Открытые вопросы

1.  **Выгрузка бэкапов наружу VM** — TBD владельца (SMB/rsync/USB/облако). Не блокирует U0–U7; локальный минимум (14 gzip-копий) — этап B.
    
2.  Сверка юзера/путей (`auditbot` vs `vitele`) — до verify-скриптов.
    
3.  Порт: `ss -tlnp` при установке; занят → 8091 (8090 занят weather-api этапа B).
    

## 13\. Резюме

Stdlib-Python + vanilla JS + Chart.js; **9 read-only эндпоинтов** (`query_only`, rw-open per-request ради WAL-recovery); TZ дачи на всех экранах; raw-история ≤ 7 д + `/api/hourly` ≤ 90 д; стриминговый CSV с предварительным COUNT; LAN-only 8089 + Basic auth с корректным `WWW-Authenticate`; все граничные состояния (пустой `current`, пустой `forecast`, переполнение `events`, перегрузка соединений, битый `context`) специфицированы; graceful shutdown; ~1600 строк Python + ~2000 JS/CSS/HTML.