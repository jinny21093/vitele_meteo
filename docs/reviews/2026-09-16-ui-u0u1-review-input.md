# Ревью weather-ui server.py (U0+U1) — вывод для GLM

**Задача:** weather-ui-1 · **ТЗ:** weather-ui-spec.md v1.2.2 · **Код:** v0.1.0 (U0+U1)
**Файлы:** `state/weather-ui/` — `server.py` (748 строк), `config.py` (39),
`static/` (index.html 85, 5×placeholder-страницы, style.css 204, app.js 221,
page-now.js 227, vendor/chart.min.js 4.4.4 UMD 205 КБ, icons/favicon.svg)
**Тест:** `scripts/weather_ui_smoke.py` — **70 OK / 0 FAIL** на копии живой БД
(weather-8, схема v2; инжектированы 3 свежих строки weather и 3 события, вкл. битый context)

---

## 1. Вердикт-резюме

Инфраструктурная начинка собрана строго по ТЗ и прогнана локально ДО деплоя на VM
(как и требовалось): shutdown-поток, семафор с неблокирующим acquire, rw+query_only,
401-диалог, rate-limit с блокировкой ДО проверки пароля, whitelist-SQL, ETag/gzip,
graceful SIGTERM — всё живо и покрыто тестом. Найдено и исправлено в ходе прогона:
ETag не отдавался на 200 (только на 304) — исправлено, покрыто тестом
(«ETag -> 304» теперь проходит вместе с «chart.js ETag»).

## 2. Чек-лист соответствия (ТЗ → код)

