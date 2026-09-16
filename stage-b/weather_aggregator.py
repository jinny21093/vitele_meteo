#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_aggregator.py — этап B (roadmap B1/B3, weather-8).
Материализаторы + прогнозы поверх weather.db v2.

Запуск:  weather_aggregator.py hourly|daily|probe [--db PATH]

hourly (timer :02 каждый час):
  - пересчёт ХВОСТА 3 ч завершившихся часовых окон (D10: DELETE+INSERT, late-data-safe);
  - агрегаты в v_hourly: t/rh/p avg-min-max, t_out_slope (регрессия), p_tendency_3h
    (окна по времени, не LAG-строки), ветер векторно (sum_sin/sum_cos/n + mode, D9),
    дождь через приращения типпера, solar/uvi/sun_minutes, n_samples;
  - forecast: persistence (+1h/+3h/+6h) + zambretti (+6h/+12h, text) + sager_day
    (+6h, только 10..15 локальных, облачность через solar — ТЗ §7.3);
daily (timer 00:05 MSK):
  - пересчёт последних 2 ЗАВЕРШИВШИХСЯ локальных суток (00:00 MSK, D7 fixed +10800);
  - v_daily: экстремумы+время, ветер+wind_run_km (из L1-поля raw), дождь (приращения),
    solar Wh/m², GDD (Tbase из wmeta.gdd_tbase_c, дефолт 5), frost/hard_freeze,
    fog (spread<1 & rh>95), degree_days (heat base 18, cool base 22);
  - rolling-события дня: HEATWAVE(tmax>35)/CALM(wind_max<1)/DRY_SPELL(rain 7д=0)
    — идемпотентно (DELETE+INSERT по event_type+ts_start), гвард n_samples>=720;
    DRY_SPELL: условие solar ТЗ упрощено до rain_7d==0, гвард <5 суток данных в окне;
  - чистка forecast старше 30 дней.
Наблюдаемость: materializer_log на каждый прогон; указатели wmeta
last_agg_hourly_epoch/last_agg_daily_epoch (epoch начала последнего обработанного окна).
Миграция v3: ALTER TABLE forecast ADD COLUMN text TEXT (идемпотентно).
PRAGMA busy_timeout/synchronous — на каждом соединении (§3.2). stdlib-only."""
import json
import math
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weather_zam import zambretti, sager_day, classify_trend, SOLAR_POT  # noqa: E402

DEFAULT_DB = "/home/auditbot/weather-dash/weather.db"
TZ_OFF = 10800            # Europe/Moscow, fixed (D7)
TAIL_HOURS = 3            # хвост пересчёта hourly (D10)
TAIL_DAYS = 2             # хвост пересчёта daily
FORECAST_TTL = 30 * 86400
MMHG_TO_HPA = 1.33322387415


def log(msg):
    print(f"[agg {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def open_db(path):
    con = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=10)
    con.isolation_level = None           # ручные транзакции (урок weather-6)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def meta_get(con, key, default=None):
    r = con.execute("SELECT value FROM wmeta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def meta_set(con, key, value, now):
    con.execute("INSERT INTO wmeta(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(value), now))


def migrate_v3(con, now):
    """forecast.text — для формулировок zambretti/sager (этап B). Идемпотентно."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(forecast)")]
    if "text" in cols:
        return False
    con.execute("BEGIN IMMEDIATE")
    con.execute("ALTER TABLE forecast ADD COLUMN text TEXT")
    con.execute("INSERT OR REPLACE INTO schema_migrations(version,applied_at,description) "
                "VALUES (3, ?, 'stage B: forecast.text (zambretti/sager), aggregates, api')",
                (now,))
    con.execute("COMMIT")
    log("миграция v3 применена (forecast.text)")
    return True


