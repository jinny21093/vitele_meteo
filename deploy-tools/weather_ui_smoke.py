#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke-тест weather-ui server.py (U0-U4) на копии живой БД — ЛОКАЛЬНО, без VM.

Проверки: health без auth; 401 + WWW-Authenticate (диалог); 405 POST; POST
без auth с телом -> 401 + закрытие соединения (r4-1/r5-1); авторизованный
POST -> 405 + Connection: close + закрытие (r6-4); окна
7д/90д/90д/365д; whitelist fields/types/severity; конверты {from,to,rows[,...]};
/api/now без id/schema_version (§5.1 v1.2.3); user= в authed-логе (§9 v1.2.3);
битый context -> null; ETag/304/gzip; CSP/no-store/nosniff; обход пути;
rate-limit 429 + Retry-After; graceful SIGTERM;
U4 (§5.5, v0.3.0): overlap-семантика окна T1a-T1f (вкл. приоритет скобок —
T1e), /events 200 без Chart.js, no-inline, старт без page-events.js -> exit 1.
U5 (§5.6, v0.4.0): /api/forecast — auth 401; пустая таблица -> 200
available:false (не 503); конверт по разведке (calc_ts/age_s/stale,
zambretti text+conf+targets 6/12ч, sager, persistence ×3 с horizon_h);
stale после UPDATE issued_at назад; /forecast 200 + page-forecast.js
без Chart.js, no-inline; старт без page-forecast.js -> exit 1.
U5-T2 (v0.4.0, дельты ревью владельца): letter — строка с letter ->
конверт содержит, легаси (letter NULL) -> null + экран жив; issued_values
— база расчёта на issued_at (фикстура issued_t=10.0/770.0 строго до
issued_at, forecast_t=12.0/772.0 -> Δ=+2.0; «сейчас» 12.8/775.8 НЕ
участвует); истории до issued_at нет -> issued_values null (Δ-ветка
деградации); клиент — page-forecast.js считает от issued_values.
DOM-ID forecast-страницы (в перечень; HTML не грепается — на ревью целиком):
fc-init, fc-init-body, fc-data, fc-fresh, fc-zam, fc-table, fc-sager,
refresh, last-update (+ общие шапки fresh-dot/fresh-text/batt-dot/batt-text/
ui-version/theme-toggle/banner).