| § ТЗ | Требование | Где в server.py | Статус |
|---|---|---|---|
| §0.1 | stdlib-only, один server.py | импорты: base64…http.server, urllib.parse | OK |
| §0.2 | LAN-only, bind из config, не 0.0.0.0 | `main()` → цикл по `config.BIND_HOSTS`; см. отклонение A-5 | OK* |
| §0.4 | per-request: rw-open → query_only=ON → busy_timeout=5000 → close | `db_open()`; каждый handler — try/finally con.close() | OK |
| §0.6 | без inline-скриптов/стилей (CSP) | CSP на HTML; в HTML нет inline; тест «no inline scripts» | OK |
| §0.7 | placeholders; whitelist колонок из PRAGMA на старте; типы/severity — фикс. списки; неизвестное → 400 | `load_weather_columns()` (37 колонок), `EVENT_TYPES` (10), `EVENT_SEVERITIES` (3); fields/types/severity → 400 с перечислением допустимых | OK |
| §0.8 | mobile-friendly | style.css: auto-fit grid, media ≤900/≤640 | OK |
| §2.2 | SIGTERM/SIGINT → `Thread(target=server.shutdown, daemon=True)`; после serve_forever → server_close; daemon_threads=False | `main()`: `_stop` запускает shutdown в отдельном потоке для каждого слушателя; `daemon_threads=False` в классе | OK, SIGTERM-тест RC=0 + «stop clean=1» |
| §3 | Basic auth на всё (кроме /api/health), `hmac.compare_digest`; 401 обязан нести `WWW-Authenticate: Basic realm="Weather", charset="UTF-8"` + text/plain | `_check_auth()`; тест точного заголовка; auth на статику проверен («/ без auth 401») | OK |
| §3 | rate-limit 10 неудач/мин/IP под `threading.Lock` → 429 + `Retry-After: 60`; успех сбрасывает счётчик | `RateLimiter` (deque+Lock); тест: 10×401 → 429, `Retry-After: 60`, valid-креды тоже 429 до истечения окна | OK |
| §3 | IP = `client_address[0]`, без XFF | `do_GET()` | OK |
| §3 | лог — только `user=`… без Authorization | тест «no Authorization in log»; в логе ip=, user не печатается при неудаче (см. отклонение A-7) | OK* |
| §3 | do_POST → 405, исключений нет | `do_POST()`; тест 405 | OK |
| §5.0 | семафор: acquire неблокирующий в process_request, 503 до потока, release в finally | `LimitedThreadingHTTPServer` — дословно сниппет ТЗ (+общий семафор для двух слушателей, отклонение A-5) | OK |
| §5.0 | таймаут 10 с — per-op inactivity сокета, не wall-clock | `timeout = config.HANDLER_TIMEOUT` как class-attr (механизм StreamRequestHandler.setup → settimeout(10)); общего дедлайна нет | OK |
| §5.0 | gzip статики пре-компрессия на старте, RAM-кэш | `load_static()`; тест Content-Encoding: gzip, Vary: Accept-Encoding | OK |
| §5.0 | ETag sha256, If-None-Match → 304, Cache-Control static 86400 / api no-store / html no-cache | `load_static()` + `_serve_static()`; тесты 304/gzip/no-store/no-cache | OK |
| §5.0 | JSON ≤ 10 МБ | гвард в `_json()` (LIMIT'ы не дают добраться) | OK |
| §5.1 | /api/now: current + status(collector_ok, last_poll_ts, gap_s); пустая БД → 503 «current is empty…» | `_api_now()`; адаптация A-1; тест 503-текста не делался (копия БД непуста) — ветка покрыта кодом | OK* |
| §5.2 | /api/history: from/to обязательны, from<to, окно ≤7д → 400; fields whitelist; LIMIT 50000+truncated; конверт {from,to,fields,rows,truncated} | `_api_history()`; тесты: все 400-ветки, 7д ровно OK, unknown field 400 | OK |
| §5.5 | /api/events: окно ≤90д; types/severity CSV→placeholders; LIMIT 5000+truncated; конверт {from,to,rows,truncated}; context парсится, битый → null + WARN с event_id и фрагментом ≤100 | `_api_events()`; тесты: битый context→null, открытый→duration_s null, фильтры (в т.ч. lower-case types) | OK |
| §5.7 | /api/meta: wmeta + последняя миграция + 20 collector_log + db_health | `_api_meta()`; тест tz_offset_seconds="10800"; db_health отсутствует в wmeta → null (A-4) | OK |
| §5.8 | /api/health без auth: SELECT 1 + last_ts, 200/503, не расширять | `_api_health()`; ровно 3 ключа (тест) | OK |
| §5.9 | export.csv — НЕ реализован (U6); маршрут отвечает 404 | `_route()` | by design |
| §9 | stdout→journald, ISO8601 LEVEL msg key=value; WARN 4xx/>1с; ERROR 5xx | `alog()` + do_GET finally; пример: `2026-09-16T13:28:53+00:00 WARN GET /api/now 401 0ms ip=127.0.0.1` | OK |
| §4.0 | шапка: статус 🟢🟡🔴 по 2/10 мин; батарея 🟢 ok-паттерн / 🟡 нераспознано / 🔴 BATTERY_LOW; UI_VERSION semver в app.js | app.js `setFreshness/setBattery`, page-now.js `header()`; UI_VERSION="0.1.0" | OK |
| §4.1 | «Сейчас»: 6 карточек, событие за сутки, sparkline T/P 6ч, кнопка, polling 30 с | page-now.js | OK |
| §6 | TZ-хелперы, без toLocaleTimeString; polling backoff до 5 мин, пауза hidden, refresh visible; тёмная тема; только window.WEATHER | app.js | OK |

## 3. Адаптации и отклонения (на veto GLM/владельца)

| # | Что | Почему |
|---|---|---|
| A-1 | **Таблицы `current` в БД нет.** /api/now отдаёт `{"current": <последняя строка weather>, "status": {...}, "now", "p_tendency_3h"}` — тот же подход, что у этапа B (weather_api.py /now). | ТЗ v1.2.2 писалось от ТЗ v2.0 (исторического), где current существовал; в реальной схеме v2 его нет. 503 «current is empty…» срабатывает на пустом weather. |
| A-2 | **Имена таблиц агрегатов**: в ТЗ `agg_hourly`/`agg_daily`, реально (этап B) — `v_hourly`/`v_daily`. К U0+U1 не относится (эндпоинты U3), но при U3 буду использовать реальные имена. | Предупредить заранее, чтобы не всплыло на U3. |
| A-3 | **/api/meta реализован уже в U0**, хотя в списке юнитов не упомянут явно. | §6 требует TZ_OFFSET из /api/meta при init клиента — без него не работает ни один экран. |
| A-4 | **db_health сейчас всегда null** — материализатор этапа B этот ключ в wmeta не пишет. | §5.7 сам говорит «пишет материализатор» — появится позже; UI-ветка «не проводилась» уже покрыта. |
| A-5 | **Bind: два слушателя** (192.168.8.146 LAN + 10.147.17.101 ZeroTier) с ОДНИМ BoundedSemaphore на оба. ТЗ §2.3 говорит «биндинг на LAN-IP» (ед. ч.), но там же «наружу — только через ZeroTier». Не 0.0.0.0 — проверка `ss` покажет два приватных IP. | Функциональное требование доступа через ZeroTier иначе невыполнимо. Если надо строго один — правка одной строки config. |
| A-6 | **p_tendency_3h в /api/now** — дополнение к ответу (в §5.1 не перечислен). | §4.1 требует стрелку тенденции 3 ч на экране «Сейчас»; алгоритм идентичен этапу B (окна по времени, не LAG). Альтернатива — считать клиентом из history (лишний запрос). |
| A-7 | **В логе запроса user не печатается вовсе** (только ip/status/ms). | §3 «лог — только user=, без Authorization»; при 401 пользователя нет, при 200 не печатал — не вижу пользы. Если GLM хочет `user=weather` на 200 — добавлю. |
| A-8 | **`collect ok` порог**: collector_ok = gap < 180 с (3 цикла по 60 с). | ТЗ порога не задаёт; шапка §4.0 всё равно красит независимо (2/10 мин). |
| A-9 | **EVENT_TYPES расширен относительно этапа A**: добавлены HEATWAVE/CALM/DRY_SPELL (этап B) — всего 10 типов. | Фиксированный список §0.7 должен покрывать все события в таблице, иначе фильтр отрежет реальные. |
| A-10 | **rows в /api/history — массивы** (порядок = поля в `fields`), не объекты. | §5.2 возвращает `fields` рядом с `rows` — трактую как выравнивание по индексу; экономит ~2× трафика (лимит 10 МБ). Если GLM трактует иначе — поменяю на объекты. |
| A-11 | **Battery 🔴**: активное BATTERY_LOW ищется среди событий последних 24 ч (уже запрошенных для карточки «событие»). Открытое событие старше 24 ч в U1 не увидим. | Полный обход events ради одной плашки — лишний запрос; уточню в U4 (там всё равно будет таймлайн). |

## 4. Найдено и починено в ходе smoke

1. **ETag отсутствовал на 200** (был только на 304) → клиент никогда не переиспользовал кэш корректно. Исправлено (`_serve_static`), тесты «chart.js ETag» + «ETag -> 304» зелёные.
2. Timing-утечка имён пользователя: `compare_digest(u) and compare_digest(p)` при неверном user не сравнивал password → обе проверки теперь выполняются всегда.
3. Обрыв клиента (BrokenPipe) логировался как ERROR 500 → теперь INFO «client gone», без фейковой 5xx-строки.

## 5. Smoke (локально, копия живой БД): 70 OK / 0 FAIL

Блоки: health (3) · auth/401-диалог (5) · now (5) · POST 405 (1) · history — окна,
whitelist, конверт, LIMIT-поле (9) · meta (4) · events — конверт, битый/открытый
context, фильтры, 90д (10) · будущие эндпоинты 404 (3) · статика/CSP/кэш/gzip/304/
traversal (15) · SIGTERM graceful ×2 (5) · rate-limit 429 (4) · лог (3).
Примеры строк лога в формате §9: `…INFO start version=0.1.0 bind=127.0.0.1 port=8199 …`,
`…WARN GET /api/now 401 0ms ip=127.0.0.1`.

## 6. Открытые вопросы

1. A-5 (двойной bind) и A-10 (формат rows) — два решения, которые дешевле принять сейчас.
2. Порт 8089 vs 8090 (этап B API): UI по ТЗ — 8089, занят → 8090. Но 8090 уже занят weather-api! При установке (U7) проверяю `ss` и по умолчанию беру 8089; конфликт маловероятен. Заодно вопрос стратегии: два API-стека над одной БД (8089 UI + 8090 machine-API) или после U7 закрыть 8090? Сейчас ничего не трогаю.
3. `auth not configured` (файл кред нет) → 503 на всём, кроме /health: ок ли, что сервер вообще стартует в этом состоянии? (Мой выбор — стартовать, journald получает ERROR при старте.)

## 7. Не входит (следующие юниты)

U2 Сутки · U3 Месяц (/api/hourly→v_hourly) · U4 События (полный экран+фильтры) ·
U5 Прогноз (/api/forecast) · U6 Настройки+/api/export.csv (стриминг, COUNT-гвард 100k, 413) ·
U7 systemd-юнит (§2.2, TimeoutStopSec=30), Kuma-монитор /api/health, verify_stage_ui.sh,
генерация кред, сверка путей (`id auditbot; ls -ld /home/auditbot/weather-dash`).
