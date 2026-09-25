#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather-ui server.py v0.5.0 (U0-U6 + фиксы ревью GLM r1-r6) — дашборд
погодной станции, stdlib-only.

ТЗ: weather-ui-spec.md v1.2.8 (§4.6/§5.9/§5.10/§5.11 — U6). Задачи: U7 закрыт
(юнит/loopback/verify, deploy-tools/verify_stage_ui.sh); U6 «Настройки+Экспорт».

  v0.5.0 U6 (SERVER_VERSION 0.5.0 — новые эндпоинты = minor §4.0; деплоированный
        код меняется). Три серверных механики:
        S1 /api/export.csv (§5.9 v1.2.8): GET, type=history|hourly|daily ->
          weather/v_hourly/v_daily; fields= по PRAGMA-whitelist; separator=;|,
          (дефолт ; — Excel, RFC 4180); pre-COUNT по тому же WHERE -> оценка
          байт (строки × поля × 10) -> > 300 МБ -> 413 + X-Export-Rows (фактич.
          число строк) ДО первого байта CSV; окно <= 366 д (спека v1.2.7 §5.9
          «31 д» заменена — U6-задание, зафиксировано в v1.2.8); стриминг
          chunked (Transfer-Encoding, БЕЗ Content-Length) батчами 500 строк;
          коннект живёт от db_open до конца стрима — семафорный слот занят
          ровно это время (единственный long-lived обработчик; зависший клиент
          рвётся HANDLER_TIMEOUT=10 — per-op inactivity); BEGIN перед COUNT —
          единый WAL-снапшот COUNT+SELECT; ошибка БД после заголовков —
          честный 503 уже невозможен: терминальный chunk (файл обрезан) +
          ERROR в лог; NULL -> пустая ячейка; BOM НЕ пишется (A10/U8, параметр
          bom из v1.2.7 отменён); CSV-injection: колонки с decltype TEXT
          (weather.battery_raw, v_hourly/v_daily.wind_dir_mode) при первом
          символе = + - @ префиксуются апострофом (числовые — нет: минус
          температуры легитимен); опциональный limit=N (1..100000, AFTER
          pre-COUNT) — пробник клиента U6-C2 (limit=1) проходит ту же
          проверку 413; filename weather-<type>-<from>-<to>.csv.
        S2 GET /api/settings (§5.10): конверт = /api/meta, но wmeta
          ФИЛЬТРУЕТСЯ на сервере по WMETA_VISIBLE (units, tz,
          tz_offset_seconds, gdd_tbase_c, ok_total, err_total, last_ok,
          schema_version) — пути/station_ip/mac на экран Настроек не
          попадают; /api/meta НЕ тронут (контракт §5.7 — «wmeta объект»
          целиком — ломать фильтрацией нельзя, app.js читает оттуда TZ).
        S3 POST /api/check-db (§5.11, ЕДИНСТВЕННЫЙ POST системы): PRAGMA
          quick_check на query_only-коннекте (чтение — принцип «UI не пишет
          в БД» не нарушен); СВЕРКА С3 ревьювера: db_health в wmeta не пишет
          НИКТО (этап B кладёт только last_agg_hourly/daily_epoch) — поэтому
          «кэшировать результат в wmeta» отклонено (это была бы ЗАПИСЬ из
          UI); владелец ключа — этап B, POST отвечает своим результатом
          синхронно; таймаут 60 с через set_progress_handler (statement-
          timeout в sqlite3 нет) -> 503 {"error":"check timed out"};
          rate-limit 1/мин/IP (тот же RateLimiter) -> 429 + Retry-After;
          попытка учитывается ДО работы (таймаут тоже «стоит» слота лимита).
        Клиент: settings.html + page-settings.js (M-4: required-статика);
        app.js — только UI_VERSION 0.5.0 (навигация/хелперы уже покрывали U6).
  v0.4.1 U7-2: loopback 127.0.0.1 в BIND_HOSTS (config.py) — Uptime Kuma на
        той же VM целится в http://127.0.0.1:8089/api/health и не зависит
        от LAN/ZT-интерфейса (спека v1.2.7 §2.3). Бамп SERVER_VERSION —
        изменение ДЕПЛОИРОВАННОГО кода (config.py); UI_VERSION/app.js
        не меняются. systemd-юнит — ui/weather-ui.service (U7-1, §2.2):
        он теперь штатный способ рестарта (setsid-nohup отменён).
  v0.4.0 U5 Прогноз: /api/forecast — конверт из таблицы forecast этапа B
        (§5.6): формулы (Zambretti/Sager/persistence) НЕ дублируем — пишет
        weather_aggregator в hourly-прогоне (systemd timer *:02:00), читаем
        последний прогон (MAX(issued_at)); available:false (пустая таблица)
        — 200, не ошибка; stale (U5-S2) — возраст > 2 прогонов; required-
        статика + forecast.html/page-forecast.js (M-4); экран /forecast (§4.5).
        Дельта ревью владельца до деплоя (версия остаётся 0.4.0):
        issued_values — база Δ на issued_at («вариант (б)», не «сейчас»);
        letter Замбретти в конверте (этап B пишет forecast.letter, U5-B1;
        колонка селектится только если есть — легаси-БД без 503);
        горизонт 24 ч — отклонён владельцем, не добавлен.

  v0.3.0 U4 События: /api/events — overlap-семантика окна (§5.5, канон
        v1.2.4): видно событие, начавшееся внутри окна, ЛИБО начавшееся
        раньше и открытое/закрывшееся после from (include_open отменён);
        overlap-условие во внешних скобках — иначе IN-фильтр типов
        применяется только к левой ветке (приоритет AND/OR); required-
        статика + events.html/page-events.js (M-4); экран /events (§4.4).

  v0.2.2 Ревью r4-r6 (см. отчёты r4-r6): do_POST — соединение закрывается
        в любом исходе (close_connection первой строкой, r4-1/r5-1;
        заголовок Connection: close — на финальном 405); r6-3 — лог
        POST auth-отказов; смоук 93 проверки в deploy-tools/ (основа
        verify_stage_ui.sh, U7).

  v0.2.1 Фиксы ревью r1-r3 (см. docs/reviews/weather-ui-fixtask-u2u3-glm.md):
        M-3 Cache-Control no-cache для html/js/css (отклонение от §5.0,
        патч ТЗ v1.2.4 заказан); M-4 required-статика покрывает U2/U3;
        m-4 валидация HISTORY_DEFAULT_FIELDS на старте; m-5 деградация
        _api_meta по таблицам; m-12 do_POST через _check_auth.

  §5.3/§5.4 /api/hourly -> v_hourly, /api/daily -> v_daily (реальные имена этапа B,
        A-2); fields = список ТЗ ∩ PRAGMA table_info на старте; rows — массивы (A-10).
  v1.2.3 Патч ревью U0+U1 применён: §5.1 current без id/schema_version (явный
        список колонок); §9 user= в INFO authed-запросов; §12.3 фолбэк 8091;
        ниты (assert whitelist в window_delta, timeout=10 убран); EVENT_TYPES —
        полный каталог 21 тип (A-9).

