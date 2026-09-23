# Отчёт об исполнении: фиксы weather-ui по ревью GLM (r1–r3)

**Задание:** `docs/reviews/weather-ui-fixtask-u2u3-glm.md` · **Репо:** jinny21093/vitele_meteo, main · **Базовая точка:** e6a2ca9 · **HEAD:** 4018c9f
**Версии:** SERVER_VERSION = **0.2.1** (ui/server.py:72) · UI_VERSION = **0.2.1** (ui/static/app.js:9), шапки-комментарии обновлены. Patch по §4.0: контракты API не менялись.

## 1. git log --oneline от e6a2ca9

```text
4018c9f chore(version): 0.2.0 -> 0.2.1 (patch: фиксы ревью без изменения API-контракта, §4.0)
b35c7b4 fix(C): TZ_FALLBACK — нит о синхронизации с ui/config.py
1599d40 fix(collector): battery exact match
fdaffae fix(A): .gitignore — удалить мёртвый whitelist !systemd/*
8f38ac0 fix(m-12): do_POST — через _check_auth (консистентность auth + лог)
db85ac7 fix(m-18): календарь — класс cal-nodata для дней без данных
efce6f2 fix(m-17): el() перенесена в app.js (экспорт W.el)
1aad531 fix(m-16): localStorage в try/catch — заблокированное хранилище не убивает скрипт
d0d3f76 fix(m-15): page-day — баннер при truncated из /api/history
8eb28af fix(m-14): заглушки U4-U6 — актуальный статус 'реализованы U0-U3'
2725f01 fix(m-13): init-note показывается в initMode, а не только прячется
4894802 fix(m-11): независимая деградация блоков (§8)
4ddef9b fix(m-9): 429-баннер — текст про неудачные входы + Retry-After
1dc0203 fix(m-8): refreshHeader — /api/events с types=BATTERY_LOW
e603729 fix(m-7): renderSparks — тема через W.chartTheme()
be33af5 fix(m-6): poll — in-flight guard busy, stop() снимает visibilitychange
5cd81c2 fix(m-5): _api_meta — деградация по таблицам вместо 503
63344f4 fix(m-4): валидация HISTORY_DEFAULT_FIELDS на старте
7ef2259 fix(m-3): fmtTs — null/undefined/не-число -> '—'
58dbd0f fix(m-2): page-now header() — now из d.now параметром nowSec
6f830d5 fix(m-1): apiFetch — таймаут 15 с (AbortController)
62902df fix(M-4): required-статика покрывает U2/U3
f14a16d fix(M-3): Cache-Control no-cache для .html/.js/.css
4aae3c4 fix(M-2): banner — clearTimeout первой строкой, до ветвления
67762ff fix(M-1): battery whitelist вместо regex-подстроки
e9e2291 docs(reviews): задание GLM на доработку U2+U3 (ревью r1-r3)
```

26 коммитов: задание — отдельным docs-коммитом; каждый фикс — отдельный коммит; A/B/C — вне UI, своими коммитами (не смешаны с UI-фиксами).

## 2. По каждому ID

### MAJOR

| ID | Коммит | Что сделано |
|---|---|---|
| M-1 | 67762ff | В app.js: `BATTERY_OK = new Set(["all battery are ok"])` + `batteryOk(raw)` (trim+toLowerCase, точное совпадение с Set), экспорт из WEATHER; `refreshHeader` и `header()` page-now используют её; дублированный regex в page-now удалён. Список — единственная OK-строка коллектора (совпадает с `BATTERY_OK_PATTERNS`), источник указан комментарием «держать синхронным». |
| M-2 | 4aae3c4 | `clearTimeout(banner._t)` перенесён первой строкой после получения el, до ветвления — старый 8-секундный таймер больше не гасит sticky-баннер (репро из ревью закрыто). |
| M-3 | f14a16d | `_serve_static`: `.html/.js/.css` → `no-cache` (ETag/304 уже были); `public, max-age=86400` остался только для `.svg/.png/.ico/.woff2`. В коммит-сообщении зафиксировано: отклонение от §5.0 v1.2.2, патч ТЗ v1.2.4 заказан. |
| M-4 | 62902df | required-список `load_static` дополнен: page-now.js, day.html, month.html, page-day.js, page-month.js, icons/favicon.svg. Заглушки U4–U6 сознательно не включены (комментарий в коде). |