def avg(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def rain_sum(rows, ts_from=None):
    """Сумма приращений rain_total по парам (prev,cur); reset/скачок>=5 мм — пропуск.
    rows: список (ts, rain_total), отсортирован по ts. Учитываются пары с cur_ts >= ts_from."""
    total, tips = 0.0, 0
    prev_t = prev_r = None
    for ts, r in rows:
        if prev_r is not None and r is not None and ts_from is not None and ts >= ts_from:
            d = round(r - prev_r, 3)
            if 0.0999 <= d < 5.0:
                total += d
                tips += 1
        prev_t, prev_r = ts, r
    return round(total, 2), tips


def p_tendency(con, end_ts):
    """Δ давления за 3 ч НА конец момента end_ts, по временным окнам (не LAG)."""
    a = con.execute("SELECT AVG(pressure_rel_mmhg) FROM weather WHERE ts>? AND ts<=?",
                    (end_ts - 600, end_ts)).fetchone()[0]
    b = con.execute("SELECT AVG(pressure_rel_mmhg) FROM weather WHERE ts>? AND ts<=?",
                    (end_ts - 10800 - 600, end_ts - 10800)).fetchone()[0]
    return round(a - b, 3) if (a is not None and b is not None) else None


def slope(points):
    """Линейная регрессия y по x: -> наклон или None."""
    pts = [(x, y) for x, y in points if y is not None]
    if len(pts) < 3:
        return None
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    cov = sum((x - mx) * (y - my) for x, y in pts)
    var = sum((x - mx) ** 2 for x, _ in pts)
    return round(cov / var, 6) if var else None


def mode_card(rows):
    """Мода 16 румбов; при равенстве — первый по часовой (порядок DIRS16)."""
    DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    cnt = {}
    for r in rows:
        if r is not None:
            cnt[r] = cnt.get(r, 0) + 1
    if not cnt:
        return None
    best = max(cnt.values())
    for d in DIRS:                       # tie-break по часовой
        if cnt.get(d) == best:
            return d
    return None


def agg_hour_window(con, H, now):
    """Пересчитать час H: 1 строка v_hourly (0 — нет данных)."""
    rows = con.execute(
        "SELECT ts,outdoor_temp_c,indoor_temp_c,indoor_hum_pct,outdoor_hum_pct,"
        "pressure_rel_mmhg,wind_ms,gust_ms,wind_avg2_ms,wind_dir_avg2_deg,wind_dir_card,"
        "rain_rate_calc_mmh,rain_total_mm,light_wm2,uvi "
        "FROM weather WHERE ts>=? AND ts<? ORDER BY ts", (H, H + 3600)).fetchall()
    if not rows:
        return 0
    t_out = [r[1] for r in rows]
    t_out_v = [x for x in t_out if x is not None]
    prev = con.execute("SELECT ts,rain_total_mm FROM weather WHERE ts<? AND "
                       "rain_total_mm IS NOT NULL ORDER BY ts DESC LIMIT 1", (H,)).fetchone()
    rrows = [(prev[0], prev[1])] if prev else []
    rrows += [(r[0], r[12]) for r in rows]
    rain_mm, tips = rain_sum(rrows, ts_from=H)
    wind_n = sum(1 for r in rows if r[9] is not None)
    sin_sum = sum(math.sin(math.radians(r[9])) for r in rows if r[9] is not None)
    cos_sum = sum(math.cos(math.radians(r[9])) for r in rows if r[9] is not None)
    vdeg = round(math.degrees(math.atan2(sin_sum, cos_sum)) % 360, 1) if wind_n else None
    solar = [r[13] for r in rows]
    con.execute(
        "INSERT INTO v_hourly(hour_epoch,t_out_avg,t_out_min,t_out_max,t_out_slope,"
        "t_in_avg,rh_out_avg,rh_in_avg,p_rel_avg,p_rel_min,p_rel_max,p_tendency_3h,"
        "wind_avg,wind_max,gust_max,wind_dir_sum_sin,wind_dir_sum_cos,wind_n,"
        "wind_dir_avg_deg,wind_dir_mode,rain_mm,rain_rate_max,rain_tips,solar_avg,"
        "solar_max,uvi_max,sun_minutes,n_samples) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (H, avg(t_out), min(t_out_v) if t_out_v else None,
         max(t_out_v) if t_out_v else None,
         slope([(r[0] - H, r[1]) for r in rows]),
         avg([r[2] for r in rows]), avg(rh_out := [r[4] for r in rows]),
         avg([r[3] for r in rows]),
         avg([r[5] for r in rows]),
         min([x for x in (r[5] for r in rows) if x is not None], default=None),
         max([x for x in (r[5] for r in rows) if x is not None], default=None),
         p_tendency(con, H + 3600),
         avg(wind2 := [r[8] for r in rows]),
         max([x for x in (r[6] for r in rows) if x is not None], default=None),
         max([x for x in (r[7] for r in rows) if x is not None], default=None),
         round(sin_sum, 4) if wind_n else None, round(cos_sum, 4) if wind_n else None,
         wind_n, vdeg, mode_card([r[10] for r in rows]),
         rain_mm,
         max([x for x in (r[11] for r in rows) if x is not None], default=None),
         tips,
         avg(solar), max([x for x in solar if x is not None], default=None),
         max([x for x in (r[14] for r in rows) if x is not None], default=None),
         sum(1 for x in solar if x is not None and x > 50), len(rows)))
    return 1