Инфраструктура (сверка с ТЗ — см. REVIEW-U0U1.md):
  §2.2  SIGTERM/SIGINT -> server.shutdown() в ОТДЕЛЬНОМ потоке (вызов из
        serve_forever() в том же потоке — deadlock), после выхода — server_close();
        daemon_threads=False — дожидаемся обработчиков (TimeoutStopSec=30, U7).
  §5.0  Лимит соединений: BoundedSemaphore, acquire НЕблокирующий в
        process_request (блокирующий ставил бы accept-цикл и убивал /api/health
        в перегрузке); 503 — сырой HTTP/1.1-ответ ДО создания потока;
        освобождение — в finally потоковой ветки.
  §5.0  Handler.timeout=10 — per-op inactivity сокета (механизм
        StreamRequestHandler.setup -> connection.settimeout(10)), действует и на
        idle keep-alive; НЕ общий wall-clock дедлайн (его нет вовсе).
  §0.4  БД per-request: open mode=rw (авто-WAL-recovery после некорректной смерти
        коллектора) -> PRAGMA query_only=ON -> busy_timeout=5000 -> запрос -> close.
        Долгоживущие коннекты запрещены. UI не пишет в БД ничем и никогда.
  §0.7  SQL: значения — только placeholders; имена колонок — из whitelist
        PRAGMA table_info(weather) на старте; типы/severity событий — фиксированные
        списки; неизвестное значение -> 400. Исключений на values нет.
  §3    Basic auth на всё, кроме /api/health; 401 обязан нести WWW-Authenticate
        (иначе браузер не покажет диалог); hmac.compare_digest (bytes); пароль —
        из ~/.weather-ui-credentials (600); rate-limit 10 неудач/мин/IP под
        threading.Lock -> 429 + Retry-After: 60; успех сбрасывает счётчик; IP —
        handler.client_address[0] (прямой REMOTE_ADDR, без X-Forwarded-For).
  §5.0  Заголовки: nosniff/DENY/no-referrer на ВСЕ ответы; CSP — на HTML;
        Cache-Control (факт M-3, r4-2): html/js/css -> no-cache (+ ETag(sha256)
        + 304 + Vary: Accept-Encoding); бинарные ассеты (.svg/.png/.ico/.woff2)
        -> public,max-age=86400; /api/* -> no-store.
  §5.0  Gzip статики — пре-компрессия на старте, кэш bytes в RAM.
  §9    Лог: stdout -> journald, ISO8601 LEVEL msg key=value. INFO — запросы;
        WARN — 4xx и > 1 с; ERROR — 5xx/исключения. Authorization/пароли не
        логируются никогда; содержимое БД — только фрагмент <= 100 символов
        битого context (§5.5).
"""
import base64
import csv
import gzip
import hashlib
import hmac
import io
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config

SERVER_VERSION = "0.5.0"

# --- фиксированные списки (§0.7: имена/типы — только whitelist) ---
# Типы событий: полный каталог патча v1.2.3 §5.5 (21 тип) — этап A (коллектор) +
# этап B (материализатор) + будущие типы аналитики; фильтр по ещё не существующему
# типу вернёт пусто — безвредно (решение ревью A-9). Overlap-семантика окна —
# v0.3.0 (§5.5).
EVENT_TYPES = ("FROST", "HARD_FREEZE", "FOG", "STORM_APPROACH", "THUNDER_RISK",
               "HEAVY_RAIN", "DOWNPOUR", "STRONG_WIND", "HURRICANE_GUST",
               "HEATWAVE", "DRY_SPELL", "CALM", "RAPID_TEMP_DROP",
               "RAPID_TEMP_RISE", "PRESSURE_CRASH", "RAIN_COUNTER_RESET",
               "SENSOR_MISSING", "SENSOR_STUCK", "SENSOR_DRIFT", "SENSOR_ANOMALY",
               "BATTERY_LOW")
EVENT_SEVERITIES = ("low", "mid", "high")
# Дефолтные поля /api/history (§5.2) — каждое всё равно проверяется по whitelist.
HISTORY_DEFAULT_FIELDS = ("ts", "indoor_temp_c", "outdoor_temp_c", "outdoor_hum_pct",
                          "pressure_rel_mmhg", "wind_ms", "gust_ms", "wind_avg10_ms",
                          "wind_dir_deg", "rain_rate_calc_mmh", "rain_hour_mm",
                          "light_wm2", "uvi")

HISTORY_WINDOW = 7 * 86400      # §5.2: окно <= 7 дней
EVENTS_WINDOW = 90 * 86400      # §5.5: окно <= 90 дней
HISTORY_LIMIT = 50000           # §5.2
EVENTS_LIMIT = 5000             # §5.5
HOURLY_WINDOW = 90 * 86400      # §5.3: окно <= 90 дней (<= 2160 строк)
# U5-S2 (v0.4.0): этап B пересчитывает прогноз ежечасно (timer *:02:00) —
# свежий прогон старше 2 ч = 2 пропущенных прогона подряд -> stale:true
FORECAST_STALE_S = 7200
DAILY_WINDOW = 365 * 86400      # §5.4: окно <= 365 дней
# --- U6 /api/export.csv (§5.9 v1.2.8) ---
# Окно export: 366 дней (U6-задание; спека v1.2.7 §5.9 «31 д» — наследие
# одно-табличного export v1.2.2, заменена в v1.2.8). Реальный размер ответа
# гвардит pre-COUNT-оценка (EXPORT_MAX_BYTES), а не окно.
EXPORT_WINDOW = 366 * 86400
# type= -> (таблица, ts-колонка WHERE). Имена — внутренние константы (§0.7:
# f-string по таблице/колонке допустим только для констант модуля).
EXPORT_TYPES = {"history": ("weather", "ts"),
                "hourly": ("v_hourly", "hour_epoch"),
                "daily": ("v_daily", "day_epoch")}
EXPORT_AVG_FIELD_BYTES = 10     # оценка ширины поля CSV (строки × поля × 10)
EXPORT_CHUNK_ROWS = 500         # строк на chunk (задание U6: батчи ~500)
EXPORT_MAX_LIMIT = 100000       # опциональный limit= (пробник U6-C2: limit=1)
CSV_INJECT_CHARS = "=+-@"       # первый символ TEXT-значения -> префикс "'"
# --- U6 S2/S3 ---
# §4.6/§5.10: wmeta-ключи, разрешённые к показу на экране Настроек (фильтр на
# СЕРВЕРЕ, не на клиенте). РОВНО список ревьювера (U6-S2): пути/station_ip/mac
# не показываем. /api/meta остаётся без фильтра (контракт §5.7).
WMETA_VISIBLE = ("units", "tz", "tz_offset_seconds", "gdd_tbase_c",
                 "ok_total", "err_total", "last_ok", "schema_version")
CHECK_DB_PROGRESS_OPS = 1000    # шаг set_progress_handler (гранулярность ~мс)
CHECK_DB_MAX_ROWS = 50          # строк quick_check в ответе (остальное — счётчик)
# §5.3: поля /api/hourly — фиксированный список ТЗ; на старте пересекается с
# реальными колонками v_hourly (A-2: имена этапа B), порядок — как в ТЗ.
HOURLY_FIELDS = ("hour_epoch", "t_out_avg", "t_out_min", "t_out_max", "p_rel_avg",
                 "wind_avg", "wind_max", "gust_max", "wind_dir_mode", "rain_mm",
                 "solar_avg", "uvi_max", "n_samples")
# §5.4: поля /api/daily — аналогично; конверт единый с /api/hourly, без truncated.
DAILY_FIELDS = ("day_epoch", "t_out_min", "t_out_max", "t_out_avg", "t_out_min_time",
                "t_out_max_time", "p_min", "p_max", "wind_avg", "wind_max", "gust_max",
                "wind_run_km", "wind_dir_mode", "rain_mm", "rain_hours",
                "solar_sum_wh_m2", "uvi_max", "gdd_day", "frost_flag",
                "hard_freeze_flag", "fog_flag", "n_samples")
# §5.1 (патч v1.2.3): /api/now отдаёт все колонки weather, КРОМЕ этих (явный
# список из whitelist PRAGMA, не SELECT *).
NOW_EXCLUDE = ("id", "schema_version")
# §0.7-дисциплина (нит ревью §3.4): f-string в window_delta — только по константам.
WINDOW_DELTA_COLS = ("pressure_rel_mmhg", "outdoor_temp_c")
COLLECTOR_OK_GAP = 180          # сек: коллектор пишет раз в 60 с; 3 цикла = ok
MAX_EPOCH = 2 ** 62             # защита от bigint, который не лезет в sqlite3

# HTML-страницы (маршрут -> файл в static/). U4/U5/U6 — настоящие экраны
# (v0.3.0/v0.4.0/v0.5.0).
PAGES = {"/": "index.html", "/day": "day.html", "/month": "month.html",
         "/events": "events.html", "/forecast": "forecast.html",
         "/settings": "settings.html"}

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")

SEC_HEADERS = (("X-Content-Type-Options", "nosniff"),
               ("X-Frame-Options", "DENY"),
               ("Referrer-Policy", "no-referrer"))

CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".svg": "image/svg+xml",
                 ".json": "application/json; charset=utf-8",
                 ".png": "image/png",
                 ".ico": "image/x-icon",
                 ".woff2": "font/woff2"}


def alog(level, msg):
    """§9: ISO8601 LEVEL msg key=value -> stdout -> journald."""
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
          f"{level} {msg}", flush=True)


# ---------------- БД (§0.4: rw-open + query_only, per-request) ----------------

def db_open(db_path):
    """Новый коннект на каждый запрос. mode=rw (НЕ ro — авто-WAL-recovery),
    query_only=ON (писать не может ничто), busy_timeout=5000. Коннект закрывается
    вызывающей стороной в finally — долгоживущих коннектов нет."""
    # timeout=10 убран (нит ревью §3.4): финальное значение даёт busy_timeout ниже.
    con = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    con.isolation_level = None
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def load_weather_columns(db_path):
    """Whitelist колонок weather из PRAGMA table_info — один раз на старте (§0.7).
    Возвращает УПОРЯДОЧЕННЫЙ список (схемный порядок) — нужен /api/now для явного
    списка колонок (§5.1 v1.2.3); frozenset для проверок строит main()."""
    con = db_open(db_path)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(weather)")]
    finally:
        con.close()
    if not cols:
        alog("ERROR", f"startup: table weather not found db={db_path}")
        sys.exit(1)
    return cols


def load_agg_fields(db_path, table, spec_fields):
    """§5.3/§5.4 + A-2: served-поля = фиксированный список ТЗ, пересечённый с
    реальными колонками таблицы агрегата (PRAGMA, §0.7); порядок — как в ТЗ.
    Отвалившееся поле — WARN (схема эволюционирует; клиент читает fields из
    конверта). Таблица отсутствует / нет ни одного поля — ERROR+exit (этап B
    обязателен по условию внедрения UI)."""
    assert table in ("v_hourly", "v_daily")   # имя таблицы — внутренняя константа
    con = db_open(db_path)
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()
    if not cols:
        alog("ERROR", f"startup: table {table} not found db={db_path}")
        sys.exit(1)
    served = [f for f in spec_fields if f in cols]
    dropped = [f for f in spec_fields if f not in cols]
    if dropped:
        alog("WARN", f"startup: {table} fields dropped={','.join(dropped)}")
    if not served:
        alog("ERROR", f"startup: {table} has none of the spec fields")
        sys.exit(1)
    return served


def load_table_columns(db_path, table):
    """U6-S1 (§5.9 v1.2.8): колонки таблицы export из PRAGMA table_info на старте
    — [(name, decltype-upper)]. decltype нужен для CSV-injection-гварда (TEXT-колонки).
    Таблица отсутствует — ERROR+exit (этапы A/B обязательны по условию внедрения)."""
    assert table in ("weather", "v_hourly", "v_daily")  # §0.7: не user-input
    con = db_open(db_path)
    try:
        info = con.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        con.close()
    if not info:
        alog("ERROR", f"startup: table {table} not found db={db_path}")
        sys.exit(1)
    return [(r[1], (r[2] or "").upper()) for r in info]


def window_delta(con, col, end_ts, hours):
    """avg(последние 10 мин) - avg(10 мин за hours до того) — окна по времени,
    как в этапе B (weather_api.py/window_delta), не LAG-строки. col — ТОЛЬКО
    константа из модуля (не пользовательский ввод), т.е. whitelist by construction
    (§0.7 допускает имена колонок из whitelist; values — placeholders)."""
    assert col in WINDOW_DELTA_COLS  # дисциплина §0.7 (нит ревью): не user-input
    a = con.execute(f"SELECT AVG({col}) FROM weather WHERE ts>? AND ts<=?",
                    (end_ts - 600, end_ts)).fetchone()[0]
    b = con.execute(f"SELECT AVG({col}) FROM weather WHERE ts>? AND ts<=?",
                    (end_ts - hours * 3600 - 600, end_ts - hours * 3600)).fetchone()[0]
    if a is None or b is None:
        return None
    return round(a - b, 3)


# ---------------- Rate-limit (§3: 10 неудач/мин/IP, Lock) ----------------

class RateLimiter:
    """Неудачные Basic-auth попытки по IP. Решение 429 — ДО проверки пароля,
    если IP уже исчерпал лимит (перебор не должен даже дёргать compare_digest)."""

    def __init__(self, limit, window):
        self.limit = limit
        self.window = window
        self._fails = {}
        self._lock = threading.Lock()

    def _prune(self, dq, now):
        while dq and now - dq[0] > self.window:
            dq.popleft()

    def blocked(self, ip):
        with self._lock:
            dq = self._fails.get(ip)
            if not dq:
                return False
            self._prune(dq, time.monotonic())
            return len(dq) >= self.limit

    def fail(self, ip):
        with self._lock:
            dq = self._fails.setdefault(ip, deque())
            dq.append(time.monotonic())
            self._prune(dq, time.monotonic())
            if len(self._fails) > 4096:              # гвард памяти от спуфинга
                now = time.monotonic()
                for k in [k for k, v in self._fails.items() if not v]:
                    self._fails.pop(k, None)
                for v in self._fails.values():
                    self._prune(v, now)
            return len(dq)

    def reset(self, ip):
        """§3: при успешной авторизации счётчик неудач для IP сбрасывается."""
        with self._lock:
            self._fails.pop(ip, None)


# ---------------- Креды (§3) ----------------

def load_creds(path):
    """~/.weather-ui-credentials: одна строка 'user:pass', 600. -> tuple | None.
    Пароль нигде не логируется; при слишком широких правах — WARN (не валим:
    поправимо на месте, а отказ сервера хуже)."""
    try:
        st = os.stat(path)
        if st.st_mode & 0o077:
            alog("WARN", f"cred file perms too open path={path} (ожидание 600)")
        with open(path, encoding="utf-8") as f:
            line = f.read().strip()
        user, passwd = line.split(":", 1)
        if user and passwd:
            return user, passwd
    except (OSError, ValueError) as e:
        alog("ERROR", f"cred file unreadable path={path} err={type(e).__name__}")
    return None


# ---------------- Статика: пре-компрессия на старте (§5.0) ----------------

def load_static(root):
    """RAM-кэш {url: {ctype, raw, gz, etag}}. ETag — sha256 сырых байт (§5.0)."""
    cache = {}
    if not os.path.isdir(root):
        alog("ERROR", f"startup: static root missing dir={root}")
        sys.exit(1)
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            ctype = CONTENT_TYPES.get(os.path.splitext(fn)[1].lower())
            if ctype is None:
                alog("WARN", f"static: skipped unknown-type file file={rel}")
                continue
            with open(full, "rb") as f:
                raw = f.read()
            gz = b""
            if len(raw) >= 256:
                cand = gzip.compress(raw, 6)
                if len(cand) < len(raw):
                    gz = cand
            cache["/static/" + rel] = {
                "ctype": ctype, "raw": raw, "gz": gz,
                "etag": '"' + hashlib.sha256(raw).hexdigest()[:32] + '"',
            }
    # M-4 (ревью r1-r3): required покрывает экраны U0-U6 (U6, v0.5.0:
    # +settings.html/page-settings.js — заглушка стала настоящим экраном).
    for required in ("/static/index.html", "/static/style.css", "/static/app.js",
                     "/static/vendor/chart.min.js", "/static/page-now.js",
                     "/static/day.html", "/static/month.html",
                     "/static/page-day.js", "/static/page-month.js",
                     "/static/events.html", "/static/page-events.js",
                     "/static/forecast.html", "/static/page-forecast.js",
                     "/static/settings.html", "/static/page-settings.js",
                     "/static/icons/favicon.svg"):
        if required not in cache:
            alog("ERROR", f"startup: required static file missing file={required}")
            sys.exit(1)
    alog("INFO", f"static loaded files={len(cache)} "
                 f"bytes={sum(len(e['raw']) for e in cache.values())}")
    return cache


# ---------------- HTTP-сервер с лимитом соединений (§5.0) ----------------

class LimitedThreadingHTTPServer(ThreadingHTTPServer):
    """Семафор ДО создания потока; acquire неблокирующий (блокирующий acquire
    в process_request ставит accept-цикл и рубит /api/health в перегрузке);
    503 — HTTP/1.1-корректный; освобождение — в finally потоковой ветки.
    semaphore= — общий на несколько слушателей (LAN + ZeroTier, §2.3)."""

    daemon_threads = False          # §2.2: дожидаемся обработчиков при shutdown
    allow_reuse_address = True

    def __init__(self, *args, semaphore=None, max_concurrent=None, **kwargs):
        super().__init__(*args, **kwargs)
        n = max_concurrent or config.MAX_CONCURRENT
        self._sem = semaphore if semaphore is not None \
            else threading.BoundedSemaphore(n)

    def process_request(self, request, client_address):
        if not self._sem.acquire(blocking=False):
            request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                            b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._sem.release()


# ---------------- Handler ----------------

class UiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"           # §1: keep-alive + Content-Length везде
    server_version = "weather-ui/" + SERVER_VERSION
    sys_version = ""
    timeout = config.HANDLER_TIMEOUT        # §5.0: per-op inactivity сокета —
    # механизм StreamRequestHandler.setup() -> connection.settimeout(10);
    # НЕ общий wall-clock дедлайн (стриминг §5.9 легитимен, пока сокет активен).

    # --- единая точка ответа: security-заголовки на ВСЕ ответы (§5.0) ---

    def _start(self, code, extra):
        self.send_response(code)
        for k, v in SEC_HEADERS:
            self.send_header(k, v)
        for k, v in extra or ():
            self.send_header(k, v)

    def _respond(self, code, body, ctype, cache, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._start(code, extra)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        body = body.encode("utf-8")
        if len(body) > config.JSON_MAX_BYTES:          # §5.0: JSON <= 10 МБ
            alog("ERROR", f"json too large bytes={len(body)} path={self.path[:120]}")
            code, body = 500, b'{"error":"response too large"}'
        self._respond(code, body, "application/json; charset=utf-8", "no-store",
                      extra=extra)

    def _safe_json(self, code, obj):
        """Ответ из обработчика ошибок — молча, если клиент уже ушёл."""
        try:
            self._json(code, obj)
        except OSError:
            self.close_connection = True

    # --- Auth (§3) ---

    def _check_auth(self, ip):
        """-> (True, None) либо (False, отправленный_код). 429 — ДО проверки
        пароля; 401 обязан нести WWW-Authenticate, иначе браузер не покажет
        диалог ввода (§3). Лог — только user=, никогда не Authorization."""
        if self.server.creds is None:
            self._json(503, {"error": "auth not configured"})
            return False, 503
        if self.server.limiter.blocked(ip):
            self._respond(429, "rate limited: too many failed logins, retry after 60 s\n",
                          "text/plain; charset=utf-8", "no-store",
                          extra=(("Retry-After", "60"),))
            return False, 429
        user = None
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                raw = base64.b64decode(hdr[6:].strip()).decode("utf-8")
                u, p = raw.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                u = p = ""
            cu, cp = self.server.creds
            # bytes-сравнение: compare_digest на str требует ASCII и кидает TypeError.
            # Обе проверки ВСЕГДА выполняются (нет short-circuit) — иначе timing
            # различает неверный user от неверного password (перебор имён).
            ok_user = hmac.compare_digest(u.encode("utf-8"), cu.encode("utf-8"))
            ok_pass = hmac.compare_digest(p.encode("utf-8"), cp.encode("utf-8"))
            if ok_user and ok_pass:
                user = u
        if user is None:
            self.server.limiter.fail(ip)
            self._respond(401, "401 Unauthorized\n", "text/plain; charset=utf-8",
                          "no-store",
                          extra=(("WWW-Authenticate",
                                  f'Basic realm="{config.REALM}", charset="UTF-8"'),))
            return False, 401
        self.server.limiter.reset(ip)      # §3: успех сбрасывает счётчик неудач
        self._auth_user = user             # §9 (v1.2.3): user= в INFO authed-запросов
        return True, user

    # --- Статика с ETag/304/gzip (§5.0) ---

    def _serve_static(self, url):
        ent = self.server.static.get(url)
        if ent is None:
            self._json(404, {"error": "not found"})
            return 404
        is_html = ent["ctype"].startswith("text/html")
        # M-3 (ревью r1-r3): html/js/css — no-cache: ETag/304 уже реализованы,
        # ревалидация дешёвая; долгий кэш 24 ч — только бинарные ассеты
        # (.svg/.png/.ico/.woff2). Отклонение от §5.0 v1.2.2, патч v1.2.4 заказан.
        ext = os.path.splitext(url)[1].lower()
        cache = ("no-cache" if ext in (".html", ".js", ".css")
                 else "public, max-age=86400")
        extra = [("Vary", "Accept-Encoding")]
        inm = self.headers.get("If-None-Match")
        if inm and ent["etag"] in {t.strip() for t in inm.split(",")}:
            self._start(304, extra)
            self.send_header("ETag", ent["etag"])
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return 304
        if ent["gz"] and "gzip" in self.headers.get("Accept-Encoding", "").lower():
            body = ent["gz"]
            extra.append(("Content-Encoding", "gzip"))
        else:
            body = ent["raw"]
        extra.append(("ETag", ent["etag"]))   # §5.0: ETag и на 200 (клиент кэширует)
        if is_html:
            extra.append(("Content-Security-Policy", CSP))   # §5.0: CSP — на HTML
        self._respond(200, body, ent["ctype"], cache, extra=extra)
        return 200

    # --- Маршрутизация (§1: dict path -> handler) ---

    def do_GET(self):  # noqa: N802
        ip = self.client_address[0]           # §3: прямой REMOTE_ADDR, без XFF
        self._auth_user = None                # сброс per-request (keep-alive reuse!)
        t0 = time.monotonic()
        code = 500
        try:
            code = self._route(ip)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            code = 0                      # клиент ушёл — в лог INFO, без фейкового 500
            alog("INFO", f"client gone ip={ip} path={self.path[:120]}")
            return
        except sqlite3.Error as e:            # §8: БД недоступна -> 503 везде
            code = 503
            alog("ERROR", f"db unavailable err={type(e).__name__}:{e}")
            self._safe_json(503, {"error": "db unavailable"})
        except Exception as e:  # noqa: BLE001
            code = 500
            alog("ERROR", f"internal err={type(e).__name__}:{e} path={self.path[:120]}")
            self._safe_json(500, {"error": "internal error"})
        finally:
            if code:                      # code=0 — клиент ушёл, строка уже в логе
                ms = int((time.monotonic() - t0) * 1000)
                line = f"{self.command} {self.path[:200]} {code} {ms}ms ip={ip}"
                if self._auth_user:       # §9 (v1.2.3): только authed; 401/429 — ip=
                    line += f" user={self._auth_user}"
                if code >= 500:
                    alog("ERROR", line)           # §9: ERROR — 5xx
                elif code >= 400 or ms > 1000:
                    alog("WARN", line)            # §9: WARN — 4xx, > 1 с
                else:
                    alog("INFO", line)

    def _route(self, ip):
        u = urlparse(self.path)
        path = u.path
        if path == "/api/health":             # §5.8: без auth, не расширять
            return self._api_health()
        ok, code = self._check_auth(ip)
        if not ok:
            return code
        if path == "/api/now":
            return self._api_now()
        if path == "/api/history":
            return self._api_history(u.query)
        if path == "/api/meta":
            return self._api_meta()
        if path == "/api/events":
            return self._api_events(u.query)
        if path == "/api/hourly":
            return self._api_hourly(u.query)
        if path == "/api/daily":
            return self._api_daily(u.query)
        if path == "/api/forecast":
            return self._api_forecast()
        if path == "/api/export.csv":
            return self._api_export(u.query)          # U6-S1 (§5.9 v1.2.8)
        if path == "/api/settings":
            return self._api_settings()               # U6-S2 (§5.10 v1.2.8)
        if path == "/favicon.ico":            # чтобы не шуметь 404 в логе
            return self._serve_static("/static/icons/favicon.svg")
        page = PAGES.get(path)
        if page:
            return self._serve_static("/static/" + page)
        if path.startswith("/static/"):
            return self._serve_static(path)
        if path.startswith("/icons/"):        # алиас §2.1 на static/icons/
            return self._serve_static("/static" + path)
        self._json(404, {"error": "not found"})
        return 404

    def do_POST(self):  # noqa: N802
        """§3: POST -> 405, ЕДИНСТВЕННОЕ исключение — POST /api/check-db
        (U6-S3, §5.11 v1.2.8): PRAGMA quick_check по требованию.
        m-12 (ревью r1-r3): проходит через _check_auth как GET — лимит
        неудачных логинов (§3), 429/401 и лог применяются и к POST.
        r5-1 (ревью r5): close_connection=True — первой строкой, до auth:
        соединение обязано закрываться в ЛЮБОМ исходе POST (401/429/503
        тоже) — keep-alive с непрочитанным телом недопустим. Тело POST
        не читается (check-db тела не имеет) — соединение закрывается.
        r6-3 (ревью r6): auth-отказ (401/429/503) логируется WARN-строкой."""
        self.close_connection = True
        ip = self.client_address[0]
        self._auth_user = None
        ok, code = self._check_auth(ip)
        if not ok:
            # r6-3 (ревью r6): отказ auth логируется так же, как у GET
            # (do_GET, finally: 4xx -> WARN) — без user=, без ms (t0 нет).
            alog("WARN", f"{self.command} {self.path[:200]} {code} ip={ip}")
            return
        u = urlparse(self.path)
        if u.path == "/api/check-db":          # U6-S3 (§5.11 v1.2.8)
            t0 = time.monotonic()
            try:
                code = self._api_check_db(ip)
            except sqlite3.Error as e:          # §8: БД недоступна -> 503
                code = 503
                alog("ERROR", f"check-db db unavailable err={type(e).__name__}:{e}")
                self._safe_json(503, {"error": "db unavailable"})
            except Exception as e:  # noqa: BLE001
                code = 500
                alog("ERROR", f"internal err={type(e).__name__}:{e} "
                              f"path={self.path[:120]}")
                self._safe_json(500, {"error": "internal error"})
            ms = int((time.monotonic() - t0) * 1000)
            line = f"{self.command} {self.path[:200]} {code} {ms}ms ip={ip}"
            if self._auth_user:                 # §9 (v1.2.3): user= только authed
                line += f" user={self._auth_user}"
            if code >= 500:
                alog("ERROR", line)             # §9: ERROR — 5xx
            elif code >= 400 or ms > 1000:
                alog("WARN", line)              # §9: WARN — 4xx, > 1 с
            else:
                alog("INFO", line)
            return
        line = f"{self.command} {self.path[:200]} 405 ip={ip}"
        if self._auth_user:
            line += f" user={self._auth_user}"
        alog("WARN", line)
        self._respond(405, "405 method not allowed\n", "text/plain; charset=utf-8",
                      "no-store", extra=(("Allow", "GET"), ("Connection", "close")))

    def log_message(self, fmt, *args):  # noqa: A003 — свой формат в do_GET (§9)
        return

    # --- Параметры запроса ---

    @staticmethod
    def _safe_int(v):
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            return None
        return n if -MAX_EPOCH <= n <= MAX_EPOCH else None

    def _required_window(self, qs, max_len):
        frm = self._safe_int(qs.get("from", [None])[0])
        to = self._safe_int(qs.get("to", [None])[0])
        if frm is None or to is None:
            self._json(400, {"error": "from and to are required (epoch seconds)"})
            return None
        if frm >= to:
            self._json(400, {"error": "from must be < to"})
            return None
        if to - frm > max_len:
            self._json(400, {"error": f"window too large, max {max_len // 86400} days"})
            return None
        return frm, to

    @staticmethod
    def _csv_param(qs, name):
        raw = qs.get(name, [None])[0]
        if raw is None:
            return None
        return [s.strip() for s in raw.split(",") if s.strip()]

    # --- API (§5.1, §5.2, §5.7, §5.5) ---

    def _api_health(self):
        """§5.8: SELECT 1 + last_ts; 200 или 503; p99 < 10 мс; не расширять."""
        try:
            con = db_open(self.server.db_path)
            try:
                con.execute("SELECT 1")
                row = con.execute("SELECT MAX(ts) FROM weather").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            raise                          # 503 сделает do_GET
        self._json(200, {"status": "ok", "db": "ok", "last_ts": row[0] if row else None})
        return 200

    def _api_now(self):
        """§5.1: current + status(collector_ok, last_poll_ts, gap_s).
        Адаптация: таблицы current в схеме v2 нет — payload = последняя строка
        weather (как у этапа B weather_api.py /now). Пустая БД -> 503 (§5.1).
        +p_tendency_3h — для стрелки давления экрана «Сейчас» (§4.1); алгоритм
        тот же, что в этапе B. Отклонение задокументировано в REVIEW-U0U1.md."""
        now = int(time.time())
        con = db_open(self.server.db_path)
        try:
            # §5.1 (патч v1.2.3): явный список колонок (не SELECT *), без
            # id/schema_version; список — из whitelist PRAGMA на старте.
            cols = self.server.now_cols
            cur = con.execute(f"SELECT {', '.join(cols)} FROM weather "
                              "ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
            if row is None:
                self._json(503, {"error": "current is empty, collector has not written yet"})
                return 503
            payload = dict(zip(cols, row))
            p_tendency = window_delta(con, "pressure_rel_mmhg", now, 3)
        finally:
            con.close()
        last_ts = payload.get("ts") or 0
        gap = max(0, now - last_ts)
        status = {"collector_ok": gap < COLLECTOR_OK_GAP,
                  "last_poll_ts": last_ts, "gap_s": gap}
        self._json(200, {"now": now, "current": payload, "status": status,
                         "p_tendency_3h": p_tendency})
        return 200

    def _api_history(self, query):
        """§5.2: from/to обязательны, окно <= 7 д, fields — whitelist, LIMIT 50000,
        truncated при обрезании. rows — массивы, порядок = поле в fields."""
        qs = parse_qs(query, keep_blank_values=True)
        win = self._required_window(qs, HISTORY_WINDOW)
        if win is None:
            return 400
        frm, to = win
        fields = self._csv_param(qs, "fields")
        if fields is None:
            fields = list(HISTORY_DEFAULT_FIELDS)
        else:
            seen = []
            for f in fields:
                if f not in self.server.wcols:
                    self._json(400, {"error": "unknown field", "field": f})
                    return 400
                if f not in seen:
                    seen.append(f)
            fields = seen
            if not fields:
                fields = list(HISTORY_DEFAULT_FIELDS)
        cols = ", ".join(fields)   # имена из whitelist PRAGMA (§0.7); values — ?
        con = db_open(self.server.db_path)
        try:
            cur = con.execute(f"SELECT {cols} FROM weather WHERE ts>=? AND ts<=? "
                              "ORDER BY ts LIMIT ?", (frm, to, HISTORY_LIMIT + 1))
            rows = cur.fetchall()
        finally:
            con.close()
        truncated = len(rows) > HISTORY_LIMIT
        if truncated:
            rows = rows[:HISTORY_LIMIT]
        self._json(200, {"from": frm, "to": to, "fields": fields,
                         "rows": [list(r) for r in rows], "truncated": truncated})
        return 200

    def _api_hourly(self, query):
        """§5.3: окно <= 90 д, из v_hourly (A-2: реальные имена этапа B); fields —
        фикс. список ТЗ, пересечённый с PRAGMA на старте; rows — массивы в порядке
        fields (A-10). В конверт добавлен fields (дополнение B-1, аддитивно:
        клиенту не надо знать порядок колонок наизусть). Лимит строк не нужен:
        окно 90 д при hourly-гранулярности = <= 2160 строк."""
        qs = parse_qs(query, keep_blank_values=True)
        win = self._required_window(qs, HOURLY_WINDOW)
        if win is None:
            return 400
        frm, to = win
        fields = self.server.hourly_fields
        con = db_open(self.server.db_path)
        try:
            rows = con.execute(
                f"SELECT {', '.join(fields)} FROM v_hourly "
                "WHERE hour_epoch>=? AND hour_epoch<=? ORDER BY hour_epoch",
                (frm, to)).fetchall()
        finally:
            con.close()
        self._json(200, {"from": frm, "to": to, "fields": list(fields),
                         "rows": [list(r) for r in rows]})
        return 200

    def _api_daily(self, query):
        """§5.4: окно <= 365 д, из v_daily; единый конверт с /api/hourly
        ({from, to, fields, rows}); без truncated (<= 365 строк — ТЗ)."""
        qs = parse_qs(query, keep_blank_values=True)
        win = self._required_window(qs, DAILY_WINDOW)
        if win is None:
            return 400
        frm, to = win
        fields = self.server.daily_fields
        con = db_open(self.server.db_path)
        try:
            rows = con.execute(
                f"SELECT {', '.join(fields)} FROM v_daily "
                "WHERE day_epoch>=? AND day_epoch<=? ORDER BY day_epoch",
                (frm, to)).fetchall()
        finally:
            con.close()
        self._json(200, {"from": frm, "to": to, "fields": list(fields),
                         "rows": [list(r) for r in rows]})
        return 200

    def _api_forecast(self):
        """§5.6 (U5, v0.4.0; дельты ревью владельца — issued_values + letter):
        прогноз этапа B из таблицы forecast — формулы НЕ дублируем (принцип
        U5, ревью v2.1 п.8): пишет weather_aggregator (write_forecasts,
        hourly-прогон timer *:02:00, issued_at=int(now) прогона,
        UNIQUE(issued_at,target_ts,source)); читаем последний прогон целиком
        по MAX(issued_at) — без параметров (окно не нужно).

        Конверт (v1.2.6 §5.6):
        {available: true, calc_ts, age_s, stale,
        issued_values: {t_out_c, p_rel_mmhg} | null — БАЗА РАСЧЁТА (решение
        владельца «вариант (б)»): показания на issued_at — последний замер
        weather с ts <= issued_at (не «сейчас»!); null — истории до issued_at
        нет; клиент считает Δ от них, при null — Δ «—»,
        zambretti: {text, confidence, letter | null, targets: [{target_ts,
        horizon_h}] × 2 (+6ч/+12ч)} — letter пишет этап B (миграция
        forecast.letter, U5-B1); null — строки до миграции (легаси; клиент
        обязан переживать),
        sager: {target_ts, text, confidence} | null (ночью строка не пишется
        — 10<=h<16),
        persistence: [{target_ts, horizon_h, t_out_c, p_rel_mmhg, rh_out_pct,
        wind_ms, confidence}] × 3 (+1ч/+3ч/+6ч)}. Горизонт 24 ч — отклонён
        владельцем (не добавляем).

        Совместимость: колонка letter появилась позже текста — селектим её
        только если она уже есть в таблице (PRAGMA table_info); до прогона
        этапа B с миграцией letter во всех строках null, конверт не меняется.

        U5-S2: этап B пересчитывает ежечасно -> stale = возраст >
        FORECAST_STALE_S (2 пропущенных прогона подряд).
        Прогноза нет (пустая таблица) -> 200 {"available": false} — не 503:
        это не ошибка (§5.6), на живой VM не воспроизводится при живом этапе B.
        """
        con = db_open(self.server.db_path)
        try:
            calc_ts = con.execute("SELECT MAX(issued_at) FROM forecast").fetchone()[0]
            if calc_ts is None:
                self._json(200, {"available": False,
                                 "reason": "no forecast rows: aggregator has not run yet"})
                return 200
            # letter появилась позже text (U5-B1): селектим только если
            # колонка уже есть — до прогона этапа B с миграцией конверт
            # отдаётся с letter: null (поведение легаси, без 503)
            cols = {r[1] for r in con.execute("PRAGMA table_info(forecast)")}
            sel = ("target_ts, source, t_out_c, p_rel_mmhg, rh_out_pct, "
                   "wind_ms, confidence, text")
            if "letter" in cols:
                sel += ", letter"
            rows = con.execute(
                f"SELECT {sel} FROM forecast WHERE issued_at=? "
                "ORDER BY target_ts, source", (calc_ts,)).fetchall()
            # U5-U1 (вариант (б), решение владельца): базовые значения на
            # issued_at — последний замер ДО расчёта, не «сейчас»
            iv = con.execute(
                "SELECT outdoor_temp_c, pressure_rel_mmhg FROM weather "
                "WHERE ts<=? ORDER BY ts DESC LIMIT 1", (calc_ts,)).fetchone()
        finally:
            con.close()
        issued_values = ({"t_out_c": iv[0], "p_rel_mmhg": iv[1]} if iv else None)
        age_s = max(0, int(time.time()) - calc_ts)
        persistence, zam_targets = [], []
        zam_text, zam_conf, zam_letter, sager = None, None, None, None
        for row in rows:
            tts, src, t_out, p_rel, rh, wind, conf, text = row[:8]
            letter = row[8] if len(row) > 8 else None
            hz = max(0, (tts - calc_ts) // 3600)
            if src == "persistence":
                persistence.append({"target_ts": tts, "horizon_h": hz,
                                    "t_out_c": t_out, "p_rel_mmhg": p_rel,
                                    "rh_out_pct": rh, "wind_ms": wind,
                                    "confidence": conf})
            elif src == "zambretti":
                if zam_text is None:
                    zam_text, zam_conf, zam_letter = text, conf, letter
                zam_targets.append({"target_ts": tts, "horizon_h": hz})
            elif src == "sager_day" and sager is None:
                sager = {"target_ts": tts, "text": text, "confidence": conf}
        zambretti = (None if zam_text is None else
                     {"text": zam_text, "confidence": zam_conf,
                      "letter": zam_letter, "targets": zam_targets})
        self._json(200, {"available": True, "calc_ts": calc_ts,
                         "age_s": age_s, "stale": age_s > FORECAST_STALE_S,
                         "issued_values": issued_values,
                         "zambretti": zambretti, "sager": sager,
                         "persistence": persistence})
        return 200

    def _api_meta(self):
        """§5.7: wmeta + последняя schema_migrations + 20 collector_log + db_health
        (ключ wmeta db_health пишет материализатор — на момент v1.2.2 может
        отсутствовать, тогда null; клиент показывает «не проводилась»).
        m-5 (ревью r1-r3): каждая таблица — отдельным try/except: отсутствие
        wmeta/schema_migrations/collector_log деградирует значение с WARN,
        но не валит /api/meta в 503 — погода при этом живёт. Отказ БД на
        коннекте (db_open) по-прежнему уходит в 503 (do_GET)."""
        con = db_open(self.server.db_path)
        try:
            try:
                wmeta = {k: v for k, v in con.execute("SELECT key, value FROM wmeta")}
            except sqlite3.OperationalError as e:
                wmeta = {}
                alog("WARN", f"meta: wmeta unavailable, degraded err={e}")
            try:
                mig = con.execute("SELECT version, applied_at, description "
                                  "FROM schema_migrations ORDER BY version DESC "
                                  "LIMIT 1").fetchone()
            except sqlite3.OperationalError as e:
                mig = None
                alog("WARN", f"meta: schema_migrations unavailable, degraded err={e}")
            try:
                clog = con.execute("SELECT ts, status, latency_ms, bytes, error "
                                   "FROM collector_log ORDER BY ts DESC "
                                   "LIMIT 20").fetchall()
            except sqlite3.OperationalError as e:
                clog = []
                alog("WARN", f"meta: collector_log unavailable, degraded err={e}")
        finally:
            con.close()
        db_health = None
        raw = wmeta.get("db_health")
        if raw:
            try:
                db_health = json.loads(raw)
            except ValueError:
                db_health = None
                alog("WARN", "meta: broken db_health in wmeta (len="
                              f"{len(raw)})")
        self._json(200, {
            "wmeta": wmeta,
            "schema_migrations": ({"version": mig[0], "applied_at": mig[1],
                                   "description": mig[2]} if mig else None),
            "collector_log": [dict(zip(("ts", "status", "latency_ms", "bytes",
                                        "error"), r)) for r in clog],
            "db_health": db_health,
        })
        return 200

    def _api_export(self, query):
        """§5.9 (v1.2.8, U6-S1): GET /api/export.csv — CSV-стриминг сырых таблиц.
        type=history|hourly|daily -> weather / v_hourly / v_daily (константы
        EXPORT_TYPES); fields= — whitelist PRAGMA (дефолт: все колонки таблицы;
        для weather исключены сервисные id/schema_version — дисциплина §5.1);
        separator=;|, (дефолт ; — Excel, RFC 4180 lineterminator=\\r\\n);
        limit= — опциональный LIMIT (пробник U6-C2; валиден 1..100000;
        отсутствует -> без LIMIT; НЕ-ЧИСЛО -> 400, молча игнорировать
        нельзя — вердикт B-1 ревьювера U6: тихий полный экспорт при
        опечатке клиента не является честным контрактом).

        Pre-COUNT ДО стриминга: BEGIN (единый WAL-снапшот COUNT+SELECT) ->
        COUNT(*) по ТОМУ ЖЕ WHERE -> оценка байт = строки × поля ×
        EXPORT_AVG_FIELD_BYTES(10) -> > EXPORT_MAX_BYTES (300 МБ) -> 413 +
        X-Export-Rows (фактическое число строк) ДО первого байта CSV. Окно
        > 366 д -> 400 (гвард _required_window): 413 — про РАЗМЕР, 400 — про
        ОКНО (ответ на вопрос U6-T1 ревьювера).

        Стриминг: chunked (Transfer-Encoding: chunked, БЕЗ Content-Length),
        батчи EXPORT_CHUNK_ROWS=500 строк через fetchmany, csv.writer в
        StringIO (quoting QUOTE_MINIMAL = RFC 4180), Connection: close
        (send_header сам ставит close_connection — keep-alive после chunked
        не открываем).

        ЖИЗНЬ КОННЕКТА (решение S1, задание U6): коннект открывается ПОСЛЕ
        валидаций и закрывается в finally ПОСЛЕ конца/обрыва стрима — он живёт
        РОВНО столько, сколько живёт обработчик (генератор наружу не
        возвращается — цикл fetchmany/write ведёт сам обработчик). Семафорный
        слот (acquire в process_request до потока, release в
        process_request_thread после обработчика) занят всё это время — это
        ЕДИНСТВЕННЫЙ long-lived обработчик (§5.0 v1.2.8); зависший клиент
        рвётся HANDLER_TIMEOUT (per-op inactivity: write в полный TCP-буфер
        даёт socket.timeout) — слот освобождается, утечки нет. WAL: длинный
        read-стрим не блокирует коллектора.

        Ошибка БД ДО заголовков (db_open/BEGIN/COUNT/SELECT) — штатный 503
        через do_GET. Ошибка БД ПОСЛЕ заголовков — честный 503 уже невозможен:
        best-effort терминальный chunk (файл обрезан), ERROR в лог, код 200
        (статус уже отправлен).

        CSV-injection (§5.9 v1.2.8): значения колонок с decltype TEXT
        (по PRAGMA на старте; фактически weather.battery_raw,
        v_hourly/v_daily.wind_dir_mode) при первом символе из = + - @
        префиксуются апострофом; числовые колонки НЕ трогаем (минус
        температуры легитимен). NULL -> пустая ячейка (csv.writer пишет None
        как ""). BOM не пишется (A10/U8; параметр ?bom из v1.2.7 отменён).
        """
        qs = parse_qs(query, keep_blank_values=True)
        win = self._required_window(qs, EXPORT_WINDOW)
        if win is None:
            return 400
        frm, to = win
        etype = (qs.get("type", ["history"])[0] or "history").strip()
        if etype not in EXPORT_TYPES:
            self._json(400, {"error": "unknown type",
                             "allowed": list(EXPORT_TYPES)})
            return 400
        table, tcol = EXPORT_TYPES[etype]
        sep = qs.get("separator", [";"])[0]
        if sep not in (";", ","):
            self._json(400, {"error": "unknown separator",
                             "allowed": [";", ","]})
            return 400
        raw_limit = qs.get("limit", [None])[0]
        limit = self._safe_int(raw_limit)
        # не-число (в т.ч. вне ±MAX_EPOCH) != отсутствующий параметр:
        # raw есть, а int распарсить не удалось -> 400 (вердикт B-1 U6)
        if ((raw_limit is not None and limit is None) or
                (limit is not None and
                 not (1 <= limit <= EXPORT_MAX_LIMIT))):
            self._json(400, {"error": "invalid limit", "max": EXPORT_MAX_LIMIT})
            return 400
        all_cols = self.server.export_cols[table]        # whitelist старта
        text_cols = self.server.export_text[table]
        fields = self._csv_param(qs, "fields")
        if fields is None:
            fields = list(all_cols)
        else:
            seen = []
            for f in fields:
                if f not in all_cols:
                    self._json(400, {"error": "unknown field", "field": f})
                    return 400
                if f not in seen:
                    seen.append(f)
            fields = seen
        text_idx = [i for i, f in enumerate(fields) if f in text_cols]
        # §0.7: имена — из whitelist старта (load_table_columns); values — ?
        cols_sql = ", ".join(fields)
        lim_sql, lim_param = ((" LIMIT ?", limit) if limit is not None
                              else ("", None))
        fname = ("weather-%s-%s-%s.csv" % (
            etype,
            datetime.fromtimestamp(frm, tz=timezone.utc).strftime("%Y-%m-%d"),
            datetime.fromtimestamp(to, tz=timezone.utc).strftime("%Y-%m-%d")))
        con = db_open(self.server.db_path)
        try:
            # единый read-снапшот для COUNT + SELECT (изоляция авто-commit
            # дала бы два разных снапшота; read-txn не мешает коллектору — WAL)
            con.execute("BEGIN")
            count = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {tcol}>=? AND {tcol}<=?",
                (frm, to)).fetchone()[0]
            est = count * len(fields) * EXPORT_AVG_FIELD_BYTES
            if est > self.server.export_max_bytes:
                # §5.9: честная ошибка ДО первого байта CSV; заголовок несёт
                # фактическое число строк (задание U6-S1)
                self._json(413, {"error": "export too large", "rows": count,
                                 "estimated_bytes": est,
                                 "limit_bytes": self.server.export_max_bytes},
                           extra=(("X-Export-Rows", str(count)),))
                return 413
            cur = con.execute(
                f"SELECT {cols_sql} FROM {table} WHERE {tcol}>=? AND {tcol}<=? "
                f"ORDER BY {tcol}{lim_sql}",
                (frm, to, limit) if lim_param else (frm, to))
            self._start(200, (
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("Content-Disposition", f'attachment; filename="{fname}"'),
                ("Transfer-Encoding", "chunked"),
                ("Connection", "close"),   # send_header ставит close_connection
            ))
            self.end_headers()
            try:
                wbuf = io.StringIO()
                cw = csv.writer(wbuf, delimiter=sep, lineterminator="\r\n")
                cw.writerow(fields)
                self._chunk(wbuf.getvalue().encode("utf-8"))
                n_sent = 0
                while True:
                    rows = cur.fetchmany(EXPORT_CHUNK_ROWS)
                    if not rows:
                        break
                    wbuf = io.StringIO()
                    cw = csv.writer(wbuf, delimiter=sep, lineterminator="\r\n")
                    if text_idx:
                        for r in rows:
                            r = list(r)
                            for i in text_idx:
                                v = r[i]
                                if v is not None:
                                    s = str(v)
                                    if s[:1] in CSV_INJECT_CHARS:
                                        s = "'" + s
                                    r[i] = s
                            cw.writerow(r)
                    else:
                        cw.writerows(rows)
                    self._chunk(wbuf.getvalue().encode("utf-8"))
                    n_sent += len(rows)
                self.wfile.write(b"0\r\n\r\n")
            except sqlite3.Error:            # §5.9 v1.2.8: после заголовков
                # честный 503 невозможен — файл обрезается терминальным chunk
                alog("ERROR", f"export aborted mid-stream rows={n_sent} "
                              f"type={etype}")
                try:
                    self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    pass                     # клиент уже ушёл
                self.close_connection = True
                return 200                   # статус уже отправлен (200)
        finally:
            con.close()                      # S1: close ПОСЛЕ генератора
        return 200

    def _chunk(self, data):
        """Один chunked-фрейм RFC 7230: <hex-len>\\r\\n data \\r\\n."""
        self.wfile.write(("%x\r\n" % len(data)).encode("ascii")
                         + data + b"\r\n")
        self.wfile.flush()

    def _api_settings(self):
        """§5.10 (v1.2.8, U6-S2): конверт = /api/meta, но wmeta ФИЛЬТРУЕТСЯ
        на СЕРВЕРЕ по WMETA_VISIBLE (units, tz, tz_offset_seconds, gdd_tbase_c,
        ok_total, err_total, last_ok, schema_version — ровно список ревьювера
        U6-S2): пути/station_ip/mac на экран Настроек не попадают.
        /api/meta НЕ тронут: контракт §5.7 («wmeta объект» целиком) документи-
        рован и используется всеми страницами (app.js берёт оттуда TZ) —
        фильтр там был бы ломающим изменением (решение S2, отчёт U6).
        db_health идёт отдельным ключом конверта (как в /api/meta) — парсится
        из НЕфильтрованного словаря: он не элемент wmeta-карточек.
        Degradation по таблицам — как в _api_meta (m-5): отсутствие
        wmeta/schema_migrations/collector_log деградирует значение с WARN,
        но не валит эндпоинт в 503."""
        con = db_open(self.server.db_path)
        try:
            try:
                raw_wmeta = {k: v for k, v in con.execute(
                    "SELECT key, value FROM wmeta")}
            except sqlite3.OperationalError as e:
                raw_wmeta = {}
                alog("WARN", f"settings: wmeta unavailable, degraded err={e}")
            try:
                mig = con.execute("SELECT version, applied_at, description "
                                  "FROM schema_migrations ORDER BY version DESC "
                                  "LIMIT 1").fetchone()
            except sqlite3.OperationalError as e:
                mig = None
                alog("WARN", f"settings: schema_migrations unavailable, degraded err={e}")
            try:
                clog = con.execute("SELECT ts, status, latency_ms, bytes, error "
                                   "FROM collector_log ORDER BY ts DESC "
                                   "LIMIT 20").fetchall()
            except sqlite3.OperationalError as e:
                clog = []
                alog("WARN", f"settings: collector_log unavailable, degraded err={e}")
        finally:
            con.close()
        wmeta = {k: v for k, v in raw_wmeta.items() if k in WMETA_VISIBLE}
        db_health = None
        raw = raw_wmeta.get("db_health")
        if raw:
            try:
                db_health = json.loads(raw)
            except ValueError:
                db_health = None
                alog("WARN", "settings: broken db_health in wmeta (len="
                              f"{len(raw)})")
        self._json(200, {
            "wmeta": wmeta,
            "schema_migrations": ({"version": mig[0], "applied_at": mig[1],
                                   "description": mig[2]} if mig else None),
            "collector_log": [dict(zip(("ts", "status", "latency_ms", "bytes",
                                        "error"), r)) for r in clog],
            "db_health": db_health,
        })
        return 200

    def _api_check_db(self, ip):
        """§5.11 (v1.2.8, U6-S3) — ЕДИНСТВЕННЫЙ POST системы. PRAGMA quick_check
        на query_only-коннекте: это ЧТЕНИЕ — принцип «UI не пишет в БД» не
        нарушен (§0.4). РЕШЕНИЕ S3 (задание U6 — ДОКЛАД ДО РЕАЛИЗАЦИИ):
        «кэшировать результат в wmeta db_health» ОТКЛОНЕНО — это была бы
        ЗАПИСЬ из UI; grep по репо: db_health не пишет НИКТО (этап B кладёт
        только last_agg_hourly/daily_epoch, weather_aggregator.py:376/393) —
        владелец ключа этап B; POST отвечает СВОИМ результатом синхронно и
        ни читает, ни триггерит чужие записи.
        Таймаут: statement-timeout в sqlite3 нет -> set_progress_handler
        (_tick, CHECK_DB_PROGRESS_OPS): дедлайн time.monotonic()+timeout,
        прерывание -> OperationalError 'interrupted' -> 503
        {"error": "check timed out"}; timeout — config.CHECK_DB_TIMEOUT (60 с),
        CLI --check-timeout для тестов (verify local: 0 -> гарантированный
        прерыватель без тяжёлой фикстуры).
        Rate-limit 1/мин/IP (тот же RateLimiter, отдельный инстанс): попытка
        фиксируется ДО работы — долбёжка таймаутами тоже стоит слота;
        повтор в пределах минуты -> 429 + Retry-After: 60 (U6-T2).
        Таймаут <= 0 -> 503 немедленно (детерминированный тест-крючок U6-T2:
        quick_check на пустой/крошечной БД укладывается в < 1000 VM-операций,
        progress-handler не успевает сработать — потому pre-check обязателен).
        Ответ 200: {checked_at, duration_ms, status: ok|errors, rows[<=50],
        row_count, truncated} — quick_check отдаёт 'ok' или список проблем."""
        if self.server.check_limiter.blocked(ip):
            self._json(429, {"error": "rate limited: 1 request per minute"},
                       extra=(("Retry-After", "60"),))
            return 429
        self.server.check_limiter.fail(ip)     # попытка фиксируется ДО работы
        if self.server.check_timeout <= 0:
            alog("WARN", f"check-db timed out pre-check timeout={self.server.check_timeout}s")
            self._json(503, {"error": "check timed out"})
            return 503
        deadline = time.monotonic() + self.server.check_timeout

        def _tick():
            return 1 if time.monotonic() > deadline else 0

        t0 = time.monotonic()
        con = db_open(self.server.db_path)     # query_only=ON (§0.4)
        try:
            con.set_progress_handler(_tick, CHECK_DB_PROGRESS_OPS)
            try:
                all_rows = [r[0] for r in con.execute("PRAGMA quick_check")]
            except sqlite3.OperationalError as e:
                if "interrupt" in str(e).lower():
                    alog("WARN", f"check-db timed out timeout={self.server.check_timeout}s")
                    self._json(503, {"error": "check timed out"})
                    return 503
                raise                          # прочая ошибка БД -> do_POST 503
        finally:
            con.close()
        ms = int((time.monotonic() - t0) * 1000)
        truncated = len(all_rows) > CHECK_DB_MAX_ROWS
        status = "ok" if all_rows == ["ok"] else "errors"
        self._json(200, {"checked_at": int(time.time()), "duration_ms": ms,
                         "status": status, "rows": all_rows[:CHECK_DB_MAX_ROWS],
                         "row_count": len(all_rows), "truncated": truncated})
        return 200

    def _api_events(self, query):
        """§5.5: окно <= 90 д, types/severity CSV по фиксированным спискам,
        LIMIT 5000 + truncated; единый конверт {from, to, rows, truncated};
        context парсим на сервере: битый -> null + WARN с event_id и фрагментом
        <= 100 символов (один мусорный ряд не валит эндпоинт).
        v0.3.0 (канон §5.5, патч v1.2.3): семантика окна — ПЕРЕКРЫТИЕ:
        видно событие, начавшееся внутри окна, ЛИБО начавшееся раньше и
        открытое/закрывшееся после from; include_open отменён."""
        qs = parse_qs(query, keep_blank_values=True)
        win = self._required_window(qs, EVENTS_WINDOW)
        if win is None:
            return 400
        frm, to = win
        types = self._csv_param(qs, "types") or []
        types = [t.upper() for t in types]
        if any(t not in EVENT_TYPES for t in types):
            self._json(400, {"error": "unknown type", "allowed": list(EVENT_TYPES)})
            return 400
        severities = [s.lower() for s in (self._csv_param(qs, "severity") or [])]
        if any(s not in EVENT_SEVERITIES for s in severities):
            self._json(400, {"error": "unknown severity",
                             "allowed": list(EVENT_SEVERITIES)})
            return 400
        # v0.3.0 (§5.5): overlap-окно. ВСЁ OR-условие — во внешних скобках, ДО
        # дописывания "AND event_type IN (...)" ниже: без скобок приоритет AND
        # над OR применит IN-фильтр только к правой ветке, и события внутри
        # окна потекут мимо фильтра. Параметры по порядку: frm, to, frm, frm.
        where = ("((ts_start >= ? AND ts_start <= ?) "
                 "OR (ts_start < ? AND (ts_end IS NULL OR ts_end >= ?)))")
        params = [frm, to, frm, frm]
        if types:
            where += " AND event_type IN (%s)" % ",".join(["?"] * len(types))
            params += types
        if severities:
            where += " AND severity IN (%s)" % ",".join(["?"] * len(severities))
            params += severities
        params.append(EVENTS_LIMIT + 1)
        con = db_open(self.server.db_path)
        try:
            cur = con.execute("SELECT id, ts_start, ts_end, event_type, severity, "
                              f"value, context FROM events WHERE {where} "
                              "ORDER BY ts_start DESC LIMIT ?", params)
            raw_rows = cur.fetchall()
        finally:
            con.close()
        truncated = len(raw_rows) > EVENTS_LIMIT
        if truncated:
            raw_rows = raw_rows[:EVENTS_LIMIT]
        rows = []
        for (eid, ts_start, ts_end, etype, sev, value, ctx_raw) in raw_rows:
            ctx = None
            if ctx_raw:
                try:
                    ctx = json.loads(ctx_raw)
                except ValueError:
                    # §5.5: битый context -> null + WARN с event_id и <= 100 симв.
                    frag = ctx_raw[:100].replace("\n", " ").replace("\r", " ")
                    alog("WARN", f"events: broken context event_id={eid} "
                                 f"fragment={frag!r}")
            rows.append({
                "id": eid, "ts_start": ts_start, "ts_end": ts_end,
                "event_type": etype, "severity": sev, "value": value,
                "context": ctx,
                "duration_s": (ts_end - ts_start) if ts_end is not None else None,
            })
        self._json(200, {"from": frm, "to": to, "rows": rows, "truncated": truncated})
        return 200


# ---------------- main (§2.2: graceful shutdown) ----------------

def main(argv):
    db_path = config.DB_PATH
    port = config.PORT
    hosts = list(config.BIND_HOSTS)
    cred_file = config.CRED_FILE
    static_root = config.STATIC_ROOT
    check_timeout = config.CHECK_DB_TIMEOUT   # §5.11 (CLI-оверрайд для тестов)
    export_max_bytes = config.EXPORT_MAX_BYTES  # §5.9 (CLI-оверрайд для тестов)
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--db" and args:
            db_path = args.pop(0)
        elif a == "--port" and args:
            port = int(args.pop(0))
        elif a == "--bind" and args:       # локальные тесты/смена интерфейса
            hosts = [args.pop(0)]
        elif a == "--cred" and args:
            cred_file = args.pop(0)
        elif a == "--static" and args:    # локальные тесты
            static_root = args.pop(0)
        elif a == "--check-timeout" and args:   # U6-T2: тест таймаут-пути
            check_timeout = float(args.pop(0))
        elif a == "--export-max-bytes" and args:  # U6-T1: 413-путь с малым лимитом
            export_max_bytes = int(args.pop(0))
        else:
            print("usage: server.py [--db PATH] [--port N] [--bind IP] [--cred FILE] "
                  "[--static DIR] [--check-timeout SEC] [--export-max-bytes N]", file=sys.stderr)
            return 2

    creds = load_creds(cred_file)
    if creds is None:
        alog("ERROR", "auth disabled: cred file unreadable — authed endpoints -> 503")
    static_map = load_static(static_root)
    wcols_list = load_weather_columns(db_path)
    wcols = frozenset(wcols_list)
    # m-4 (ревью r1-r3): дефолтные поля /api/history валидируются на старте —
    # дрейф схемы не должен превращать каждый дефолтный запрос в 503
    # (дисциплина как у load_agg_fields: ERROR + exit).
    unknown = [f for f in HISTORY_DEFAULT_FIELDS if f not in wcols]
    if unknown:
        alog("ERROR", f"startup: HISTORY_DEFAULT_FIELDS unknown={','.join(unknown)} "
                      f"db={db_path}")
        sys.exit(1)
    now_cols = [c for c in wcols_list if c not in NOW_EXCLUDE]   # §5.1 v1.2.3
    hourly_fields = load_agg_fields(db_path, "v_hourly", HOURLY_FIELDS)   # §5.3
    daily_fields = load_agg_fields(db_path, "v_daily", DAILY_FIELDS)      # §5.4
    # U6-S1 (§5.9 v1.2.8): whitelist колонок export по PRAGMA на старте + карта
    # TEXT-колонок для CSV-injection-гварда (weather: minus id/schema_version
    # — дисциплина NOW_EXCLUDE §5.1; battery_raw остаётся — он в §4.0)
    export_cols, export_text = {}, {}
    for tbl in ("weather", "v_hourly", "v_daily"):
        info = load_table_columns(db_path, tbl)
        export_cols[tbl] = [c for c, _t in info
                            if not (tbl == "weather" and c in NOW_EXCLUDE)]
        export_text[tbl] = frozenset(c for c, t in info if "TEXT" in t)
    limiter = RateLimiter(config.RATE_LIMIT, config.RATE_WINDOW)
    check_limiter = RateLimiter(config.CHECK_DB_RATE, config.CHECK_DB_RATE_WINDOW)

    semaphore = threading.BoundedSemaphore(config.MAX_CONCURRENT)
    servers = []
    for host in hosts:
        try:
            httpd = LimitedThreadingHTTPServer((host, port), UiHandler,
                                               semaphore=semaphore)
        except OSError as e:
            alog("ERROR", f"bind failed host={host} port={port} err={e} "
                          "(§12.3 v1.2.3: занят 8089 -> установщик переключает на 8091)")
            for s in servers:
                s.server_close()
            return 1
        httpd.db_path = db_path
        httpd.static = static_map
        httpd.creds = creds
        httpd.limiter = limiter
        httpd.check_limiter = check_limiter
        httpd.check_timeout = check_timeout
        httpd.export_max_bytes = export_max_bytes
        httpd.wcols = wcols
        httpd.now_cols = now_cols
        httpd.hourly_fields = hourly_fields
        httpd.daily_fields = daily_fields
        httpd.export_cols = export_cols
        httpd.export_text = export_text
        servers.append(httpd)

    # §2.2: shutdown() из обработчика сигнала — в ОТДЕЛЬНОМ потоке
    # (вызов shutdown() из потока serve_forever() — deadlock).
    for sig in (signal.SIGTERM, signal.SIGINT):

        def _stop(signum, _frame):
            alog("INFO", f"signal={signum} action=shutdown")
            for s in servers:
                threading.Thread(target=s.shutdown, daemon=True).start()

        signal.signal(sig, _stop)

    threads = []
    for s in servers:
        t = threading.Thread(target=s.serve_forever, name="serve", daemon=False)
        t.start()
        threads.append(t)
    alog("INFO", f"start version={SERVER_VERSION} bind={','.join(hosts)} "
                 f"port={port} db={db_path} auth={'yes' if creds else 'NO'} "
                 f"whitelist={len(wcols)}cols")
    for t in threads:
        t.join()
    for s in servers:
        s.server_close()      # §2.2: после выхода из serve_forever()
    alog("INFO", "stop clean=1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
