#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Патч ТЗ v1.2.4 (пункт D ревью r4) — docs/weather-ui-spec.md.

Дословный перенос D1-D8 + полный влив патча v1.2.3 (§5.1, §5.5, §9, §12.3, §2.2).
Хирургия по номерам строк (документ полон U+00A0 — Edit-матчинг ненадёжен).
Правки идут снизу вверх, чтобы номера строк не поплыли.
"""
import sys

PATH = "/home/z/my-project/github/vitele_meteo/docs/weather-ui-spec.md"

src = open(PATH, encoding="utf-8").read()
lines = src.split("\n")
n0 = len(lines)

# ---------------------------------------------------------------- L385 §12.3
assert lines[384].startswith("3.  Порт:"), lines[384][:40]
lines[384] = ("3.  Порт: `ss -tlnp` при установке; занят → 8091 "
              "(8090 занят weather-api этапа B).")

# ---------------------------------------------------------------- L335 §9
assert lines[334].startswith("stdout → journald."), lines[334][:40]
lines[334] = ("stdout → journald. ISO8601 LEVEL msg key=value. INFO — запросы; "
              "WARN — > 1 с, 4xx, битый context; ERROR — 5xx, исключения. "
              "INFO-строки authed-запросов содержат `user=weather`; 401/429 — "
              "только `ip=` (идентифицированного пользователя нет). "
              "Authorization-заголовок не логируется никогда. Не логируем: "
              "`Authorization`, пароли, содержимое БД (кроме фрагментов для "
              "диагностики битого context — до 100 символов).")

# ---------------------------------------------------------------- L228 §5.5
assert lines[227].startswith("Окно ≤ 90 дней;"), lines[227][:40]
lines[227:228] = [
    lines[227],
    "",
    ("**Семантика окна — перекрытие (патч v1.2.3):** попадает событие, "
     "начавшееся внутри окна, ЛИБО начавшееся раньше и открытое/закрывшееся "
     "после `from`:"),
    "",
    "sql",
    "",
    "WHERE (ts\\_start >= ? AND ts\\_start <= ?)   OR "
    "(ts\\_start <  ? AND (ts\\_end IS NULL OR ts\\_end >= ?))",
    "",
    ("Клиент показывает `ts_start < from` как «идёт с более раннего времени». "
     "Параметр `include_open` отменён."),
    "",
    ("**Каталог типов (21, патч v1.2.3):** FROST, HARD\\_FREEZE, FOG, "
     "STORM\\_APPROACH, THUNDER\\_RISK, HEAVY\\_RAIN, DOWNPOUR, STRONG\\_WIND, "
     "HURRICANE\\_GUST, HEATWAVE, DRY\\_SPELL, CALM, RAPID\\_TEMP\\_DROP, "
     "RAPID\\_TEMP\\_RISE, PRESSURE\\_CRASH, RAIN\\_COUNTER\\_RESET, "
     "SENSOR\\_MISSING, SENSOR\\_STUCK, SENSOR\\_DRIFT, SENSOR\\_ANOMALY, "
     "BATTERY\\_LOW. Фильтр по типу вне каталога → 400."),
    "",
    ("**Батарея (§4.0/§4.1, патч v1.2.3):** статус BATTERY\\_LOW определяется "
     "запросом `/api/events?from=<now-3600>&to=<now>` — любое открытое "
     "событие видно независимо от возраста."),
]

# ---------------------------------------------------------------- L224 §5.4
assert lines[223].startswith("**Ответ:**"), lines[223][:40]
lines[223] = ("**Ответ:** единый с `/api/hourly` — `{from, to, fields, rows}`; "
              "без `truncated` (≤ 365 строк не упирается в лимит).")

# ---------------------------------------------------------------- L222 §5.4
assert "agg_daily" in lines[221], lines[221][:40]
lines[221] = lines[221].replace("`agg_daily`", "`v_daily`")

# ---------------------------------------------------------------- L218 §5.3
assert lines[217].startswith("Окно ≤ 90 дней (≤ 2160 строк)"), lines[217][:40]
lines[217] = ("Окно ≤ 90 дней (≤ 2160 строк), из `v_hourly` (реальные имена "
              "этапа B, решение A-2). Поля: `hour_epoch, t_out_avg, t_out_min, "
              "t_out_max, p_rel_avg, wind_avg, wind_max, gust_max, "
              "wind_dir_mode, rain_mm, solar_avg, uvi_max, n_samples`. "
              "Конверт ответа: `{from, to, fields, rows}` — `fields`: список "
              "колонок, порядок `rows` соответствует порядку `fields` "
              "(решение B-1). p95 < 500 мс.")

# ---------------------------------------------------------------- L200-203 §5.1
assert lines[199].startswith("### 5.1."), lines[199][:40]
assert "current.payload" in lines[201], lines[201][:40]
assert "Пустой" in lines[202], lines[202][:40]
lines[199:203] = [
    "### 5.1. GET /api/now",
    "",
    "Ответ (контракт v1.2.3; таблицы `current` в схеме v2 нет):",
    "",
    "json",
    "",
    ('{  "now": <epoch>,  "current": { "<колонки weather>: значения" },  '
     '"status": {"collector\\_ok": <bool>, "last\\_poll\\_ts": <int>, '
     '"gap\\_s": <int>},  "p\\_tendency\\_3h": <float|null>}'),
    "",
    ("-   `current` — последняя строка `weather` **плоским словарём** "
     "(raw + L1 + L2-short). Вложенная группировка Indoor/Outdoor/… из ранних "
     "версий — отменена. Сервер отдаёт все колонки, **кроме** `id` и "
     "`schema_version` (явный список, не `SELECT *`)."),
    "    ",
    ("-   `p_tendency_3h` — для карточки давления (§4.1): avg(последние "
     "10 мин) − avg(10 мин 3 ч назад), окна по времени, не LAG-строки."),
    "    ",
    "-   `collector_ok`: `gap_s < 180` (3 цикла коллектора).",
    "    ",
    ("-   Пустая БД → 503 + `{\"error\": \"current is empty, collector has "
     "not written yet\"}`. UI — плашка «Инициализация…», повтор через 10 с."),
    "    ",
    "-   p95 < 50 мс.",
]

# ---------------------------------------------------------------- L163 §5.0
assert lines[162].startswith("-   Заголовки:"), lines[162][:40]
lines[162] = ("-   Заголовки: `X-Content-Type-Options: nosniff`, "
              "`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` — "
              "всегда; CSP — на HTML. Кэш статики: ETag (sha256) \\+ 304 \\+ "
              "`Vary: Accept-Encoding`; `Cache-Control`: HTML, JS, CSS → "
              "`no-cache` (ревалидация на каждом запросе — релизы без "
              "версионирования URL); `.svg`/`.png`/`.ico`/`.woff2` → "
              "`public, max-age=86400`; `/api/*` → `no-store`.")

# ---------------------------------------------------------------- L126 §4.0
assert "**Батарея:**" in lines[125], lines[125][:40]
lines[125] = ("-   **Батарея:** 🟢 `battery_raw` ТОЧНО равен одной из "
              "whitelist-строк коллектора (`stage-a/weather_collector.py` "
              "`BATTERY_OK_PATTERNS`); 🟡 — нераспознан (fail-safe); 🔴 — "
              "активное `BATTERY_LOW`;")

# ---------------------------------------------------------------- L109 §3
assert "`do_POST`" in lines[108], lines[108][:40]
lines[108] = ("-   `do_POST` → 405 c `Connection: close` (исключений нет, UI "
              "полностью read-only; тело POST не читается — соединение "
              "закрывается).")

# ---------------------------------------------------------------- L87-88 §2.2
assert lines[86].startswith("-   Watchdog"), lines[86][:40]
assert lines[87].strip() == "", "L88 не пустая-4sp: %r" % lines[87]
lines[87:88] = [
    "    ",
    "-   Зависимости юнита U7 (патч v1.2.3): ZeroTier поднимается до UI —",
    "    ",
    "ini",
    "",
    "[Unit]",
    "After=network-online.target zerotier-one.service",
    "Wants=network-online.target zerotier-one.service",
    "",
]

# ---------------------------------------------------------------- L9/10 changelog
assert lines[8].startswith("**История:**"), lines[8][:40]
assert lines[10].startswith("## Changelog v1.2.1"), lines[10][:40]
lines[10:10] = [
    "## Changelog v1.2.2 → v1.2.4",
    "",
    "| # | Изменение |",
    "| --- | --- |",
    ("| 1 | 1.2.4 — синхронизация с реализацией U0–U3 (ревью GLM r1–r3): "
     "реальные имена агрегатов, battery-whitelist, cache-политика, "
     "влит патч v1.2.3 |"),
    ("| 2 | §3: POST → 405 c `Connection: close` (следствие r4-1: тело POST "
     "не читается — соединение закрывается) |"),
    "",
]

# ---------------------------------------------------------------- L6 версия
assert "**Версия документа:**" in lines[5], lines[5][:40]
lines[5] = "**Версия документа:**\u00a01.2.4  "

# ---------------------------------------------------------------- запись
out = "\n".join(lines)
open(PATH, "w", encoding="utf-8").write(out)

# ---------------------------------------------------------------- проверки
must = [
    "Версия документа:**\u00a01.2.4",
    "Changelog v1.2.2 → v1.2.4",
    "battery-whitelist, cache-политика, влит патч v1.2.3",
    "405 c `Connection: close` (исключений нет",
    "zerotier-one.service",
    "ТОЧНО равен одной из whitelist-строк",
    "BATTERY_OK_PATTERNS",
    "ревалидация на каждом запросе",
    "контракт v1.2.3",
    "из `v_hourly` (реальные имена этапа B, решение A-2)",
    "`v_daily`",
    "решение B-1",
    "Семантика окна — перекрытие",
    "Каталог типов (21",
    "include_open",
    "user=weather`; 401/429 — только `ip=`",
    "8091 (8090 занят weather-api этапа B)",
]
for m in must:
    if m not in out:
        print("ASSERT FAIL: нет маркера:", m)
        sys.exit(1)
gone = ["матчит ok-паттерн", "current.payload", "из `agg_hourly`.",
        "из `agg_daily`.", "занят → 8090."]
for g in gone:
    if g in out:
        print("ASSERT FAIL: старый текст остался:", g)
        sys.exit(1)
n_agg_h = out.count("agg_hourly")
n_agg_d = out.count("agg_daily")
print("OK: %d правок применено, строк %d -> %d" % (13, n0, len(lines)))
print("agg_hourly осталось: %d (ожид. 1 — «Условие внедрения», вне скоупа D)"
      % n_agg_h)
print("agg_daily осталось: %d (ожид. 1)" % n_agg_d)