def upsert_day_event(con, d0, etype, cond, value, severity, ctx):
    """Идемпотентное дневное событие: DELETE по (event_type, ts_start) + INSERT при cond."""
    con.execute("DELETE FROM events WHERE event_type=? AND ts_start=?", (etype, d0))
    if cond:
        con.execute("INSERT INTO events(ts_start,ts_end,event_type,severity,value,context,"
                    "acknowledged) VALUES(?,?,?,?,?,?,0)",
                    (d0, d0 + 86400, etype, severity, value, json.dumps(ctx, ensure_ascii=False)))
        return 1
    return 0


def agg_day_window(con, d0, now):
    """Пересчитать локальные сутки d0 (00:00 MSK): v_daily + rolling-события."""
    rows = con.execute(
        "SELECT ts,outdoor_temp_c,dew_point_c,outdoor_hum_pct,pressure_rel_mmhg,wind_ms,"
        "gust_ms,wind_avg2_ms,wind_dir_card,rain_rate_calc_mmh,rain_total_mm,light_wm2,"
        "uvi,wind_run_m FROM weather WHERE ts>=? AND ts<? ORDER BY ts",
        (d0, d0 + 86400)).fetchall()
    if not rows:
        return 0
    t = [r[1] for r in rows]
    tv = [x for x in t if x is not None]
    p = [r[4] for r in rows]
    pv = [x for x in p if x is not None]
    prev = con.execute("SELECT ts,rain_total_mm FROM weather WHERE ts<? AND "
                       "rain_total_mm IS NOT NULL ORDER BY ts DESC LIMIT 1", (d0,)).fetchone()
    rrows = [(prev[0], prev[1])] if prev else []
    rrows += [(r[0], r[10]) for r in rows]
    rain_mm, _ = rain_sum(rrows, ts_from=d0)
    rain_hours = con.execute("SELECT COUNT(DISTINCT ts/3600) FROM weather WHERE ts>=? "
                             "AND ts<? AND rain_event=1", (d0, d0 + 86400)).fetchone()[0]
    solar = [r[11] for r in rows]
    tmin = min(tv) if tv else None
    tmax = max(tv) if tv else None
    tmin_ts = tmax_ts = None
    if tv:
        tmin_ts = con.execute("SELECT MIN(ts) FROM weather WHERE ts>=? AND ts<? AND "
                              "outdoor_temp_c=?", (d0, d0 + 86400, tmin)).fetchone()[0]
        tmax_ts = con.execute("SELECT MIN(ts) FROM weather WHERE ts>=? AND ts<? AND "
                              "outdoor_temp_c=?", (d0, d0 + 86400, tmax)).fetchone()[0]
    tbase = float(meta_get(con, "gdd_tbase_c", "5") or 5)
    tavg = avg(t)
    fog = any(r[2] is not None and r[1] is not None and r[3] is not None
              and (r[1] - r[2]) < 1.0 and r[3] > 95 for r in rows)
    wmax = max([x for x in (r[5] for r in rows) if x is not None], default=None)
    con.execute("DELETE FROM v_daily WHERE day_epoch=?", (d0,))
    con.execute(
        "INSERT INTO v_daily(day_epoch,t_out_min,t_out_max,t_out_avg,t_out_min_time,"
        "t_out_max_time,t_out_amp,p_min,p_max,p_amp,wind_avg,wind_max,gust_max,"
        "wind_run_km,wind_dir_mode,rain_mm,rain_hours,rain_max_rate,solar_sum_wh_m2,"
        "uvi_max,sun_hours,gdd_day,frost_flag,hard_freeze_flag,fog_flag,thunder_flag,"
        "degree_days_heat,degree_days_cool,n_samples) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d0, tmin, tmax, tavg, tmin_ts, tmax_ts,
         round(tmax - tmin, 2) if (tmax is not None and tmin is not None) else None,
         min(pv) if pv else None, max(pv) if pv else None,
         round(max(pv) - min(pv), 2) if pv else None,
         avg([r[7] for r in rows]), wmax,
         max([x for x in (r[6] for r in rows) if x is not None], default=None),
         round(sum(x for x in (r[13] for r in rows) if x is not None) / 1000.0, 3),
         mode_card([r[8] for r in rows]), rain_mm, rain_hours,
         max([x for x in (r[9] for r in rows) if x is not None], default=None),
         round(sum(x for x in solar if x is not None) / 60.0, 1),
         max([x for x in (r[12] for r in rows) if x is not None], default=None),
         round(sum(1 for x in solar if x is not None and x > 50) / 60.0, 2),
         round(max(0.0, (tmax + tmin) / 2.0 - tbase), 2) if (tmax is not None and tmin is not None) else None,
         1 if (tmin is not None and tmin <= 0) else 0,
         1 if (tmin is not None and tmin <= -10) else 0,
         1 if fog else 0, 0,
         round(max(0.0, 18.0 - tavg), 2) if tavg is not None else None,
         round(max(0.0, tavg - 22.0), 2) if tavg is not None else None,
         len(rows)))
    ev = 0
    if len(rows) >= 720:  # гвард: только полные сутки флагуются
        ev += upsert_day_event(con, d0, "HEATWAVE", tmax is not None and tmax > 35,
                               tmax, "high", {"t_out_max": tmax, "day": d0})
        ev += upsert_day_event(con, d0, "CALM", wmax is not None and wmax < 1,
                               wmax, "low", {"wind_max": wmax, "day": d0})
        ndays = con.execute("SELECT COUNT(*) FROM v_daily WHERE day_epoch>=? AND day_epoch<=?",
                            (d0 - 6 * 86400, d0)).fetchone()[0]
        rain7 = con.execute("SELECT SUM(rain_mm) FROM v_daily WHERE day_epoch>=? AND day_epoch<=?",
                            (d0 - 6 * 86400, d0)).fetchone()[0]
        ev += upsert_day_event(con, d0, "DRY_SPELL",
                               ndays >= 5 and rain7 is not None and rain7 == 0,
                               rain7, "low",
                               {"rain_7d": rain7, "days_in_window": ndays,
                                "note": "solar-условие ТЗ упрощено (weather-8)"})
    return 1 + ev


