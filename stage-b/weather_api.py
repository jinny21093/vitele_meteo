#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_api.py — этап B (roadmap B4/B5, weather-8): REST API-скелет.
stdlib http.server (Flask не ставим — зависимость не нужна), порт 8090, bind 0.0.0.0
(наружу закрыто NAT Keenetic; доступ LAN + ZeroTier). Basic auth — обязателен на всех
эндпоинтах данных; /health — без auth (только живость, метеоданных не выдаёт).
Креды: /home/auditbot/weather-dash/api_auth.conf, формат "user:pass", chmod 600.
БД: read-only соединение (file:...?mode=ro + PRAGMA query_only), busy_timeout 5 с.
Эндпоинты: /health /now /history /hourly /daily /events /forecast /csv
Тренды 1h/3h/6h — по временным окнам (avg конца минус avg начала, не LAG-строки).
Запуск: python3 weather_api.py [--db PATH] [--port N]"""
import base64
import hmac
import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weather_zam import zambretti, sager_day, classify_trend, SOLAR_POT  # noqa: E402

DEFAULT_DB = "/home/auditbot/weather-dash/weather.db"
AUTH_CONF = "/home/auditbot/weather-dash/api_auth.conf"
MAX_LIMIT = 10000
TZ_OFF = 10800


def log(msg):
    print(f"[api {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_auth():
    try:
        line = open(AUTH_CONF).read().strip()
        u, p = line.split(":", 1)
        return u, p
    except Exception:  # noqa: BLE001
        log("WARN: api_auth.conf не читается — только /health")
        return None, None


def db_connect(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def rows_to_dicts(cur):
    return [dict(r) for r in cur.fetchall()]


def window_delta(con, col, now, hours):
    """avg(последние 10 мин) − avg(за hours до того, те же 10 мин) — окно по времени."""
    a = con.execute(f"SELECT AVG({col}) FROM weather WHERE ts>? AND ts<=?",
                    (now - 600, now)).fetchone()[0]
    b = con.execute(f"SELECT AVG({col}) FROM weather WHERE ts>? AND ts<=?",
                    (now - hours * 3600 - 600, now - hours * 3600)).fetchone()[0]
    return round(a - b, 3) if (a is not None and b is not None) else None


def now_payload(con, now):
    row = con.execute("SELECT * FROM weather ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return {"error": "нет данных"}
    d = dict(row)
    d.pop("schema_version", None)
    tr = {
        "t_out_1h": window_delta(con, "outdoor_temp_c", now, 1),
        "t_out_3h": window_delta(con, "outdoor_temp_c", now, 3),
        "t_out_6h": window_delta(con, "outdoor_temp_c", now, 6),
        "p_tendency_3h": window_delta(con, "pressure_rel_mmhg", now, 3),
        "rh_out_1h": window_delta(con, "outdoor_hum_pct", now, 1),
    }
    tr["pressure_class"] = classify_trend(tr["p_tendency_3h"])
    if d.get("outdoor_temp_c") is not None and d.get("dew_point_c") is not None:
        tr["dew_point_spread"] = round(d["outdoor_temp_c"] - d["dew_point_c"], 2)
    mon = time.localtime(now).tm_mon
    loc_h = time.localtime(now).tm_hour
    letter, ztext = zambretti(d.get("pressure_rel_mmhg"), tr["p_tendency_3h"], mon)
    forecast_now = {"zambretti_letter": letter, "zambretti": ztext}
    if 10 <= loc_h < 16:
        sol = con.execute("SELECT AVG(light_wm2) FROM weather WHERE ts>? AND ts<=?",
                          (now - 7200, now)).fetchone()[0]
        pot = SOLAR_POT.get(mon, 200)
        cf = max(0.0, min(1.0, (sol or 0.0) / pot)) if pot else None
        stext, ok = sager_day(d.get("pressure_rel_mmhg"), tr["p_tendency_3h"],
                              d.get("wind_ms"), cf)
        if ok:
            forecast_now["sager"] = stext
    hour_cnt = con.execute("SELECT COUNT(*) FROM weather WHERE ts>?", (now - 3600,)).fetchone()[0]
    return {"now": now, "last_ts": d.get("ts"), "age_s": now - (d.get("ts") or 0),
            "current": d, "trends": tr, "forecast_now": forecast_now,
            "samples_last_hour": hour_cnt}


def make_handler(db, auth):
    class H(BaseHTTPRequestHandler):
        server_version = "weather-api/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 — в journald свой формат
            log(f"{self.address_string()} {fmt % args}")

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _csv(self, cur):
            cols = [c[0] for c in cur.description]
            out = ["\t".join(cols)]
            for r in cur.fetchall():
                out.append("\t".join("" if v is None else str(v) for v in r))
            body = ("\n".join(out) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            if auth[0] is None:
                self._json(503, {"error": "auth not configured"})
                return False
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Basic "):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="weather-api"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            try:
                u, p = base64.b64decode(hdr[6:]).decode().split(":", 1)
            except Exception:  # noqa: BLE001
                u = p = ""
            ok = hmac.compare_digest(u, auth[0]) and hmac.compare_digest(p, auth[1])
            if not ok:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="weather-api"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            path, q = u.path.rstrip("/") or "/", parse_qs(u.query)
            now = int(time.time())

            def qint(name, default):
                try:
                    return int(q.get(name, [default])[0])
                except (ValueError, TypeError):
                    return default

            if path == "/health":
                try:
                    con = db_connect(db)
                    last = con.execute("SELECT MAX(ts) FROM weather").fetchone()[0]
                    con.close()
                    self._json(200, {"status": "ok", "service": "weather-api",
                                     "now": now, "last_ok": last,
                                     "age_s": now - last if last else None})
                except Exception as e:  # noqa: BLE001
                    self._json(503, {"status": "fail", "error": str(e)})
                return
            if not self._authed():
                return
            con = None
            try:
                con = db_connect(db)
                if path == "/now":
                    self._json(200, now_payload(con, now))
                elif path == "/history":
                    to = qint("to", now)
                    frm = qint("from", to - 86400)
                    lim = min(qint("limit", MAX_LIMIT), MAX_LIMIT)
                    cur = con.execute("SELECT * FROM weather WHERE ts>=? AND ts<=? "
                                      "ORDER BY ts LIMIT ?", (frm, to, lim))
                    self._json(200, {"from": frm, "to": to,
                                     "rows": rows_to_dicts(cur)})
                elif path == "/hourly":
                    to = qint("to", now)
                    frm = qint("from", to - 7 * 86400)
                    cur = con.execute("SELECT * FROM v_hourly WHERE hour_epoch>=? "
                                      "AND hour_epoch<=? ORDER BY hour_epoch", (frm, to))
                    self._json(200, rows_to_dicts(cur))
                elif path == "/daily":
                    to = qint("to", now)
                    frm = qint("from", to - 62 * 86400)
                    cur = con.execute("SELECT * FROM v_daily WHERE day_epoch>=? "
                                      "AND day_epoch<=? ORDER BY day_epoch", (frm, to))
                    self._json(200, rows_to_dicts(cur))
                elif path == "/events":
                    et = q.get("type", [None])[0]
                    lim = min(qint("limit", 100), 1000)
                    if et:
                        cur = con.execute("SELECT * FROM events WHERE event_type=? "
                                          "ORDER BY ts_start DESC LIMIT ?", (et, lim))
                    else:
                        cur = con.execute("SELECT * FROM events "
                                          "ORDER BY ts_start DESC LIMIT ?", (lim,))
                    self._json(200, rows_to_dicts(cur))
                elif path == "/forecast":
                    lim = min(qint("limit", 24), 200)
                    cur = con.execute("SELECT * FROM forecast ORDER BY issued_at DESC, "
                                      "target_ts LIMIT ?", (lim,))
                    self._json(200, rows_to_dicts(cur))
                elif path == "/csv":
                    kind = q.get("kind", ["raw"])[0]
                    to = qint("to", now)
                    frm = qint("from", to - 86400)
                    if kind == "raw":
                        cur = con.execute("SELECT * FROM weather WHERE ts>=? AND ts<=? "
                                          "ORDER BY ts", (frm, to))
                    elif kind == "hourly":
                        cur = con.execute("SELECT * FROM v_hourly WHERE hour_epoch>=? "
                                          "AND hour_epoch<=? ORDER BY hour_epoch", (frm, to))
                    elif kind == "daily":
                        cur = con.execute("SELECT * FROM v_daily WHERE day_epoch>=? "
                                          "AND day_epoch<=? ORDER BY day_epoch", (frm, to))
                    else:
                        self._json(400, {"error": "kind=raw|hourly|daily"})
                        return
                    self._csv(cur)
                elif path == "/":
                    self._json(200, {"service": "weather-api", "version": "1.0",
                                     "endpoints": ["/health", "/now", "/history", "/hourly",
                                                   "/daily", "/events", "/forecast", "/csv"],
                                     "auth": "basic (кроме /health)"})
                else:
                    self._json(404, {"error": "unknown endpoint"})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
            finally:
                if con:
                    con.close()

    return H


def main():
    db = DEFAULT_DB
    port = 8090
    args = sys.argv[1:]
    if "--db" in args:
        db = args[args.index("--db") + 1]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    auth = load_auth()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(db, auth))
    log(f"weather-api: слушаю 0.0.0.0:{port}, db={db}, auth={'есть' if auth[0] else 'НЕТ'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
