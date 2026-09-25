# === АРХИВ: реликт до-гитовой эпохи. НЕ ИСПОЛНЯТЬ, НЕ ИМПОРТИРОВАТЬ, в деплой-манифест не включать ===
# Реликт этапа 1, до-репо, 15.09.2026; схема v1, 23 поля, events не писал; заменён weather_collector.py v2. НЕ ИСПОЛНЯТЬ
# Оригинал: /home/auditbot/weather-dash/weather_poller.py (6541 байта), md5 b180ec6d3e88a5dd9dc1ccbb949e700d, заархивирован 2026-09-25.
# Ниже — тело оригинала БЕЗ изменений: архив = шапка (4 строки) + оригинал; md5 оригинала = md5 файла со срезом первых 4 строк.
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_poller.py v1 — опрос метеостанции 192.168.8.101 -> SQLite (vitele).
Проект weather (владелец, 15.09.2026), этап 1 «сбор + хранение». stdlib-only.

- GET /client?command=record (таймаут 8 с, 1 ретрай через 3 с)
- JSON decode ЯВНО utf-8 (у станции Content-Type без charset)
- INSERT OR REPLACE в weather (ts INTEGER PRIMARY KEY = epoch; 23 REAL + battery TEXT)
- wmeta: версия схемы, снимок единиц (смена -> предупреждение в лог), счётчики ok/err
- единицы — как на станции (владелец: мм рт. столба); конверсия не выполняется
- ничего не POSTит, конфиг станции не трогает
"""
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime

STATION_URL = "http://192.168.8.101/client?command=record"
DB = "/home/auditbot/weather-dash/weather.db"
SCHEMA_VERSION = "1"

# (группа, параметр) -> колонка БД. 23 числовых поля.
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
NUM_COLS = list(MAP.values())  # порядок колонок = порядок объявления MAP


def fetch():
    last = None
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(
                STATION_URL, headers={"User-Agent": "weather-poller/1"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read()
            return json.loads(raw.decode("utf-8"))  # charset в ответе отсутствует — задаём явно
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 1:
                time.sleep(3)
    raise RuntimeError(f"fetch fail после ретрая: {last}")


def flatten(data):
    """JSON станции -> (row: dict 23 float + battery, units: str, extra: list)."""
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
    row["battery"] = "; ".join(b.get("list", []))[:200] if isinstance(b, dict) else ""
    return row, ", ".join(sorted(units)), extra


def init_db(con):
    cols = ",\n  ".join(f"{c} REAL" for c in NUM_COLS)
    con.execute(f"CREATE TABLE IF NOT EXISTS weather (\n"
                f"  ts INTEGER PRIMARY KEY,\n  {cols},\n  battery TEXT\n)")
    con.execute("CREATE TABLE IF NOT EXISTS wmeta (k TEXT PRIMARY KEY, v TEXT)")


def meta_get(con, k, d=None):
    r = con.execute("SELECT v FROM wmeta WHERE k=?", (k,)).fetchone()
    return r[0] if r else d


def meta_set(con, k, v):
    con.execute("INSERT INTO wmeta (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def main():
    ts = int(time.time())
    data = fetch()
    row, units, extra = flatten(data)
    missing = [c for c in NUM_COLS if c not in row]
    if extra:
        print(f"[weather] WARN новые поля без маппинга: {', '.join(extra[:6])}"
              f"{' ...' if len(extra) > 6 else ''} — внеси в MAP")
    if missing:
        print(f"[weather] WARN не найдены в ответе: {', '.join(missing)}")

    con = sqlite3.connect(DB, timeout=10)
    init_db(con)

    cols = ["ts"] + NUM_COLS + ["battery"]
    vals = [ts] + [row.get(c) for c in NUM_COLS] + [row.get("battery")]
    ph = ", ".join("?" for _ in cols)
    con.execute(f"INSERT OR REPLACE INTO weather ({', '.join(cols)}) VALUES ({ph})", vals)

    old_units = meta_get(con, "units")
    if old_units is None:
        meta_set(con, "units", units)
        meta_set(con, "units_set_at", str(ts))
    elif old_units != units:
        print(f"[weather] WARN ЕДИНИЦЫ СМЕНИЛИСЬ: '{old_units}' -> '{units}' "
              f"(колонки БД остались прежними — проверь /unit.html)")
        meta_set(con, "units_prev", old_units)
        meta_set(con, "units", units)
        meta_set(con, "units_set_at", str(ts))
    meta_set(con, "schema", SCHEMA_VERSION)
    ok_n = int(meta_get(con, "ok_total", "0")) + 1
    meta_set(con, "ok_total", str(ok_n))
    meta_set(con, "last_ok", str(ts))
    con.commit()
    con.close()

    f = lambda c: (lambda v: f"{v:g}" if isinstance(v, float) else "-")(row.get(c))
    print(f"[weather] OK {datetime.now().strftime('%H:%M:%S')} "
          f"out={f('outdoor_temp_c')}C/{f('outdoor_hum_pct')}% "
          f"in={f('indoor_temp_c')}C/{f('indoor_hum_pct')}% "
          f"P={f('pressure_rel_mmhg')} wind={f('wind_ms')} "
          f"dir={f('wind_dir_deg')} rain_day={f('rain_day_mm')} "
          f"light={f('light_wm2')} uvi={f('uvi')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[weather] ERR {datetime.now().strftime('%H:%M:%S')} {e}")
        sys.exit(1)