### MINOR

| ID | Коммит | Что сделано |
|---|---|---|
| m-1 | 6f830d5 | apiFetch: AbortController + setTimeout(15000); AbortError → баннер «Сервер не отвечает (таймаут 15 с)» + `throw new ApiError(0, null)`; clearTimeout в finally. Сигнал ставится поверх opts — таймаут обязателен всегда. |
| m-2 | 58dbd0f | `header(nowSec, status, ...)` — d.now передаётся параметром (в `status` поля now нет; контракт _api_now: now — верхний уровень); fallback Date.now() сохранён. |
| m-3 | 7ef2259 | fmtTs: `if (!isFinite(n)) return "—"`. Плюс к формулировке ревью добавлена явная проверка null/undefined: `Number(null) === 0` финитен, и без неё null давал бы «1970…». |
| m-4 | 63344f4 | В main() после load_weather_columns: `unknown = [f for f in HISTORY_DEFAULT_FIELDS if f not in wcols]` → alog ERROR + sys.exit(1), по образцу load_agg_fields. |
| m-5 | 5cd81c2 | _api_meta: wmeta/schema_migrations/collector_log — каждый в отдельном try/except sqlite3.OperationalError → WARN + деградация (`{}` / None / `[]`). Отказ БД на коннекте (db_open) — прежний 503 через do_GET. |
| m-6 | be33af5 | poll(): busy-флаг — параллельный tick пропускается, schedule() в finally; stop() вызывает removeEventListener("visibilitychange", onVisible). |
| m-7 | e603729 | renderSparks: ручное чтение CSS-переменных заменено на `W.chartTheme()`; неиспользуемая переменная fg удалена. |
| m-8 | 1dc0203 | refreshHeader: `/api/events?...&types=BATTERY_LOW`. |
| m-9 | 4ddef9b | 429: `parseInt(res.headers.get("Retry-After"),10)||60`, текст «Слишком много неудачных попыток входа — подождите N с», sticky. |
| m-11 | 4894802 | page-day: renderChartsSafe (guard `typeof Chart === "undefined"` + try/catch вокруг renderCharts); rain-total вынесен в setRainTotal (чистая математика по rainBuckets) — таблица и осадки живут без Chart.js. page-month: Promise.all → независимые try/catch по запросам (/api/hourly, /api/daily) и по блокам (heatmap, календарь, тренд); рендеры null-safe. page-now: renderSparks в try/catch (образец — renderLastEvent). |
| m-12 | 8f38ac0 | Взято по желанию, выбран вариант «через _check_auth»: POST проходит auth (429/401 как у GET, лимит неудачных логинов применяется), затем 405 + Allow: GET; успешная авторизация логируется WARN-строкой с user= (логирование добавлено — вторая половина замечания). |
| m-13 | 2725f01 | refresh(): catch вокруг /api/now — при e.initMode init-note.classList.remove("hidden"), при успехе add("hidden"); throw идёт дальше, retry 10 с сохранён. |
| m-14 | 8eb28af | events/forecast/settings.html: «Сейчас реализованы U0–U3: каркас (auth, health) и экраны „Сейчас“, „Сутки“, „Месяц“.» |
| m-15 | d0d3f76 | page-day: `if (h.truncated) W.banner("Данные за окно обрезаны лимитом","warn")`. Нюанс: показ размещён ПОСЛЕ `W.banner(null)` в конце refresh — иначе тот же цикл гасил бы баннер сразу (зафиксировано в коммите). |
| m-16 | 1aad531 | Обёртки lsGet/lsSet в app.js (try/catch, экспорт из WEATHER); все 4 места: applyTheme, toggleTheme, чтение DAYS_KEY на верхнем уровне IIFE page-month, setItem в обработчике селектора. Дефолты: тема — системная, days — 30. |
| m-17 | efce6f2 | el() перенесена в app.js, экспорт W.el; в page-now/day/month — `const el = W.el;`, локальные копии удалены. |
| m-18 | db85ac7 | День без строки v_daily: `classList.add("cal-nodata")` + `title="нет данных"`. CSS: `.cal-nodata { border-style: dashed; background: var(--chip); opacity: .6; }` — пунктир + приглушение, отличимо от сухого дня. |

