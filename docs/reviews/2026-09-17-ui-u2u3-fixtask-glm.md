# Задание на доработку: weather-ui после ревью GLM (r1–r3)

> Сохранено дословно (сообщение владельцу от GLM-ревьюера, 2026-09-17).
> Нумерация: ревью U0+U1 — `weather-ui-review-u0u1-glm.md`; ревью U2+U3
> (раунды r1–r3, консолидированы ревьюером в настоящее задание) — этот файл.
> Исполнение и финальный отчёт — см. ответ REVIEW-U2U3-fixes.md (после сдачи).

## КОНТЕКСТ

Репо: jinny21093/vitele_meteo, ветка main, базлайн — коммит e6a2ca9.
Юнит: weather-ui-2 (U2 «Сутки» + U3 «Месяц»), версии server.py/app.js = 0.2.0.
Ревьюер GLM; полные тексты ревью — docs/reviews/ (ID замечаний ниже совпадают).
Экраны U2/U3 концептуально ПРИНЯТЫ; это — только список фиксов, не редизайн.

## ОБЩИЕ ПРАВИЛА

1. Коммиты — отдельные, по смысловым группам, сообщение вида "fix(M-1): battery
   whitelist". НЕ одним коммитом «все фиксы». Базовая точка — e6a2ca9.
2. За рамки списка не выходить. Новых фич, рефакторинга сверх указанного,
   переименований — нет. Не согласен с замечанием — не молчи и не обходи:
   оформи возражение с обоснованием в финальном отчёте.
3. По завершении поднять версии: SERVER_VERSION (server.py) и UI_VERSION (app.js)
   → "0.2.1" (patch по §4.0: фиксы без изменения API-контракта). Обновить
   шапки-комментарии файлов.
4. Статические проверки после правок: python3 -m py_compile ui/server.py;
   node --check для каждого тронутого .js. Сервер на VM не перезапускать —
   smoke будет отдельно по команде владельца.
5. ТЗ не править (док-патч v1.2.4 заказывается DeepSeek отдельно). Отклонения
   от текущего текста ТЗ, если они есть в фиксаx, — фиксируй в коммит-сообщении.

## ФИКСЫ — MAJOR (все обязательны)

### M-1. Батарея: whitelist вместо regex-подстроки (регрессия класса «not ok»)

Файлы: ui/static/app.js (refreshHeader), ui/static/page-now.js (header).
Сейчас: `/all battery are ok/i.test(...)` — test() матчит подстроку, строка
"not all battery are ok" распознаётся как OK.
Фикс: в app.js — точный список:

```js
const BATTERY_OK = new Set(["all battery are ok"]);
// источник: stage-a/weather_collector.py BATTERY_OK_PATTERNS — держать синхронным
function batteryOk(raw) {
  return BATTERY_OK.has(String(raw || "").trim().toLowerCase());
}
```

Если в коллекторе OK-строк несколько — ВСЕ внести в Set (список взять из
collector, источник указать комментарием). Экспортировать batteryOk из WEATHER;
использовать в refreshHeader и в header() страницы. Дублированную логику в
page-now.js удалить.

### M-2. banner(): sticky-баннер убивается устаревшим таймером

Файл: app.js. Сейчас clearTimeout(banner._t) только в ветке if (!sticky).
Репро: не-sticky баннер (таймер 8 c) → через 7.9 c sticky-баннер → старый
таймер его скрывает. Фикс: clearTimeout(banner._t) первой строкой после
получения el, до ветвления.

### M-3. Cache-Control: JS/CSS кэшируются 24 ч без ревалидации

Файл: server.py, _serve_static (~строки 381–382).
Сейчас: всё не-HTML → public, max-age=86400.
Фикс: для .html/.js/.css → "no-cache" (ETag/304 уже реализованы — ревалидация
дёшева); max-age=86400 оставить только для .svg/.png/.ico/.woff2.
В коммит-сообщении: «отклонение от §5.0 v1.2.2, патч v1.2.4 заказан».

### M-4. load_static: required-список не покрывает U2/U3

Файл: server.py (~262). Сейчас только 4 файла. Добавить: /static/page-now.js,
/static/day.html, /static/month.html, /static/page-day.js,
/static/page-month.js, /static/icons/favicon.svg. Заглушки U4–U6
(events/forecast/settings) НЕ включать — их отсутствие не должно валить старт.

## ФИКСЫ — MINOR (все обязательны, кроме отмеченного)

**m-1.** apiFetch: таймаут. Сейчас зависший TCP держит экран без фидбека.
Фикс: AbortController + setTimeout(15000); AbortError → баннер «Сервер не
отвечает (таймаут 15 с)» + throw new ApiError(0, null); clearTimeout в finally.

**m-2.** page-now.js header(): читается несуществующее status.now. Контракт
/api/now (server.py:541): "now" — верхнего уровня. Фикс: передавать d.now в
header() параметром nowSec, использовать его (fallback Date.now() оставить).

**m-3.** app.js fmtTs: fmtTs(null) → "1970…", fmtTs(undefined) → RangeError.
Фикс: const n = Number(epoch); if (!isFinite(n)) return "—";

