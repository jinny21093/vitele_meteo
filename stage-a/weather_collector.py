#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_collector.py v2.0.1 — этап A + правки «вдогонку» DeepSeek (weather-7). stdlib-only.

weather-7 (v2.0→v2.0.1): battery — whitelist известных OK-строк вместо regex \bok\b
(тот матчит «not ok» — ложный «OK»); sanity — границы ПО КАЖДОМУ ПОЛЮ (единые
550–850 мм рт.ст. шире физики Земли); push down при sanity_fail — в msg поле-нарушитель.

Демон (systemd weather-collector.service): цикл раз в 60 с, выравнивание на
границу минуты +2 с. SIGTERM — плавное завершение после текущего цикла.

- GET /client?command=record (таймаут 8 с, 1 ретрай через 3 с), JSON декод ЯВНО utf-8
- Схема v2: weather(id PK AUTOINCREMENT, ts UNIQUE-индекс, 23 REAL, battery_raw,
  L1-поля), PRAGMA per-connection: busy_timeout=5000, synchronous=NORMAL,
  journal_size_limit=64M (WAL ставится один раз при миграции — персистентен)
- L1 в коллекторе: точка росы (Magnus), wind chill (JAG/TI), heat index (Rothfusz),
  16 румбов, ΔP, wind run (dt<=300 c), расчётный rain rate (окно 10 мин, квант
  типпера 0.1 мм), rain_event, RAIN_COUNTER_RESET (отрицательная дельта total)
- collector_log (ok|sanity_fail|timeout|parse_error|http_5xx|http_other)
- sanity-чек: границы по полям (SANITY ниже), ts >= last_ts-5. Провал -> статус
  sanity_fail + collector_log.error=«поле=знач;...» + Kuma push down с msg
  «sanity_fail: поле=знач,...» (до 6 полей; строка в weather сохраняется)
- События этапа A: FROST, HARD_FREEZE, BATTERY_LOW (whitelist OK-строк,
  fail-safe: пусто/неизвестно = LOW), SENSOR_MISSING
  (gap > 600 c), RAPID_TEMP_DROP/RISE (±5 C за ~1 ч, не чаще раза в 3 ч),
  RAIN_COUNTER_RESET
- Kuma Push-монитор: GET /api/push/<token>?status=&msg=&ping=; URL из
  kuma_push.conf рядом с БД (строка url=...); файла нет — push не выполняется
