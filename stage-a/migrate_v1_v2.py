#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_v1_v2.py — этап A2 (weather-6). Копирующая миграция v1 -> v2 на vitele.

- CREATE weather_new (v2.1) + копия raw из weather (v1) + L1-пересчёт батчем
- wmeta (k,v) -> wmeta_new (key,value,updated_at) + новые ключи
- collector_log, events, forecast, summaries, insights, агрегаты v_*, materializer_log
- swap: weather -> weather_v1_backup, weather_new -> weather (v1-копию НЕ удаляем)
- schema_migrations: v2. Всё в ОДНОЙ транзакции (DDL в SQLite транзакционен).
Идемпотентно: если уже v2 — выход без действий.
Запуск: python3 migrate_v1_v2.py [--dry-run]
"""
import math
import sqlite3
import sys
import time

DB = "/home/auditbot/weather-dash/weather.db"
SCHEMA_VERSION = 2
NOW = int(time.time())

RAW_COLS = ["indoor_temp_c", "indoor_hum_pct", "outdoor_temp_c", "outdoor_hum_pct",
            "pressure_abs_mmhg", "pressure_rel_mmhg", "wind_max_daily_ms",
            "wind_ms", "gust_ms", "wind_dir_deg", "wind_avg2_ms",
            "wind_dir_avg2_deg", "wind_avg10_ms", "wind_dir_avg10_deg",
            "rain_rate_mmh", "rain_hour_mm", "rain_day_mm", "rain_week_mm",
            "rain_month_mm", "rain_year_mm", "rain_total_mm", "light_wm2", "uvi"]
L1_COLS = ["dew_point_c", "wind_chill_c", "heat_index_c", "wind_dir_card",
           "wind_dir_avg2_card", "wind_dir_avg10_card", "p_delta_mmhg",
           "wind_run_m", "rain_rate_calc_mmh", "rain_event"]
DIRS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
          "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

DDL_STMTS = [
    # --- weather v2 ---
    """CREATE TABLE weather_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  """ + ",\n  ".join(f"{c} REAL" for c in RAW_COLS) + """,
  battery_raw TEXT,
  """ + ",\n  ".join(f"{c} REAL" for c in L1_COLS[:-1]) + """,
  rain_event INTEGER DEFAULT 0,
  schema_version INTEGER DEFAULT 2
)""",
    "CREATE UNIQUE INDEX idx_weather_ts ON weather_new(ts)",
    """CREATE TABLE wmeta_new (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at INTEGER
)""",
    """CREATE TABLE collector_log (
  ts INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  bytes INTEGER,
  error TEXT
)""",
    "CREATE INDEX idx_collector_log_status ON collector_log(status, ts)",
    """CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start INTEGER NOT NULL,
  ts_end INTEGER,
  event_type TEXT NOT NULL,
  severity TEXT,
  value REAL,
  context TEXT,
  acknowledged INTEGER DEFAULT 0
)""",
    "CREATE INDEX idx_events_type_ts ON events(event_type, ts_start)",
    "CREATE INDEX idx_events_open ON events(ts_end) WHERE ts_end IS NULL",
    """CREATE TABLE forecast (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issued_at INTEGER NOT NULL,
  target_ts INTEGER NOT NULL,
  source TEXT NOT NULL,
  t_out_c REAL,
  p_rel_mmhg REAL,
  rh_out_pct REAL,
  wind_ms REAL,
  rain_mm REAL,
  confidence REAL,
  UNIQUE(issued_at, target_ts, source)
)""",
    "CREATE INDEX idx_forecast_target ON forecast(target_ts, source)",
    """CREATE TABLE summaries (
  ts INTEGER PRIMARY KEY,
  period TEXT NOT NULL,
  text TEXT NOT NULL,
  model TEXT,
  tokens INTEGER
)""",
    """CREATE TABLE insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  confidence REAL,
  data TEXT
)""",
    """CREATE TABLE v_hourly (
  hour_epoch INTEGER PRIMARY KEY,
  t_out_avg REAL, t_out_min REAL, t_out_max REAL, t_out_slope REAL,
  t_in_avg REAL, rh_out_avg REAL, rh_in_avg REAL,
  p_rel_avg REAL, p_rel_min REAL, p_rel_max REAL, p_tendency_3h REAL,
  wind_avg REAL, wind_max REAL, gust_max REAL,
  wind_dir_sum_sin REAL, wind_dir_sum_cos REAL, wind_n INTEGER,
  wind_dir_avg_deg REAL, wind_dir_mode TEXT,
  rain_mm REAL, rain_rate_max REAL, rain_tips INTEGER,
  solar_avg REAL, solar_max REAL, uvi_max REAL,
  sun_minutes INTEGER, n_samples INTEGER
)""",
    """CREATE TABLE v_daily (
  day_epoch INTEGER PRIMARY KEY,
  t_out_min REAL, t_out_max REAL, t_out_avg REAL,
  t_out_min_time INTEGER, t_out_max_time INTEGER, t_out_amp REAL,
  p_min REAL, p_max REAL, p_amp REAL,
  wind_avg REAL, wind_max REAL, gust_max REAL,
  wind_run_km REAL, wind_dir_mode TEXT,
  rain_mm REAL, rain_hours INTEGER, rain_max_rate REAL,
  solar_sum_wh_m2 REAL, uvi_max REAL, sun_hours REAL,
  gdd_day REAL, frost_flag INTEGER, hard_freeze_flag INTEGER,
  fog_flag INTEGER, thunder_flag INTEGER,
  degree_days_heat REAL, degree_days_cool REAL, n_samples INTEGER
)""",
    """CREATE TABLE v_monthly (
  month_epoch INTEGER PRIMARY KEY,
  t_out_min_abs REAL, t_out_max_abs REAL, t_out_avg REAL,
  p_min REAL, p_max REAL,
  wind_max REAL, gust_max REAL,
  rain_mm REAL, rain_days INTEGER,
  gdd_month_sum REAL,
  frost_days INTEGER, hot_days INTEGER, cold_days INTEGER,
  n_samples INTEGER
)""",
    """CREATE TABLE v_yearly (
  year INTEGER PRIMARY KEY,
  t_out_min_abs REAL, t_out_max_abs REAL, t_out_avg REAL,
  rain_mm REAL, rain_days INTEGER,
  gdd_year_sum REAL,
  frost_days INTEGER, hot_days INTEGER, cold_days INTEGER
)""",
    """CREATE TABLE materializer_log (
  ts INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  hours_processed INTEGER,
  rows_written INTEGER,
  latency_ms INTEGER,
  error TEXT
)""",
    """CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at INTEGER NOT NULL,
  description TEXT
)""",
]


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


def main():
    dry = "--dry-run" in sys.argv
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.isolation_level = None  # ручные транзакции (executescript не используется)
    con.execute("PRAGMA busy_timeout=5000")

    # --- состояние ---
    has_mig = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='schema_migrations'").fetchone()
    if has_mig:
        v2 = con.execute("SELECT 1 FROM schema_migrations WHERE version=?",
                         (SCHEMA_VERSION,)).fetchone()
        if v2:
            print("ALREADY_MIGRATED v2 — ничего не делаю")
            return
    cnt = con.execute("SELECT COUNT(*) AS n FROM weather").fetchone()["n"]
    jm = con.execute("PRAGMA journal_mode").fetchone()[0]
    ic = con.execute("PRAGMA integrity_check").fetchone()[0]
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    print(f"[i] v1: {cnt} строк, journal_mode={jm}, integrity={ic}, таблиц={len(names)}")
    assert ic == "ok", "integrity_check v1 не ok"
    if "weather_v1_backup" in names:
        print("FATAL: weather_v1_backup уже существует — миграция прерывается")
        sys.exit(2)
    if dry:
        print("DRY-RUN OK — схема и предпроверки валидны")
        return

    # --- WAL до транзакции (персистентно) ---
    if jm.lower() != "wal":
        print("[i] journal_mode -> WAL:",
              con.execute("PRAGMA journal_mode=WAL").fetchone()[0])
    con.execute("PRAGMA journal_size_limit=67108864")

    # --- транзакция ---
    con.execute("BEGIN IMMEDIATE")
    try:
        for stmt in DDL_STMTS:
            con.execute(stmt)
        src_cols = ", ".join(RAW_COLS)
        con.execute(f"INSERT INTO weather_new (ts, {src_cols}, battery_raw, "
                    f"schema_version) SELECT ts, {src_cols}, battery, "
                    f"{SCHEMA_VERSION} FROM weather")
        rows = con.execute(
            f"SELECT ts, {src_cols} FROM weather_new ORDER BY ts").fetchall()
        assert len(rows) == cnt, f"копия: {len(rows)} != {cnt}"
        prev_ts = None
        for r in rows:
            ts = r["ts"]
            d = {c: r[c] for c in RAW_COLS}
            l1 = {
                "dew_point_c": dew_point_c(d["outdoor_temp_c"], d["outdoor_hum_pct"]),
                "wind_chill_c": wind_chill_c(d["outdoor_temp_c"], d["wind_ms"]),
                "heat_index_c": heat_index_c(d["outdoor_temp_c"], d["outdoor_hum_pct"]),
                "wind_dir_card": cardinal16(d["wind_dir_deg"]),
                "wind_dir_avg2_card": cardinal16(d["wind_dir_avg2_deg"]),
                "wind_dir_avg10_card": cardinal16(d["wind_dir_avg10_deg"]),
            }
            pa, pr = d["pressure_abs_mmhg"], d["pressure_rel_mmhg"]
            l1["p_delta_mmhg"] = (round(pr - pa, 2)
                                  if (pa is not None and pr is not None) else None)
            dt = (ts - prev_ts) if prev_ts else None
            w = d["wind_ms"]
            l1["wind_run_m"] = (round(w * dt, 1)
                                if (dt and 0 < dt <= 300 and w is not None) else None)
            l1["rain_rate_calc_mmh"] = None
            l1["rain_event"] = 0
            prev_ts = ts
            con.execute("UPDATE weather_new SET "
                        + ", ".join(f"{c}=?" for c in L1_COLS) + " WHERE ts=?",
                        [l1[c] for c in L1_COLS] + [ts])
        print(f"[i] raw скопирован: {cnt} строк, L1 пересчитан")

        old_meta = {r["k"]: r["v"] for r in con.execute("SELECT k, v FROM wmeta")}
        for k, v in old_meta.items():
            ua = int(v) if (k in ("units_set_at", "last_ok") and str(v).isdigit()) else NOW
            con.execute("INSERT INTO wmeta_new (key, value, updated_at) VALUES (?,?,?)",
                        (k, str(v), ua))
        for k, v in [("schema", str(SCHEMA_VERSION)),
                     ("schema_version", str(SCHEMA_VERSION)),
                     ("tz", "Europe/Moscow"), ("tz_offset_seconds", "10800"),
                     ("tz_policy", "fixed_offset"), ("gdd_tbase_c", "5"),
                     ("station_mac", "d8:bc:38:a6:e6:14"),
                     ("station_ip", "192.168.8.101"),
                     ("err_total", "0"), ("sanity_fail_total", "0"),
                     ("migrated_at", str(NOW))]:
            con.execute("INSERT OR REPLACE INTO wmeta_new (key, value, updated_at) "
                        "VALUES (?,?,?)", (k, str(v), NOW))
        print(f"[i] wmeta: {len(old_meta)} старых ключей перенесено + новые")

        con.execute("ALTER TABLE weather RENAME TO weather_v1_backup")
        con.execute("ALTER TABLE weather_new RENAME TO weather")
        con.execute("ALTER TABLE wmeta RENAME TO wmeta_v1_backup")
        con.execute("ALTER TABLE wmeta_new RENAME TO wmeta")
        con.execute("INSERT INTO schema_migrations (version, applied_at, description) "
                    "VALUES (2, ?, 'epoch UTC, surrogate PK+UNIQUE(ts), L1, WAL, "
                    "collector_log/events/forecast/aggregates/materializer_log')",
                    (NOW,))
        con.execute("COMMIT")
        print("[i] swap выполнен, schema_migrations v2 записана")
    except Exception:
        con.execute("ROLLBACK")
        print("[!] ROLLBACK выполнен — БД в состоянии v1")
        raise

    # --- верификация ---
    n2 = con.execute("SELECT COUNT(*) FROM weather").fetchone()[0]
    ic2 = con.execute("PRAGMA integrity_check").fetchone()[0]
    jm2 = con.execute("PRAGMA journal_mode").fetchone()[0]
    idx = [r[1] for r in con.execute("PRAGMA index_list(weather)")]
    l1n = con.execute("SELECT COUNT(*) FROM weather WHERE dew_point_c IS NOT NULL "
                      "AND p_delta_mmhg IS NOT NULL").fetchone()[0]
    sample = con.execute("SELECT ts, outdoor_temp_c, dew_point_c, wind_dir_card, "
                         "p_delta_mmhg FROM weather ORDER BY ts DESC LIMIT 3").fetchall()
    tables = sorted(r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
        "'sqlite_%'").fetchall())
    print(f"[verify] rows={n2} (ожидалось {cnt}), integrity={ic2}, journal={jm2}")
    print(f"[verify] indexes={idx}, L1 (Td+dP) заполнено у {l1n}/{n2}")
    for r in sample:
        print(f"[verify] sample ts={r['ts']} t_out={r['outdoor_temp_c']} "
              f"Td={r['dew_point_c']} dir={r['wind_dir_card']} dP={r['p_delta_mmhg']}")
    print("[verify] tables:", ", ".join(tables))
    assert n2 == cnt and ic2 == "ok" and jm2.lower() == "wal"
    print("MIGRATION_OK")


if __name__ == "__main__":
    main()