def write_forecasts(con, now):
    """B3: persistence (+1h/+3h/+6h), zambretti (+6h/+12h), sager_day (+6h, день)."""
    last = con.execute("SELECT ts,outdoor_temp_c,pressure_rel_mmhg,outdoor_hum_pct,wind_ms "
                       "FROM weather ORDER BY ts DESC LIMIT 1").fetchone()
    if not last:
        return 0
    issued = int(now)
    last_ts = last[0]
    mon = time.localtime(now).tm_mon
    loc_h = time.localtime(now).tm_hour
    tend = p_tendency(con, last_ts)        # тренд от последнего замера (не от now)
    n = 0
    con.execute("BEGIN IMMEDIATE")
    for hz in (3600, 10800, 21600):
        con.execute("INSERT OR REPLACE INTO forecast(issued_at,target_ts,source,t_out_c,"
                    "p_rel_mmhg,rh_out_pct,wind_ms,confidence) VALUES(?,?,?,?,?,?,?,?)",
                    (issued, issued + hz, "persistence", last[1], last[2], last[3],
                     last[4], 0.5))
        n += 1
    letter, text = zambretti(last[2], tend, mon)
    if text:
        for hz in (21600, 43200):
            con.execute("INSERT OR REPLACE INTO forecast(issued_at,target_ts,source,"
                        "confidence,text) VALUES(?,?,?,?,?)",
                        (issued, issued + hz, "zambretti", 0.5, text))
            n += 1
    sager_note = "ночь"
    if 10 <= loc_h < 16:
        sol = con.execute("SELECT AVG(light_wm2) FROM weather WHERE ts>? AND ts<=?",
                          (last_ts - 7200, last_ts)).fetchone()[0]
        pot = SOLAR_POT.get(mon, 200)
        cf = max(0.0, min(1.0, (sol or 0.0) / pot)) if pot else None
        stext, ok = sager_day(last[2], tend, last[4], cf)
        if ok:
            con.execute("INSERT OR REPLACE INTO forecast(issued_at,target_ts,source,"
                        "confidence,text) VALUES(?,?,?,?,?)",
                        (issued, issued + 21600, "sager_day", 0.4, stext))
            n += 1
            sager_note = "да"
    con.execute("COMMIT")
    log(f"forecast: +{n} (persistence 3; zambretti {letter or '-'}; sager: {sager_note}; "
        f"trend {tend} мм {classify_trend(tend)})")
    return n