### Дополнительно (вне юнита UI)

| ID | Коммит | Что сделано |
|---|---|---|
| A | fdaffae | `.gitignore`: строка `!systemd/*` удалена; проверено `git check-ignore` — реальные юниты stage-*/systemd/ остаются отслеженными. Комментарий в .gitignore объясняет мотив. |
| B | 1599d40 | **Чтение кода ревьюером подтверждено, возражений нет.** Verbatim ДО: `def battery_is_ok(raw):` → `if not raw: return False; low = raw.lower(); return any(p in low for p in BATTERY_OK_PATTERNS)`; `BATTERY_OK_PATTERNS = ("all battery are ok",)` — единственная известная OK-строка. Семантика заменена на точное равенство: `return raw.strip().lower() in BATTERY_OK_PATTERNS`. Регресс-проверка 6/6: not-строка/мусор/пусто → LOW, точная строка (trim/lower) → OK. Комментарий-синхронизация с app.js BATTERY_OK добавлен с обеих сторон. |
| C | b35c7b4 | Комментарий «держать синхронным с ui/config.py TZ_FALLBACK» рядом с константой (значения совпадают: 10800 в обоих файлах). |

## 3. Итоговые версии

- `ui/server.py`: SERVER_VERSION = **"0.2.1"**, шапка-докстринг дополнена блоком v0.2.1 (перечень серверных фиксов).
- `ui/static/app.js`: UI_VERSION = **"0.2.1"**, шапка-комментарий дополнена.

## 4. wc -l (после правок)

```text
   930 ui/server.py            (876 → 930)
   317 ui/static/app.js        (258 → 317)
   236 ui/static/page-now.js   (227 → 236)
   284 ui/static/page-day.js   (267 → 284)
   246 ui/static/page-month.js (240 → 246)
    85 ui/static/index.html    (без изменений)
    72 ui/static/day.html      (без изменений)
    61 ui/static/month.html    (без изменений)
```

## 5. Статические проверки и локальные тесты

- `python3 -m py_compile ui/server.py` — **OK** (прогон после каждого серверного коммита).
- `node --check` — **OK** для каждого тронутого .js: app.js, page-now.js, page-day.js, page-month.js (после каждого клиентского коммита).
- Локальный smoke на копии живой БД (сервер на VM НЕ трогался, по п.4 задания): **91 OK / 0 FAIL** — прежние 89 проверок плюс новые: chart.js/style.css → no-cache (M-3), favicon.svg → max-age=86400 (M-3). Локальный смоук-скрипт (инструмент, вне репо) обновлён под новые ожидания кэша; POST-тест смоука (valid auth → 405) прошёл без правок — совместимо с m-12.
- Целевые проверки m-4/m-5 (смоук их не покрывает): **5 OK / 0 FAIL** — дрейф схемы (удалённая колонка uvi) → exit 1 с ERROR `HISTORY_DEFAULT_FIELDS unknown=...`; БД без wmeta/schema_migrations/collector_log → /api/meta 200 с деградированными значениями, /api/now живёт.

## 6. Что НЕ сделано и почему

1. **ID m-10** — в полученном задании отсутствует (нумерация идёт m-9 → m-11). Исполнять нечего; если это потерянный пункт — пришлите, добавлю отдельным коммитом.
2. **Деплой на VM / перезапуск сервиса** — запрещён п.4 задания («smoke будет отдельно по команде владельца»). Локальные прогонки — на копии БД, VM не затронута.
3. **ТЗ не правился** (п.5) — единственное отклонение от его текста (M-3, §5.0 v1.2.2) зафиксировано в коммит-сообщении f14a16d; док-патч v1.2.4 — вне зоны этого задания (заказывается DeepSeek отдельно).
4. **Возражений по существу замечаний нет** — все воспроизведённые ревьюером утверждения проверены по коду и подтверждены (включая B: подстрочная проверка в коллекторе — реальна). Технические нюансы исполнения (m-3 явный null-гвард, m-15 позиция баннера, m-16 общие обёртки, m-12 логирование) задокументированы в соответствующих коммит-сообщениях.
5. **Заглушки U4–U6 в required-статике** — не включены намеренно (прямо предписано M-4).