Запуск: python scripts/weather_ui_smoke.py"""
import base64
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "/home/z/my-project/state/weather-ui"
TESTDIR = "/home/z/my-project/state/ui_test"
SRC_DB = "/home/z/my-project/state/weather8/weather_b_copy.db"
PORT = 8199
URL = f"http://127.0.0.1:{PORT}"
USER, PASS = "weather", "smoke-test-pass-123"

FAILS = []
PASSES = 0


def check(name, cond, extra=""):
    global PASSES
    if cond:
        PASSES += 1
        print(f"   OK {name}")
    else:
        FAILS.append(name)
        print(f" FAIL {name} {extra}")


def req(path, auth=None, method="GET", headers=None):
    r = urllib.request.Request(URL + path, method=method)
    if auth == "valid":
        r.add_header("Authorization", "Basic " +
                     base64.b64encode(f"{USER}:{PASS}".encode()).decode())
    elif auth == "wrong":
        r.add_header("Authorization", "Basic " +
                     base64.b64encode(b"wrong:wrong").decode())
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def prepare():
    shutil.rmtree(TESTDIR, ignore_errors=True)
    os.makedirs(TESTDIR)
    db = os.path.join(TESTDIR, "test.db")
    shutil.copy(SRC_DB, db)
    now = int(time.time())
    con = sqlite3.connect(db)
    # свежие строки: свежесть/collector_ok/окно 6ч/тенденция должны быть
    # детерминированы (копия БД — утренняя, реальный gap > 6 ч)
    con.executemany(
        "INSERT INTO weather (ts, outdoor_temp_c, outdoor_hum_pct, "
        "indoor_temp_c, pressure_rel_mmhg, wind_ms) VALUES (?,?,?,?,?,?)", [
            (now - 11100, 12.0, 80.0, 20.0, 775.2, 1.2),
            (now - 1800, 12.5, 79.0, 20.1, 775.5, 1.5),
            (now, 12.8, 78.0, 20.2, 775.8, 1.1),
        ])
    con.executemany(
        "INSERT INTO events (ts_start, ts_end, event_type, severity, value, context) "
        "VALUES (?,?,?,?,?,?)", [
            (now - 3600, now - 3000, "FROST", "high", -0.3,
             json.dumps({"t_out": -0.3})),
            (now - 2000, now - 1500, "RAPID_TEMP_DROP", "mid", -5.2,
             '{"broken": [not json'),                       # битый context
            (now - 600, None, "SENSOR_MISSING", "mid", 700,
             json.dumps({"prev_ts": now - 600})),           # открытое событие
        ])
    # U4-T1 (§5.5 overlap, v0.3.0): события слева/справа от основного окна
    # (from=now-86400, to=now): a) открыт слева -> видно; b) закрыт до from ->
    # нет; c) перекрывает from -> видно; d) старт после to -> нет
    con.executemany(
        "INSERT INTO events (ts_start, ts_end, event_type, severity, value, context) "
        "VALUES (?,?,?,?,?,?)", [
            (now - 5 * 86400, None, "DRY_SPELL", "low", 0.0,
             json.dumps({"rain_7d": 0})),                   # a: открыт слева
            (now - 5 * 86400, now - 4 * 86400, "HEATWAVE", "mid", 29.1,
             json.dumps({"t_max": 29.1})),                  # b: закрыт до from
            (now - 5 * 86400, now - 3600, "STRONG_WIND", "high", 16.2,
             json.dumps({"gust": 16.2})),                   # c: перекрывает from
            (now + 3600, None, "FOG", "low", None, None),  # d: старт после to
        ])
    # U4-T1f: 5100 событий внутри окна -> LIMIT 5000 + truncated=true
    con.executemany(
        "INSERT INTO events (ts_start, ts_end, event_type, severity, value, context) "
        "VALUES (?,?,?,?,?,?)",
        [(now - k * 10, now - k * 10 + 5, "CALM", "low", 0.8, None)
         for k in range(5100)])
    # v_hourly/v_daily: 5 часов и 3 суток до now (для U2/U3 — §5.3/§5.4)
    h0 = (now // 3600) * 3600
    con.executemany(
        "INSERT INTO v_hourly (hour_epoch,t_out_avg,t_out_min,t_out_max,p_rel_avg,"
        "wind_avg,wind_max,gust_max,wind_dir_mode,rain_mm,solar_avg,uvi_max,n_samples) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(h0 - k * 3600, 10.0 + k, 8.0, 12.0 + k, 775.0, 2.0, 4.0, 6.0, "С",
          0.5 * k, 100.0, 1.0, 55) for k in range(5)])
    # U5-T1 (§5.6, v0.4.0): прогноз детерминирован — таблица forecast пуста
    # (в копии живой БД строки этапа B есть); фиксстуры вставит main()
    # между available:false и available:true
    con.execute("DELETE FROM forecast")
    # миграция v3 этапа B (forecast.text) в копии БД weather-8 может
    # отсутствовать (агрегатор применяет её на своём старте) — идемпотентно;
    # запись в schema_migrations НЕ добавляем — чек meta migrations
    # ожидает последнюю версию копии (2)
    cols = [r[1] for r in con.execute("PRAGMA table_info(forecast)")]
    if "text" not in cols:
        con.execute("ALTER TABLE forecast ADD COLUMN text TEXT")
    if "letter" not in [r[1] for r in con.execute("PRAGMA table_info(forecast)")]:
        # U5-T2: миграция letter этапа B (U5-B1) — практика смоук-prepare:
        # идемпотентно, в schema_migrations НЕ пишем (чек meta migrations
        # ждёт последнюю версию копии = 2)
        con.execute("ALTER TABLE forecast ADD COLUMN letter TEXT")
    d0 = ((now + 10800) // 86400) * 86400 - 10800        # полночь MSK (этап B)
    con.executemany(
        "INSERT INTO v_daily (day_epoch,t_out_min,t_out_max,t_out_avg,t_out_min_time,"
        "t_out_max_time,p_min,p_max,wind_avg,wind_max,gust_max,wind_run_km,"
        "wind_dir_mode,rain_mm,rain_hours,solar_sum_wh_m2,uvi_max,gdd_day,"
        "frost_flag,hard_freeze_flag,fog_flag,n_samples) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(d0 - k * 86400, 5.0, 15.0, 10.0, d0 - 3600, d0 - 43200, 770.0, 778.0,
          3.0, 6.0, 9.0, 12.3, "Ю", 2.5, 4, 3000.0, 5.0, 0.0, 0, 0, 0, 1400)
         for k in range(3)])
    con.commit()
    con.close()
    cred = os.path.join(TESTDIR, ".creds")
    with open(cred, "w") as f:
        f.write(f"{USER}:{PASS}\n")
    os.chmod(cred, 0o600)
    return db, cred, now          # fixnow: ts_start-ассерты — относительно него


def main():
    db, cred, fixnow = prepare()
    log_path = os.path.join(TESTDIR, "server.log")
    logf = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BASE, "server.py"),
         "--db", db, "--port", str(PORT), "--bind", "127.0.0.1",
         "--cred", cred, "--static", os.path.join(BASE, "static")],
        stdout=logf, stderr=subprocess.STDOUT, cwd=BASE)
    try:
        for _ in range(50):                       # ждём живость
            try:
                st, _, _ = req("/api/health")
                if st == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            print("server did not start; log:")
            print(open(log_path).read())
            return 1

        now = int(time.time())

        # --- health (§5.8): без auth, формат ---
        st, hd, body = req("/api/health")
        j = json.loads(body)
        check("health 200 no-auth", st == 200)
        check("health keys", set(j) == {"status", "db", "last_ts"}, str(j))
        check("health nosniff", hd.get("X-Content-Type-Options") == "nosniff")

        # --- auth (§3) ---
        st, hd, body = req("/api/now")
        check("now no-auth 401", st == 401)
        check("401 WWW-Authenticate exact",
              hd.get("WWW-Authenticate") == 'Basic realm="Weather", charset="UTF-8"',
              repr(hd.get("WWW-Authenticate")))
        check("401 text/plain", "text/plain" in hd.get("Content-Type", ""))
        st, _, _ = req("/api/now", auth="wrong")
        check("now wrong 401", st == 401)
        st, hd, body = req("/api/now", auth="valid")
        j = json.loads(body)
        check("now valid 200", st == 200)
        check("now shape", all(k in j for k in ("current", "status", "now", "p_tendency_3h")))
        check("now status keys", set(j["status"]) == {"collector_ok", "last_poll_ts", "gap_s"})
        check("now collector_ok", j["status"]["collector_ok"] is True)
        check("now no id/schema_version (§5.1 v1.2.3)",
              "id" not in j["current"] and "schema_version" not in j["current"],
              str(sorted(j["current"])[:4]))
        check("now no-store", hd.get("Cache-Control") == "no-store")

        # --- POST -> 405 (§3) ---
        st, _, _ = req("/api/now", auth="valid", method="POST")
        check("POST 405", st == 405)

        # --- POST без auth с телом -> 401 + соединение закрыто (r4-1/r5-1) ---
        # Дискриминатор закрытия — пара запросов одним вызовом curl (--next).
        # Одиночный curl -v не годится: 401 уходит БЕЗ заголовка Connection:
        # close, и curl печатает "left intact" даже по закрытому сервером
        # сокету. num_connects 2-го трансфера: 1 — свежий connect (сервер
        # закрыл соединение после 401), 0 — reuse (keep-alive = регресс).
        try:
            p = subprocess.run(
                ["curl", "-v", "-s", "-o", "/dev/null",
                 "-w", "%{http_code} %{num_connects}\n", "--max-time", "10",
                 "-X", "POST", "--data", "a=1", URL + "/api/now",
                 "--next", "-s", "-o", "/dev/null",
                 "-w", "%{http_code} %{num_connects}\n", "--max-time", "10",
                 "-u", f"{USER}:{PASS}", URL + "/api/now"],
                capture_output=True, text=True, timeout=30)
            out = [ln.split() for ln in p.stdout.splitlines() if ln.strip()]
            check("POST no-auth with body -> 401 + conn closed (r4-1/r5-1)",
                  len(out) == 2 and out[0] == ["401", "1"] and
                  out[1] == ["200", "1"] and "< HTTP/1.1 401" in p.stderr,
                  f"stdout={p.stdout!r} stderr=...{p.stderr[-200:]}")
        except FileNotFoundError:
            check("POST no-auth with body -> 401 + conn closed (r4-1/r5-1)",
                  False, "curl недоступен")

        # --- авторизованный POST -> 405 + Connection: close + закрыто (r6-4) ---
        # В пару к 401-проверке. У 405 заголовок Connection: close есть,
        # поэтому дискриминатор тривиален: < Connection: close в verbose;
        # num_connects 2-го трансфера — для симметрии с 401-проверкой.
        try:
            p = subprocess.run(
                ["curl", "-v", "-s", "-o", "/dev/null",
                 "-w", "%{http_code} %{num_connects}\n", "--max-time", "10",
                 "-X", "POST", "--data", "a=1", "-u", f"{USER}:{PASS}",
                 URL + "/api/now",
                 "--next", "-s", "-o", "/dev/null",
                 "-w", "%{http_code} %{num_connects}\n", "--max-time", "10",
                 "-u", f"{USER}:{PASS}", URL + "/api/now"],
                capture_output=True, text=True, timeout=30)
            out = [ln.split() for ln in p.stdout.splitlines() if ln.strip()]
            check("POST authed -> 405 + Connection: close + conn closed (r6-4)",
                  len(out) == 2 and out[0] == ["405", "1"] and
                  out[1] == ["200", "1"] and "< HTTP/1.1 405" in p.stderr and
                  "< Connection: close" in p.stderr,
                  f"stdout={p.stdout!r} stderr=...{p.stderr[-200:]}")
        except FileNotFoundError:
            check("POST authed -> 405 + Connection: close + conn closed (r6-4)",
                  False, "curl недоступен")

        # --- history (§5.2) ---
        st, _, body = req("/api/history", auth="valid")
        check("history missing from/to 400", st == 400)
        st, _, _ = req(f"/api/history?from={now}&to={now - 1}", auth="valid")
        check("history from>=to 400", st == 400)
        st, _, _ = req(f"/api/history?from={now - 8 * 86400}&to={now}", auth="valid")
        check("history window>7d 400", st == 400)
        st, _, body = req(f"/api/history?from={now - 6 * 3600}&to={now}", auth="valid")
        j = json.loads(body)
        check("history 6h 200", st == 200)
        check("history envelope", set(j) == {"from", "to", "fields", "rows", "truncated"})
        check("history rows arrays", isinstance(j["rows"][0], list) and
              len(j["rows"]) >= 3 and len(j["rows"][0]) == len(j["fields"]) == 13)
        check("history truncated false", j["truncated"] is False)
        st, _, body = req(f"/api/history?from={now - 3600}&to={now}"
                          "&fields=ts,battery_raw,wind_chill_c", auth="valid")
        j = json.loads(body)
        check("history whitelist non-default OK", st == 200 and j["fields"] ==
              ["ts", "battery_raw", "wind_chill_c"])
        st, _, _ = req(f"/api/history?from={now - 3600}&to={now}&fields=ts,evil;--",
                       auth="valid")
        check("history unknown field 400", st == 400)
        st, _, _ = req(f"/api/history?from={now - 7 * 86400}&to={now}", auth="valid")
        check("history window==7d OK", st == 200)

        # --- meta (§5.7) ---
        st, _, body = req("/api/meta", auth="valid")
        j = json.loads(body)
        check("meta 200", st == 200)
        check("meta wmeta tz", j["wmeta"].get("tz_offset_seconds") == "10800")
        check("meta collector_log<=20", len(j["collector_log"]) <= 20)
        check("meta migrations", j["schema_migrations"]["version"] == 2)

        # --- events (§5.5) ---
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}", auth="valid")
        j = json.loads(body)
        check("events envelope", set(j) == {"from", "to", "rows", "truncated"})
        by_type = {}
        for r in j["rows"]:
            by_type.setdefault(r["event_type"], []).append(r)
        check("events broken context -> null",
              by_type["RAPID_TEMP_DROP"][0]["context"] is None)
        frost = by_type["FROST"][0]
        check("events context parsed", frost["context"] == {"t_out": -0.3})
        check("events duration closed", frost["duration_s"] == 600)
        check("events duration open null",
              by_type["SENSOR_MISSING"][0]["duration_s"] is None)
        st, _, _ = req(f"/api/events?from={now - 86400}&to={now}&types=FROST,NOPE",
                       auth="valid")
        check("events unknown type 400", st == 400)
        st, _, _ = req(f"/api/events?from={now - 86400}&to={now}&severity=zzz",
                       auth="valid")
        check("events unknown severity 400", st == 400)
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}"
                          "&types=frost,SENSOR_MISSING", auth="valid")
        j = json.loads(body)
        check("events type filter (case-insens)",
              st == 200 and {r["event_type"] for r in j["rows"]} ==
              {"FROST", "SENSOR_MISSING"})
        st, _, body = req(f"/api/events?from={now - 91 * 86400}&to={now}", auth="valid")
        check("events window>90d 400", st == 400)

        # --- U4: overlap-семантика окна (§5.5, v0.3.0) ---
        # e (ПРИОРИТЕТ, U4-S1): событие FROST-фикстуры внутри окна + фильтр по
        # ДРУГОМУ типу -> 0 строк. Ловит потерю внешних скобок overlap-условия:
        # без них "(A AND B) OR (C AND type IN ...)" пропускает ветку A мимо
        # фильтра (приоритет AND над OR) — и FROST утёк бы в ответ.
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}"
                          "&types=BATTERY_LOW", auth="valid")
        j = json.loads(body)
        check("U4-T1e overlap+filter: window rows bypass type filter (скобки)",
              st == 200 and j["rows"] == [], str(j.get("rows"))[:120])
        # a: открытое, ts_start < from -> ВИДИМО (ветка ts_end IS NULL)
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}"
                          "&types=DRY_SPELL", auth="valid")
        j = json.loads(body)
        check("U4-T1a overlap: open event started before from -> visible",
              st == 200 and len(j["rows"]) == 1 and
              j["rows"][0]["ts_start"] == fixnow - 5 * 86400 and
              j["rows"][0]["ts_end"] is None and
              j["rows"][0]["duration_s"] is None, str(j["rows"])[:160])
        # b: закрытое, ts_end < from -> НЕ видно
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}"
                          "&types=HEATWAVE", auth="valid")
        j = json.loads(body)
        check("U4-T1b overlap: closed event ended before from -> invisible",
              st == 200 and j["rows"] == [], str(j.get("rows"))[:120])
        # c: началось слева, закрылось внутри окна -> ВИДИМО
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}"
                          "&types=STRONG_WIND", auth="valid")
        j = json.loads(body)
        check("U4-T1c overlap: event spans from -> visible",
              st == 200 and len(j["rows"]) == 1 and
              j["rows"][0]["duration_s"] == 5 * 86400 - 3600, str(j["rows"])[:160])
        # d: ts_start > to -> НЕ видно
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}"
                          "&types=FOG", auth="valid")
        j = json.loads(body)
        check("U4-T1d overlap: starts after to -> invisible",
              st == 200 and j["rows"] == [], str(j.get("rows"))[:120])
        # f: 5100 событий в окне -> truncated=true, ровно 5000 строк
        st, _, body = req(f"/api/events?from={now - 86400}&to={now}", auth="valid")
        j = json.loads(body)
        check("U4-T1f overlap: >5000 events -> truncated true, 5000 returned",
              st == 200 and j["truncated"] is True and len(j["rows"]) == 5000,
              f"n={len(j.get('rows', []))} truncated={j.get('truncated')}")
        # реальный gap копии БД > 6 ч: с инжектированной свежей строкой
        # (ts=now) collector_ok обязан стать True, а окно 6ч — непустым
        st, _, body = req("/api/now", auth="valid")
        j = json.loads(body)
        check("now collector_ok (fresh row)", j["status"]["collector_ok"] is True,
              str(j["status"]))
        check("now p_tendency computed", j["p_tendency_3h"] is not None)

        # --- hourly (§5.3) ---
        st, _, _ = req("/api/hourly", auth="valid")
        check("hourly missing from/to 400", st == 400)
        st, _, _ = req(f"/api/hourly?from={now}&to={now - 1}", auth="valid")
        check("hourly from>=to 400", st == 400)
        st, _, _ = req(f"/api/hourly?from={now - 91 * 86400}&to={now}", auth="valid")
        check("hourly window>90d 400", st == 400)
        st, _, body = req(f"/api/hourly?from={now - 90 * 86400}&to={now}", auth="valid")
        j = json.loads(body)
        check("hourly window==90d 200", st == 200)
        check("hourly envelope", set(j) == {"from", "to", "fields", "rows"})
        check("hourly fields spec", j["fields"] == [
            "hour_epoch", "t_out_avg", "t_out_min", "t_out_max", "p_rel_avg",
            "wind_avg", "wind_max", "gust_max", "wind_dir_mode", "rain_mm",
            "solar_avg", "uvi_max", "n_samples"])
        check("hourly rows arrays", len(j["rows"]) == 5 and
              all(len(r) == 13 for r in j["rows"]))
        check("hourly ordered", j["rows"][0][0] < j["rows"][-1][0])
        # сортировка ASC: последняя строка = текущий час (k=0) -> t_out_avg 10.0,
        # первая = час 4 часа назад (k=4) -> 14.0
        check("hourly values", j["rows"][-1][1] == 10.0 and j["rows"][0][1] == 14.0)

        # --- daily (§5.4) ---
        st, _, _ = req(f"/api/daily?from={now - 366 * 86400}&to={now}", auth="valid")
        check("daily window>365d 400", st == 400)
        st, _, body = req(f"/api/daily?from={now - 365 * 86400}&to={now}", auth="valid")
        j = json.loads(body)
        check("daily window==365d 200", st == 200)
        check("daily envelope", set(j) == {"from", "to", "fields", "rows"})
        check("daily fields count", len(j["fields"]) == 22)
        check("daily rows", len(j["rows"]) == 3 and len(j["rows"][0]) == 22)
        check("daily no truncated key", "truncated" not in j)
        st, _, _ = req("/api/forecast")
        check("U5-T1: forecast без auth 401 (§3)", st == 401)
        st, _, body = req("/api/forecast", auth="valid")
        j = json.loads(body)
        check("U5-T1a: пустая таблица -> 200 available:false (не 503, §5.6)",
              st == 200 and j.get("available") is False)
        check("U5-T1a: reason строкой", isinstance(j.get("reason"), str))
        # фиксстуры: один прогон этапа B (issued_at = now-2 c — свежий,
        # age_s<=5, не stale): persistence ×3 (+1/+3/+6 ч) + zambretti ×2
        # (+6/+12 ч) + sager ×1 (+6 ч)
        issued = now - 2
        con = sqlite3.connect(db)
        con.executemany(
            "INSERT INTO forecast (issued_at, target_ts, source, t_out_c, "
            "p_rel_mmhg, rh_out_pct, wind_ms, confidence, text) "
            "VALUES (?,?,?,?,?,?,?,?,?)", [
                (issued, issued + 3600, "persistence", 12.8, 775.8, 78.0, 1.1,
                 0.5, None),
                (issued, issued + 10800, "persistence", 12.8, 775.8, 78.0, 1.1,
                 0.5, None),
                (issued, issued + 21600, "persistence", 12.8, 775.8, 78.0, 1.1,
                 0.5, None),
                (issued, issued + 21600, "zambretti", None, None, None, None,
                 0.5, "Fairly fine, improving — Довольно ясно, улучшение"),
                (issued, issued + 43200, "zambretti", None, None, None, None,
                 0.5, "Fairly fine, improving — Довольно ясно, улучшение"),
                (issued, issued + 21600, "sager_day", None, None, None, None,
                 0.4, "Малооблачно, преимущественно сухо"),
            ])
        con.commit()
        con.close()
        st, _, body = req("/api/forecast", auth="valid")
        j = json.loads(body)
        check("U5-T1b: 200 available:true",
              st == 200 and j.get("available") is True)
        check("U5-T1b: calc_ts=issued, age_s<=5, stale:false",
              j.get("calc_ts") == issued and 0 <= j.get("age_s", 10**9) <= 5
              and j.get("stale") is False)
        pers = j.get("persistence") or []
        check("U5-T1b: persistence ×3, горизонты 1/3/6 ч",
              len(pers) == 3 and [r.get("horizon_h") for r in pers] == [1, 3, 6])
        check("U5-T1b: persistence поля (t/p/rh/wind/conf)",
              bool(pers) and pers[0].get("t_out_c") == 12.8 and
              pers[0].get("p_rel_mmhg") == 775.8 and
              pers[0].get("rh_out_pct") == 78.0 and
              pers[0].get("wind_ms") == 1.1 and
              pers[0].get("confidence") == 0.5)
        z = j.get("zambretti") or {}
        check("U5-T1c: zambretti text+conf+targets 6/12 ч",
              z.get("text") == "Fairly fine, improving — Довольно ясно, улучшение"
              and z.get("confidence") == 0.5 and
              [t.get("horizon_h") for t in z.get("targets") or []] == [6, 12])
        s = j.get("sager") or {}
        check("U5-T1c: sager text+conf (0.4)",
              s.get("text") == "Малооблачно, преимущественно сухо" and
              s.get("confidence") == 0.4)
        # U5-S2: прогон старше 2 ч -> stale:true + возраст
        con = sqlite3.connect(db)
        con.execute("UPDATE forecast SET issued_at=? WHERE issued_at=?",
                    (now - 10800, issued))
        con.commit()
        con.close()
        st, _, body = req("/api/forecast", auth="valid")
        j = json.loads(body)
        check("U5-T1d: прогон 3 ч назад -> stale:true, age_s>=7200",
              st == 200 and j.get("stale") is True and j.get("age_s") >= 7200)

        # --- U5-T2 (v0.4.0, дельты ревью владельца): letter + issued_values ---
        iss2 = now - 10800                      # issued_at после UPDATE выше (T1d)
        con = sqlite3.connect(db)
        # (a) строка с letter -> конверт содержит (источник буквы — этап B)
        con.execute("UPDATE forecast SET letter='A' WHERE source='zambretti' "
                    "AND issued_at=?", (iss2,))
        # (c) база расчёта: замер issued_t=10.0/770.0 строго ДО issued_at
        # (ts=iss2-60); «сейчас» (ts=now, 12.8/775.8) в Δ НЕ участвует;
        # forecast_t=12.0/772.0 -> Δ = +2.0 (математика клиента от issued_values)
        con.execute("INSERT INTO weather (ts, outdoor_temp_c, outdoor_hum_pct, "
                    "indoor_temp_c, pressure_rel_mmhg, wind_ms) VALUES (?,?,?,?,?,?)",
                    (iss2 - 60, 10.0, 80.0, 20.0, 770.0, 1.2))
        con.execute("UPDATE forecast SET t_out_c=12.0, p_rel_mmhg=772.0 "
                    "WHERE source='persistence' AND issued_at=?", (iss2,))
        con.commit()
        con.close()
        st, _, body = req("/api/forecast", auth="valid")
        j = json.loads(body)
        z = j.get("zambretti") or {}
        check("U5-T2a: строка с letter -> конверт содержит",
              st == 200 and j.get("available") is True and z.get("letter") == "A")
        iv = j.get("issued_values")
        check("U5-T2c: issued_values от issued_at (10.0/770.0), не current 12.8/775.8",
              iv is not None and iv.get("t_out_c") == 10.0 and
              iv.get("p_rel_mmhg") == 770.0)
        pers = j.get("persistence") or []
        check("U5-T2c: Δ-математика = +2.0 (forecast 12.0 − issued 10.0, не от current)",
              bool(pers) and bool(iv) and pers[0].get("t_out_c") == 12.0 and
              round(pers[0]["t_out_c"] - iv["t_out_c"], 1) == 2.0 and
              round(pers[0]["p_rel_mmhg"] - iv["p_rel_mmhg"], 1) == 2.0)
        st, _, body = req("/static/page-forecast.js", auth="valid")
        check("U5-T2d: клиент Δ от issued_values (page-forecast.js содержит)",
              st == 200 and b"issued_values" in body and b"calc_ts" in body)
        # (b) легаси: строка без letter -> letter null; (e) истории до
        # issued_at нет -> issued_values null (Δ-ветка деградации)
        con = sqlite3.connect(db)
        con.execute("UPDATE forecast SET letter=NULL WHERE source='zambretti' "
                    "AND issued_at=?", (iss2,))
        con.execute("UPDATE forecast SET issued_at=(SELECT MIN(ts)-100 FROM weather) "
                    "WHERE issued_at=?", (iss2,))
        con.commit()
        con.close()
        st, _, body = req("/api/forecast", auth="valid")
        j = json.loads(body)
        z = j.get("zambretti") or {}
        check("U5-T2b: легаси-строка без letter -> letter null, текст жив",
              st == 200 and z.get("text") is not None and z.get("letter") is None)
        check("U5-T2e: истории до issued_at нет -> issued_values null",
              st == 200 and j.get("issued_values") is None and
              j.get("persistence"))
        st, _, _ = req("/forecast", auth="valid")
        check("U5-T2b: экран жив при легаси/деградации (/forecast 200)", st == 200)

        st, _, _ = req("/api/export.csv?from=1&to=2", auth="valid")
        check("export 404 (U6)", st == 404)

        # --- статика/HTML/CSP/кэш (§5.0; auth на всё, включая статику — §3) ---
        st, hd, body = req("/", auth="valid")
        check("/ 200 html", st == 200 and "text/html" in hd.get("Content-Type", ""))
        check("CSP on HTML", "default-src 'self'" in hd.get("Content-Security-Policy", ""))
        check("HTML no-cache", hd.get("Cache-Control") == "no-cache")
        check("nosniff on HTML", hd.get("X-Content-Type-Options") == "nosniff")
        check("no inline scripts", b"<script>" not in body.replace(
            b'<script src=', b'<script-'))          # только src, не inline
        st, _, _ = req("/day", auth="valid")
        check("/day 200", st == 200)
        st, _, body = req("/day", auth="valid")
        check("day page has charts+scripts", b"chart-t" in body and
              b"page-day.js" in body)
        st, _, body = req("/month", auth="valid")
        check("/month 200", st == 200 and b"page-month.js" in body)
        # U4-C1 (§4.4): /events — настоящий экран, БЕЗ Chart.js (таймлайн — DOM)
        st, _, body = req("/events", auth="valid")
        check("/events 200 + page-events.js", st == 200 and
              b"page-events.js" in body)
        check("events page без Chart.js (§4.4)", b"chart.min.js" not in body)
        check("events no inline scripts", b"<script>" not in body.replace(
            b'<script src=', b'<script-'))          # только src, не inline
        st, _, _ = req("/static/page-events.js", auth="valid")
        check("page-events.js 200", st == 200)
        # U5-C1 (§4.5): /forecast — настоящий экран, БЕЗ Chart.js
        st, _, body = req("/forecast", auth="valid")
        check("/forecast 200 + page-forecast.js", st == 200 and
              b"page-forecast.js" in body)
        check("forecast page без Chart.js (§4.5)", b"chart.min.js" not in body)
        check("forecast no inline scripts", b"<script>" not in body.replace(
            b'<script src=', b'<script-'))          # только src, не inline
        st, _, _ = req("/static/page-forecast.js", auth="valid")
        check("page-forecast.js 200", st == 200)
        st, _, _ = req("/settings", auth="valid")
        check("/settings 200", st == 200)
        st, _, _ = req("/", auth="wrong")
        check("/ без auth 401 (§3: auth на всё)", st == 401)
        st, hd, body = req("/static/vendor/chart.min.js", auth="valid",
                           headers={"Accept-Encoding": "gzip"})
        check("chart.js 200", st == 200)
        check("chart.js gzip", hd.get("Content-Encoding") == "gzip")
        etag = hd.get("ETag", "")
        check("chart.js ETag", etag.startswith('"'))
        check("chart.js Vary", hd.get("Vary") == "Accept-Encoding")
        # M-3 (ревью r1-r3): html/js/css — no-cache (ревалидация по ETag)
        check("chart.js cache no-cache (M-3)",
              hd.get("Cache-Control") == "no-cache")
        st, hd, _ = req("/static/vendor/chart.min.js", auth="valid",
                        headers={"If-None-Match": etag})
        check("ETag -> 304", st == 304 and "Content-Length" not in hd)
        st, _, _ = req("/static/style.css", auth="valid")
        check("style.css 200", st == 200)
        st, hd, _ = req("/static/style.css", auth="valid")
        check("style.css no-cache (M-3)", hd.get("Cache-Control") == "no-cache")
        st, hd, _ = req("/static/icons/favicon.svg", auth="valid")
        check("favicon.svg 200", st == 200)
        # M-3: долгий кэш — только бинарные ассеты
        check("favicon.svg max-age=86400 (M-3)",
              "max-age=86400" in hd.get("Cache-Control", ""))
        st, _, _ = req("/icons/favicon.svg", auth="valid")
        check("/icons/ alias 200", st == 200)
        st, _, _ = req("/static/../server.py", auth="valid")
        check("path traversal 404", st == 404)
        st, _, _ = req("/static/%2e%2e/server.py", auth="valid")
        check("path traversal encoded 404", st == 404)
        st, _, _ = req("/nope", auth="valid")
        check("unknown 404", st == 404)

        # --- SIGTERM -> graceful (§2.2) ---
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=15)
        log = open(log_path).read()
        check("SIGTERM exit 0", rc == 0, f"rc={rc}")
        check("shutdown clean in log", "stop clean=1" in log)
        check("no Authorization in log", "Basic " not in log)
        check("requests logged", "GET /api/now 200" in log)
        check("user= on authed INFO (§9 v1.2.3)", "user=weather" in log)
        check("401 lines without user=", all("user=" not in ln
              for ln in log.splitlines() if " 401 " in ln))

        # --- rate-limit (§3): 12 неудач -> 10x401, затем 429+Retry-After ---
        proc2 = subprocess.Popen(
            [sys.executable, os.path.join(BASE, "server.py"),
             "--db", db, "--port", str(PORT), "--bind", "127.0.0.1",
             "--cred", cred, "--static", os.path.join(BASE, "static")],
            stdout=logf, stderr=subprocess.STDOUT, cwd=BASE)
        for _ in range(50):
            try:
                if req("/api/health")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        codes = []
        for _ in range(12):
            codes.append(req("/api/now", auth="wrong")[0])
        check("rate limit 10x401", codes[:10] == [401] * 10, str(codes))
        check("rate limit then 429", set(codes[10:]) == {429}, str(codes))
        st, hd, _ = req("/api/now", auth="wrong")
        check("429 Retry-After 60", st == 429 and hd.get("Retry-After") == "60")
        # даже верный пароль не спасает, пока окно не истекло (антибрут)
        st, _, _ = req("/api/now", auth="valid")
        check("429 blocks even valid creds", st == 429)
        proc2.send_signal(signal.SIGTERM)
        rc2 = proc2.wait(timeout=15)
        check("second SIGTERM exit 0", rc2 == 0, f"rc={rc2}")

        # --- U4-S4 (M-4): старт без page-events.js -> required static, exit 1.
        # После proc2 (иначе её старт упал бы): page-events.js удаляется из
        # BASE/static, сервер обязан отказаться стартовать (rc=1, ERROR в лог).
        # Самовосстановление: байты читаются ДО удаления и пишутся обратно
        # после проверок — повторный прогон смоука не ломается.
        pej = os.path.join(BASE, "static", "page-events.js")
        with open(pej, "rb") as f:
            pej_bytes = f.read()
        os.remove(pej)
        proc3 = subprocess.Popen(
            [sys.executable, os.path.join(BASE, "server.py"),
             "--db", db, "--port", "8201", "--bind", "127.0.0.1",
             "--cred", cred, "--static", os.path.join(BASE, "static")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=BASE)
        rc3 = proc3.wait(timeout=20)
        log3 = proc3.stdout.read().decode("utf-8", "replace")
        check("U4-S4: missing page-events.js -> exit 1", rc3 == 1, f"rc={rc3}")
        check("U4-S4: startup error names page-events.js",
              "required static file missing" in log3 and
              "page-events.js" in log3,
              log3.strip().splitlines()[-1] if log3.strip() else "")
        with open(pej, "wb") as f:
            f.write(pej_bytes)

        # --- U5-S3 (M-4): старт без page-forecast.js -> required static, exit 1
        # (по образцу U4-S4; самовосстановление: байты читаются ДО удаления)
        pfj = os.path.join(BASE, "static", "page-forecast.js")
        with open(pfj, "rb") as f:
            pfj_bytes = f.read()
        os.remove(pfj)
        proc4 = subprocess.Popen(
            [sys.executable, os.path.join(BASE, "server.py"),
             "--db", db, "--port", "8202", "--bind", "127.0.0.1",
             "--cred", cred, "--static", os.path.join(BASE, "static")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=BASE)
        rc4 = proc4.wait(timeout=20)
        log4 = proc4.stdout.read().decode("utf-8", "replace")
        check("U5-S3: missing page-forecast.js -> exit 1", rc4 == 1, f"rc={rc4}")
        check("U5-S3: startup error names page-forecast.js",
              "required static file missing" in log4 and
              "page-forecast.js" in log4,
              log4.strip().splitlines()[-1] if log4.strip() else "")
        with open(pfj, "wb") as f:
            f.write(pfj_bytes)

        print(f"\n{'=' * 46}\nSMOKE: {PASSES} OK, {len(FAILS)} FAIL"
              + (f" -> {FAILS}" if FAILS else " — ALL PASSED"))
        return 1 if FAILS else 0
    finally:
        if proc.poll() is None:
            proc.kill()
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
