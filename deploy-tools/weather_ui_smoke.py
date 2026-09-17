#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke-тест weather-ui server.py (U0-U3) на копии живой БД — ЛОКАЛЬНО, без VM.

Проверки: health без auth; 401 + WWW-Authenticate (диалог); 405 POST; окна
7д/90д/90д/365д; whitelist fields/types/severity; конверты {from,to,rows[,...]};
/api/now без id/schema_version (§5.1 v1.2.3); user= в authed-логе (§9 v1.2.3);
битый context -> null; ETag/304/gzip; CSP/no-store/nosniff; обход пути;
rate-limit 429 + Retry-After; graceful SIGTERM.

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
    # v_hourly/v_daily: 5 часов и 3 суток до now (для U2/U3 — §5.3/§5.4)
    h0 = (now // 3600) * 3600
    con.executemany(
        "INSERT INTO v_hourly (hour_epoch,t_out_avg,t_out_min,t_out_max,p_rel_avg,"
        "wind_avg,wind_max,gust_max,wind_dir_mode,rain_mm,solar_avg,uvi_max,n_samples) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(h0 - k * 3600, 10.0 + k, 8.0, 12.0 + k, 775.0, 2.0, 4.0, 6.0, "С",
          0.5 * k, 100.0, 1.0, 55) for k in range(5)])
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
    return db, cred


def main():
    db, cred = prepare()
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
        st, _, _ = req("/api/forecast", auth="valid")
        check("forecast 404 (U5)", st == 404)
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

        print(f"\n{'=' * 46}\nSMOKE: {PASSES} OK, {len(FAILS)} FAIL"
              + (f" -> {FAILS}" if FAILS else " — ALL PASSED"))
        return 1 if FAILS else 0
    finally:
        if proc.poll() is None:
            proc.kill()
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