**m-4.** server.py: HISTORY_DEFAULT_FIELDS не валидируются на старте (дрейф
схемы → каждый дефолтный /api/history отдаёт 503). Фикс: в main() после
load_weather_columns: unknown = [f for f in HISTORY_DEFAULT_FIELDS if f not in
wcols] → если непусто: alog ERROR + sys.exit(1) (аналог load_agg_fields).

**m-5.** server.py _api_meta: отсутствие wmeta / schema_migrations /
collector_log сейчас даёт 503 «db unavailable», хотя погода живёт. Фикс: каждую
таблицу — отдельным try/except sqlite3.OperationalError → WARN + деградация
значения (wmeta: {}, schema_migrations: None, collector_log: []). Отказ БД на
коннекте — оставить 503 как есть.

**m-6.** app.js poll(): (а) нет in-flight guard — visibilitychange гонит
параллельные tick; (б) stop() не снимает listener. Фикс: флаг busy в tick
(если busy — пропустить, schedule() в finally), stop() —
document.removeEventListener("visibilitychange", onVisible).

**m-7.** page-now.js renderSparks: читает CSS-переменные руками. Заменить на
W.chartTheme() (в page-day/month уже так).

**m-8.** app.js refreshHeader: тянет все события за 24 ч ради BATTERY_LOW.
Фикс: добавить "&types=BATTERY_LOW" в запрос /api/events.

**m-9.** 429-баннер: текст неверен (лимит — неудачные логины, не запросы),
заголовок не читается. Фикс: const ra = parseInt(res.headers.get("Retry-After"),10)||60;
banner("Слишком много неудачных попыток входа — подождите "+ra+" с","warn",true).

**m-11. Независимая деградация блоков (§8):**
- page-day: renderTable не должен зависеть от Chart.js. Guard
  typeof Chart === "undefined" перед рендером графиков; renderCharts — в
  try/catch (таблица и rain-total живут).
- page-month: заменить Promise.all на независимые try/catch по каждому
  запросу/блоку (heatmap, календарь+тренд): сбой /api/hourly не должен гасить
  календарь из /api/daily, и наоборот.
- page-now: renderSparks — в try/catch (образец — renderLastEvent).

**m-13.** «Инициализация…» мёртвая: page-now refresh() только прячет init-note.
Фикс: catch вокруг /api/now: при e.initMode — init-note classList.remove("hidden");
при успехе — добавить. throw дальше (retry 10 с сохраняется).

**m-14 (остаток).** Тексты заглушек events/forecast/settings.html упоминают
только U0/U1 — заменить на актуальный «реализованы U0–U3».

**m-15.** page-day: игнорируется truncated из /api/history. Фикс:
if (h.truncated) → banner("Данные за окно обрезаны лимитом","warn").

**m-16.** localStorage без try/catch — исключение (заблокированное хранилище)
убивает весь скрипт. Все 4 места: app.js applyTheme, app.js toggleTheme,
page-month.js чтение DAYS_KEY (верхний уровень IIFE!), page-month.js setItem
в обработчике. Обёртки с дефолтами: тема — системная, days — 30.

**m-17.** el() дублирована в трёх файлах. Перенести в app.js (экспорт W.el),
использовать в page-now/day/month, локальные копии удалить.

**m-18.** Календарь: «нет данных» и «0 мм» визуально неотличимы. Фикс: для дня
без строки v_daily — cell.classList.add("cal-nodata"), cell.title="нет данных";
в style.css класс .cal-nodata (приглушённый фон или пунктир — на твой выбор,
но различимый с сухим днём).

**m-12 (по желанию).** do_POST отдаёт 405 до auth и не логируется. Либо
провести через _check_auth для консистентности, либо задокументировать
осознанность.

## ДОПОЛНИТЕЛЬНО — вне юнита UI, отдельными коммитами, не смешивать с UI-фиксами

**A.** .gitignore: удалить строку "!systemd/*" — мёртвый паттерн, который при
появлении корневой systemd/ пере-включит auth-файлы вопреки *.conf.

**B.** Коллектор, батарея (тот же класс бага, что M-1):
stage-a/weather_collector.py ~96–104 — проверка any(p in low ...) — ПОДСТРОКА,
не точное равенство; «not …» ложится в OK → пропущенный BATTERY_LOW. Приведи
в отчёте verbatim текущий код и список BATTERY_OK_PATTERNS; замени семантику
на точное равенство (множество/==). Коммит "fix(collector): battery exact
match". Если увидишь, что я неправильно понял код, — покажи код и возрази.

**C.** Нит: рядом с TZ_FALLBACK в app.js — комментарий «держать синхронным
с ui/config.py TZ_FALLBACK».

## ФИНАЛЬНЫЙ ОТЧЁТ (обязателен, без него ревью не начнётся)

1. git log --oneline от e6a2ca9 (все коммиты).
2. По каждому ID (M-1…M-4, m-1…m-18, A/B/C): коммит + 2–3 строки «что сделано»
   ЛИБО мотивированное возражение.
3. Итоговые SERVER_VERSION / UI_VERSION.
4. wc -l для server.py, app.js, page-now.js, page-day.js, page-month.js,
   index.html, day.html, month.html.
5. Результаты py_compile / node --check.
6. Список всего, что НЕ сделал, и почему.