- Единицы — как на станции (мм рт. ст.); смена единиц -> WARN + wmeta.units_prev
"""
import json
import math
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

STATION_URL = "http://192.168.8.101/client?command=record"
BASE_DIR = "/home/auditbot/weather-dash"
DB = os.path.join(BASE_DIR, "weather.db")
KUMA_CONF = os.path.join(BASE_DIR, "kuma_push.conf")
PERIOD = 60          # сек между циклами (решение D2: 60 с, не 30)
OFFSET = 2           # смещение от границы минуты (не совпадать с :00 cron-джобами)
SCHEMA_VERSION = 2

MAP = {
    ("Indoor", "Temperature"): "indoor_temp_c",
    ("Indoor", "Humidity"): "indoor_hum_pct",
    ("Outdoor", "Temperature"): "outdoor_temp_c",
    ("Outdoor", "Humidity"): "outdoor_hum_pct",
    ("Pressure", "Absolute"): "pressure_abs_mmhg",
    ("Pressure", "Relative"): "pressure_rel_mmhg",
    ("Wind Speed", "Max Daily Gust"): "wind_max_daily_ms",
    ("Wind Speed", "Wind"): "wind_ms",
    ("Wind Speed", "Gust"): "gust_ms",
    ("Wind Speed", "Direction"): "wind_dir_deg",
    ("Wind Speed", "Wind Average 2 Minute"): "wind_avg2_ms",
    ("Wind Speed", "Direction Average 2 Minute"): "wind_dir_avg2_deg",
    ("Wind Speed", "Wind Average 10 Minute"): "wind_avg10_ms",
    ("Wind Speed", "Direction Average 10 Minute"): "wind_dir_avg10_deg",
    ("Rainfall", "Rate"): "rain_rate_mmh",
    ("Rainfall", "Hour"): "rain_hour_mm",
    ("Rainfall", "Day"): "rain_day_mm",
    ("Rainfall", "Week"): "rain_week_mm",
    ("Rainfall", "Month"): "rain_month_mm",
    ("Rainfall", "Year"): "rain_year_mm",
    ("Rainfall", "Total"): "rain_total_mm",
    ("Solar", "Light"): "light_wm2",
    ("Solar", "UVI"): "uvi",
}
NUM_COLS = list(MAP.values())
L1_COLS = ["dew_point_c", "wind_chill_c", "heat_index_c", "wind_dir_card",
           "wind_dir_avg2_card", "wind_dir_avg10_card", "p_delta_mmhg",
           "wind_run_m", "rain_rate_calc_mmh", "rain_event"]
DIRS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
          "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

# sanity-границы v2.0.1 (weather-7, «вдогонка» DeepSeek §2 — ПО КАЖДОМУ ПОЛЮ):
# единые 550–850 мм рт.ст. = 733–1133 гПа шире любых физически возможных значений
# (рекорды ~870/1084 гПа) — проверка теряла смысл. Микро-правка агента: indoor нижняя
# граница -45 (неотапливаемая дача в Карелии, с. Видлица — редкие морозы до ~-40;
# решение владельца 16.09; у DeepSeek -20, первая оценка агента -30), gust 75 (аномалия).
SANITY = {
    "outdoor_temp_c": (-60, 60), "indoor_temp_c": (-45, 50),
    "outdoor_hum_pct": (0, 100), "indoor_hum_pct": (0, 100),
    "pressure_abs_mmhg": (500, 820),   # высота до ~3000 м над у.м.
    "pressure_rel_mmhg": (680, 820),   # приведено к уровню моря
    "wind_ms": (0, 75), "gust_ms": (0, 75),
    "wind_avg2_ms": (0, 75), "wind_avg10_ms": (0, 75),
    "wind_max_daily_ms": (0, 90), "rain_rate_mmh": (0, 500),
    "light_wm2": (0, 1500), "uvi": (0, 15),
}

# battery — whitelist (weather-7): regex \bok\b матчит «not ok» -> ложный OK.
# Всё, что НЕ содержит известную OK-строку целиком, = LOW (fail-safe в верную
# сторону). Новые OK-варианты дополнять сюда И в weatherstation.md.
BATTERY_OK_PATTERNS = (
    "all battery are ok",   # единственная известная OK-строка станции
)


def battery_is_ok(raw):
    """Fail-safe: пусто/неизвестно = LOW (DeepSeek «вдогонка» §1)."""
    if not raw:
        return False
    low = raw.lower()
    return any(p in low for p in BATTERY_OK_PATTERNS)


RAPID_DEDUP_SEC = 3 * 3600     # RAPID_TEMP_* не чаще раза в 3 ч
MISSING_GAP_SEC = 600          # SENSOR_MISSING при gap > 10 мин


def log(msg):
    print(f"[weather] {msg}", flush=True)


def kuma_url():
    env = os.environ.get("KUMA_PUSH")
    if env:
        return env
    try:
        with open(KUMA_CONF, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("url=") and len(line) > 4:
                    return line[4:]
    except OSError:
        pass
    return ""


KUMA = kuma_url()


def kuma_push(status, msg, ping_ms=None):
    if not KUMA:
        return
    q = {"status": status, "msg": msg[:120]}
    if ping_ms is not None:
        q["ping"] = str(max(0, int(ping_ms)))
    try:
        req = urllib.request.Request(
            KUMA + "?" + urllib.parse.urlencode(q),
            headers={"User-Agent": "weather-collector/2"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # noqa: BLE001 — push не должен валить цикл
        log(f"WARN kuma push: {e}")


def fetch_once():
    """-> (raw, latency_s) или (None, (status, err))."""
    t0 = time.monotonic()
    req = urllib.request.Request(
        STATION_URL, headers={"User-Agent": "weather-collector/2"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
        return raw, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        st = "http_5xx" if e.code >= 500 else "http_other"
        return None, (st, f"HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        return None, ("timeout", str(e)[:120])


def fetch():
    """1 ретрай. -> (data|None, status, err, latency_s, nbytes)."""
    raw, x = fetch_once()
    if raw is None:
        time.sleep(3)
        raw, x2 = fetch_once()
        if raw is None:
            return None, x2[0], x2[1], 0.0, 0
        x = x2
    try:
        return json.loads(raw.decode("utf-8")), "ok", None, x, len(raw)
    except Exception as e:  # noqa: BLE001
        return None, "parse_error", str(e)[:120], x, len(raw)


def flatten(data):
    """JSON станции -> (row: dict 23 float + battery_raw, units, extra)."""
    row, units, extra = {}, set(), []
    for s in data.get("sensor", []):
        title = s.get("title", "?")
        for item in s.get("list", []):
            name, value = item[0], item[1]
            unit = item[2] if len(item) > 2 else ""
            key = (title, name)
            if key in MAP:
                try:
                    row[MAP[key]] = float(value)
                except (TypeError, ValueError):
                    row[MAP[key]] = None
                units.add(unit)
            else:
                extra.append(f"{title}/{name}")
    b = data.get("battery", {})
    row["battery_raw"] = "; ".join(b.get("list", []))[:200] if isinstance(b, dict) else ""
    return row, ", ".join(sorted(units)), extra


# ---------------- L1 ----------------

def dew_point_c(t, rh):
    if t is None or rh is None or rh <= 0:
        return None
    a = math.log(rh / 100.0) + (17.27 * t) / (237.7 + t)
    return round((237.7 * a) / (17.27 - a), 2)


def wind_chill_c(t, v_ms):
    if t is None or v_ms is None or t > 10 or v_ms * 3.6 <= 4.8:
        return None
    v = v_ms * 3.6
    return round(13.12 + 0.6215 * t - 11.37 * v ** 0.16 + 0.3965 * t * v ** 0.16, 2)


def heat_index_c(t, rh):
    if t is None or rh is None or t < 27 or rh < 40:
        return None
    tf = t * 9.0 / 5.0 + 32.0
    hi = (-42.379 + 2.04901523 * tf + 10.14333127 * rh - 0.22475541 * tf * rh
          - 6.83783e-3 * tf * tf - 5.481717e-2 * rh * rh
          + 1.22874e-3 * tf * tf * rh + 8.5282e-4 * tf * rh * rh
          - 1.99e-6 * tf * tf * rh * rh)
    return round((hi - 32.0) * 5.0 / 9.0, 2)


def cardinal16(deg):
    if deg is None:
        return None
    try:
        d = float(deg) % 360.0
    except (TypeError, ValueError):
        return None
    return DIRS16[int((d + 11.25) / 22.5) % 16]


def compute_l1(con, ts, row):
    """L1-поля для строки. wind_run/rain по контексту предыдущих строк в БД."""
    l1 = {}
    l1["dew_point_c"] = dew_point_c(row.get("outdoor_temp_c"), row.get("outdoor_hum_pct"))
    l1["wind_chill_c"] = wind_chill_c(row.get("outdoor_temp_c"), row.get("wind_ms"))
    l1["heat_index_c"] = heat_index_c(row.get("outdoor_temp_c"), row.get("outdoor_hum_pct"))
    l1["wind_dir_card"] = cardinal16(row.get("wind_dir_deg"))
    l1["wind_dir_avg2_card"] = cardinal16(row.get("wind_dir_avg2_deg"))
    l1["wind_dir_avg10_card"] = cardinal16(row.get("wind_dir_avg10_deg"))
    pa, pr = row.get("pressure_abs_mmhg"), row.get("pressure_rel_mmhg")
    l1["p_delta_mmhg"] = round(pr - pa, 2) if (pa is not None and pr is not None) else None

    prev = con.execute("SELECT ts FROM weather WHERE ts < ? ORDER BY ts DESC LIMIT 1",
                       (ts,)).fetchone()
    dt = ts - prev[0] if prev else None
    w = row.get("wind_ms")
    l1["wind_run_m"] = round(w * dt, 1) if (dt and 0 < dt <= 300 and w is not None) else None

    total = row.get("rain_total_mm")
    l1["rain_rate_calc_mmh"] = None
    l1["rain_event"] = 0
    if total is not None:
        # окно 10 мин: ближайшая строка к ts-600 в [ts-700, ts-500]
        win = con.execute(
            "SELECT ts, rain_total_mm FROM weather "
            "WHERE ts BETWEEN ? AND ? AND rain_total_mm IS NOT NULL ORDER BY ts LIMIT 1",
            (ts - 700, ts - 500)).fetchone()
        if win and 0 < (ts - win[0]) <= 700:
            delta = total - win[1]
            dt_h = (ts - win[0]) / 3600.0
        elif prev:
            pr = con.execute("SELECT rain_total_mm FROM weather WHERE ts=?",
                             (prev[0],)).fetchone()
            delta = (total - pr[0]) if (pr and pr[0] is not None) else None
            dt_h = dt / 3600.0 if dt else None
        else:
            delta, dt_h = None, None
        if delta is not None and dt_h:
            if delta < 0:  # RAIN_COUNTER_RESET: счётчик станции сбросился
                l1["rain_rate_calc_mmh"] = 0.0
                l1["rain_event"] = 0
                open_ev = con.execute(
                    "SELECT 1 FROM events WHERE event_type='RAIN_COUNTER_RESET' "
                    "AND ts_start > ? LIMIT 1", (ts - 3600,)).fetchone()
                if not open_ev:
                    con.execute("INSERT INTO events (ts_start, ts_end, event_type, "
                                "severity, value, context) VALUES (?,?,?,?,?,?)",
                                (ts, ts, "RAIN_COUNTER_RESET", "low", round(delta, 3),
                                 json.dumps({"prev_total": round(delta * -1 + total, 2),
                                             "new_total": total})))
            else:
                l1["rain_rate_calc_mmh"] = round(delta / dt_h, 3)
                l1["rain_event"] = 1 if delta >= 0.1 else 0
    return l1


# ---------------- БД / события ----------------

def db_open():
    con = sqlite3.connect(DB, timeout=15)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA journal_size_limit=67108864")
    return con


def meta_get(con, key, d=None):
    r = con.execute("SELECT value FROM wmeta WHERE key=?", (key,)).fetchone()
    return r[0] if r else d


def meta_set(con, key, value, ts):
    con.execute("INSERT INTO wmeta (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at", (key, str(value), ts))


def log_collector(con, ts, status, latency_ms, nbytes, error):
    con.execute("INSERT OR REPLACE INTO collector_log (ts, status, latency_ms, bytes, "
                "error) VALUES (?,?,?,?,?)",
                (ts, status, latency_ms, nbytes, error[:300] if error else None))


def event_open(con, etype, severity, ts, value=None, context=None):
    """Открыть span-событие, если ещё не открыто. -> True если открыто сейчас."""
    row = con.execute("SELECT 1 FROM events WHERE event_type=? AND ts_end IS NULL "
                      "LIMIT 1", (etype,)).fetchone()
    if row:
        return False
    con.execute("INSERT INTO events (ts_start, ts_end, event_type, severity, value, "
                "context) VALUES (?,NULL,?,?,?,?)",
                (ts, etype, severity, value, context))
    return True


def event_close(con, etype, ts):
    con.execute("UPDATE events SET ts_end=? WHERE event_type=? AND ts_end IS NULL",
                (ts, etype))


def check_rapid_temp(con, ts, t_out):
    if t_out is None:
        return
    ref = con.execute("SELECT outdoor_temp_c, ts FROM weather WHERE ts BETWEEN ? AND ? "
                      "ORDER BY ts DESC LIMIT 1", (ts - 3900, ts - 3300)).fetchone()
    if not ref or ref[0] is None:
        return
    delta = round(t_out - ref[0], 1)
    if abs(delta) < 5:
        return
    last = con.execute("SELECT 1 FROM events WHERE event_type LIKE 'RAPID_TEMP%' "
                       "AND ts_start > ? LIMIT 1", (ts - RAPID_DEDUP_SEC,)).fetchone()
    if last:
        return
    etype = "RAPID_TEMP_RISE" if delta > 0 else "RAPID_TEMP_DROP"
    con.execute("INSERT INTO events (ts_start, ts_end, event_type, severity, value, "
                "context) VALUES (?,?,?,?,?,?)",
                (ts, ts, etype, "mid", delta,
                 json.dumps({"t_now": t_out, "t_ref": ref[0], "ref_ts": ref[1]})))
    log(f"EVT {etype} delta={delta:+g}C")


def check_threshold_events(con, ts, row):
    t_out = row.get("outdoor_temp_c")
    # FROST / HARD_FREEZE
    if t_out is not None:
        if t_out <= 0:
            if event_open(con, "FROST", "high", ts, t_out,
                          json.dumps({"t_out": t_out})):
                log(f"EVT FROST open t={t_out:g}C")
        else:
            event_close(con, "FROST", ts)
        if t_out <= -10:
            if event_open(con, "HARD_FREEZE", "high", ts, t_out,
                          json.dumps({"t_out": t_out})):
                log(f"EVT HARD_FREEZE open t={t_out:g}C")
        else:
            event_close(con, "HARD_FREEZE", ts)
        check_rapid_temp(con, ts, t_out)
    # BATTERY_LOW (whitelist OK-строк, weather-7; fail-safe: пусто = LOW)
    raw = row.get("battery_raw")
    if raw is not None:
        ok = battery_is_ok(raw)
        if ok:
            event_close(con, "BATTERY_LOW", ts)
        elif event_open(con, "BATTERY_LOW", "mid", ts, None,
                        json.dumps({"battery_raw": raw})):
            log(f"EVT BATTERY_LOW open raw={raw[:60]}")


def sanity_check(row):
    """-> список нарушений «поле=знач» (границы SANITY, weather-7 §2)."""
    bad = []
    for col, (lo, hi) in SANITY.items():
        v = row.get(col)
        if v is not None and not (lo <= v <= hi):
            bad.append(f"{col}={v:g}")
    return bad


def check_missing_on_success(con, ts, last_ts):
    if last_ts and ts - last_ts > MISSING_GAP_SEC:
        con.execute("INSERT INTO events (ts_start, ts_end, event_type, severity, value, "
                    "context) VALUES (?,?,?,?,?,?)",
                    (last_ts, ts, "SENSOR_MISSING", "mid", ts - last_ts,
                     json.dumps({"prev_ts": last_ts, "back_at": ts})))
        log(f"EVT SENSOR_MISSING gap={ts - last_ts}s (закрыт возвратом)")
    event_close(con, "SENSOR_MISSING", ts)


def check_missing_on_failure(con, ts):
    con2 = db_open()
    try:
        r = con2.execute("SELECT MAX(ts) FROM weather").fetchone()
        last_ts = r[0] if r and r[0] else None
        if last_ts and ts - last_ts > MISSING_GAP_SEC:
            row = con2.execute("SELECT 1 FROM events WHERE event_type='SENSOR_MISSING' "
                               "AND ts_end IS NULL LIMIT 1").fetchone()
            if not row:
                con2.execute("INSERT INTO events (ts_start, ts_end, event_type, severity, "
                             "value, context) VALUES (?,NULL,?,?,?,?)",
                             (last_ts, "SENSOR_MISSING", "mid", ts - last_ts,
                              json.dumps({"prev_ts": last_ts})))
                log(f"EVT SENSOR_MISSING open gap={ts - last_ts}s")
    finally:
        con2.close()


# ---------------- цикл ----------------

def cycle():
    ts = int(time.time())
    con = db_open()
    try:
        r = con.execute("SELECT MAX(ts) FROM weather").fetchone()
        last_ts = r[0] if r and r[0] else None
        data, status, err, latency, nbytes = fetch()
        if data is None:
            log_collector(con, ts, status, int(latency * 1000), nbytes, err)
            con.execute("UPDATE wmeta SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT), "
                        "updated_at=? WHERE key='err_total'", (ts,))
            check_missing_on_failure(con, ts)
            con.commit()
            kuma_push("down", f"{status}: {err}", latency * 1000 if latency else None)
            log(f"ERR {datetime.now().strftime('%H:%M:%S')} {status}: {err}")
            return
        row, units, extra = flatten(data)
        if extra:
            log("WARN новые поля без маппинга: " + ", ".join(extra[:6]) +
                (" ..." if len(extra) > 6 else "") + " — внеси в MAP")
        missing = [c for c in NUM_COLS if c not in row]
        if missing:
            log("WARN не найдены в ответе: " + ", ".join(missing))

        bad = []
        if last_ts is not None and ts < last_ts - 5:
            bad.append(f"ts_back({ts}<{last_ts - 5})")
        bad += sanity_check(row)
        sstatus = "sanity_fail" if bad else "ok"
        if bad:
            log("SANITY FAIL: " + "; ".join(bad[:5]))

        l1 = compute_l1(con, ts, row)
        cols = ["ts"] + NUM_COLS + ["battery_raw"] + L1_COLS
        vals = [ts] + [row.get(c) for c in NUM_COLS] + [row.get("battery_raw")] + \
               [l1[c] for c in L1_COLS]
        ph = ", ".join("?" for _ in cols)
        con.execute(f"INSERT OR REPLACE INTO weather ({', '.join(cols)}) VALUES ({ph})", vals)

        old_units = meta_get(con, "units")
        if old_units is None:
            meta_set(con, "units", units, ts)
            meta_set(con, "units_set_at", ts, ts)
        elif old_units != units:
            log(f"WARN ЕДИНИЦЫ СМЕНИЛИСЬ: '{old_units}' -> '{units}' (проверь /unit.html)")
            meta_set(con, "units_prev", old_units, ts)
            meta_set(con, "units", units, ts)
            meta_set(con, "units_set_at", ts, ts)

        log_collector(con, ts, sstatus, int(latency * 1000), nbytes,
                      ";".join(bad) if bad else None)
        if sstatus == "ok":
            ok_n = int(meta_get(con, "ok_total", "0") or 0) + 1
            meta_set(con, "ok_total", ok_n, ts)
            meta_set(con, "last_ok", ts, ts)
        else:
            sf = int(meta_get(con, "sanity_fail_total", "0") or 0) + 1
            meta_set(con, "sanity_fail_total", sf, ts)
        meta_set(con, "schema_version", SCHEMA_VERSION, ts)

        if sstatus == "ok":
            check_missing_on_success(con, ts, last_ts)
        check_threshold_events(con, ts, row)
        con.commit()

        f = lambda c: (lambda v: f"{v:g}" if isinstance(v, float) else "-")(row.get(c))
        if bad:
            # weather-7 («вдогонка» §3): в msg — поле-нарушитель, до 6 шт
            # (полный список — в collector_log.error той же строки)
            kuma_push("down", "sanity_fail: " + ", ".join(bad[:6]), latency * 1000)
        else:
            kuma_push("up", f"ok out={f('outdoor_temp_c')}C P={f('pressure_rel_mmhg')}",
                      latency * 1000)
        log(f"{'OK' if sstatus == 'ok' else sstatus.upper()} "
            f"{datetime.now().strftime('%H:%M:%S')} "
            f"out={f('outdoor_temp_c')}C/{f('outdoor_hum_pct')}% "
            f"in={f('indoor_temp_c')}C/{f('indoor_hum_pct')}% "
            f"P={f('pressure_rel_mmhg')} wind={f('wind_ms')} dir={f('wind_dir_deg')} "
            f"rain_day={f('rain_day_mm')} light={f('light_wm2')} uvi={f('uvi')} "
            f"lat={int(latency * 1000)}ms")
    finally:
        con.close()


RUN = True


def _stop(signum, _frame):
    global RUN
    RUN = False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log(f"collector v{SCHEMA_VERSION} start pid={os.getpid()} "
        f"kuma={'yes' if KUMA else 'no'}")
    while RUN:
        try:
            cycle()
        except Exception as e:  # noqa: BLE001 — цикл обязан выжить
            log(f"CYCLE-ERR {datetime.now().strftime('%H:%M:%S')} {type(e).__name__}: {e}")
        nxt = (int(time.time()) // PERIOD) * PERIOD + PERIOD + OFFSET
        while RUN and time.time() < nxt:
            time.sleep(min(5, max(0.2, nxt - time.time())))
    log("collector stop")


if __name__ == "__main__":
    main()