def hourly(con, now):
    """B1 hourly + B3 forecast. -> (hours_done, rows_written)."""
    first = con.execute("SELECT MIN(ts) FROM weather").fetchone()[0]
    if first is None:
        log("нет данных — hourly пропущен")
        return 0, 0
    cur_h = int(now) // 3600
    last = meta_get(con, "last_agg_hourly_epoch")
    first_h = int(first) // 3600
    if last:
        # хвост 3ч + догон необработанных (D10)
        start_h = min(cur_h - TAIL_HOURS, int(last) // 3600 + 1)
    else:
        start_h = first_h                      # первый прогон: вся история
    start_h = max(start_h, first_h)
    end_h = cur_h - 1                     # только завершившиеся окна
    hours_done, rows_written = 0, 0
    if start_h <= end_h:
        con.execute("BEGIN IMMEDIATE")
        for hi in range(start_h, end_h + 1):
            H = hi * 3600                          # epoch начала часового окна
            con.execute("DELETE FROM v_hourly WHERE hour_epoch=?", (H,))
            r = agg_hour_window(con, H, now)
            hours_done += 1
            rows_written += r
        con.execute("COMMIT")
        meta_set(con, "last_agg_hourly_epoch", end_h * 3600, now)
        log(f"hourly: окна {hours_done} ({start_h*3600}..{end_h*3600}), строк {rows_written}")
    else:
        log("hourly: новых завершившихся окон нет (хвост уже свежий)")
    fw = write_forecasts(con, now)
    return hours_done, rows_written + fw


def daily(con, now):
    """B1 daily + rolling-события. -> (days_done, rows_written)."""
    today0 = (int(now) + TZ_OFF) // 86400 * 86400 - TZ_OFF   # локальная полночь сегодня
    done, written = 0, 0
    for d0 in (today0 - 86400, today0 - 2 * 86400):          # вчера, позавчера
        if d0 > 0 and d0 < today0:
            r = agg_day_window(con, d0, now)
            done += 1
            written += r
    meta_set(con, "last_agg_daily_epoch", today0 - 86400, now)
    log(f"daily: суток {done}, строк/событий {written}")
    n = con.execute("DELETE FROM forecast WHERE issued_at < ?", (now - FORECAST_TTL,)).rowcount
    if n:
        log(f"forecast: чистка {n} строк старше 30 дней")
    return done, written


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    db = DEFAULT_DB
    if "--db" in sys.argv:
        db = sys.argv[sys.argv.index("--db") + 1]
    if kind not in ("hourly", "daily", "probe"):
        print("usage: weather_aggregator.py hourly|daily|probe [--db PATH]")
        return 2
    t0 = time.time()
    now = int(time.time())
    con = open_db(db)
    mig = migrate_v3(con, now)
    if kind == "probe":
        print("last_agg_hourly_epoch:", meta_get(con, "last_agg_hourly_epoch"))
        print("last_agg_daily_epoch:", meta_get(con, "last_agg_daily_epoch"))
        for t in ("v_hourly", "v_daily", "forecast"):
            print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        print("min/max ts:", con.execute("SELECT MIN(ts),MAX(ts) FROM weather").fetchone())
        con.close()
        return 0
    try:
        if kind == "hourly":
            proc, rows = hourly(con, now)
        else:
            proc, rows = daily(con, now)
        err = ""
    except Exception as e:  # noqa: BLE001
        con.execute("ROLLBACK") if con.in_transaction else None
        proc, rows, err = 0, 0, f"{type(e).__name__}: {e}"
        log("ERROR " + err)
    lat = int((time.time() - t0) * 1000)
    con.execute("INSERT OR REPLACE INTO materializer_log(ts,kind,hours_processed,rows_written,"
                "latency_ms,error) VALUES(?,?,?,?,?,?)", (now, kind, proc, rows, lat, err))
    con.close()
    if err:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
