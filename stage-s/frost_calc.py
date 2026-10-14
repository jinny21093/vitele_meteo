#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frost_calc.py — ночной заморозочный дежурный (v1.0.0, stdlib-only).

Режимы (systemd-шаблон weather-frost@<mode>.service):
  --evening   21:30  первичная оценка предстоящей ночи (методы A/B/A2)
  --midnight  00:10  контроль: расхождение факт-vs-модель, уточнение вердикта
  --verify    09:30  факт прошедшей ночи -> frost_log.csv (обучение),
                     Kuma обратно в green, TG-итог если риск объявлялся

Источники:
  станция : /home/auditbot/weather-dash/weather.db (sqlite, read-only)
  модель  : Open-Meteo, curl --resolve api.open-meteo.com:443:5.9.98.12
            (прямой egress на api.open-meteo.com с vitele закрыт, проверено 14.10)
  алерты  : Kuma push (frost_push.conf, ключ frost_url) +
            Telegram через relay (report.env: TELEGRAM_API_BASE/BOT_TOKEN/CHAT_ID)

Артефакты: frost_state.json, frost_log.csv (рядом со скриптом)

Калибровка на 14.10.2026 (ночи 07->08, 11->12, 12->13, 13->14.10):
  радиационный bias  -2.9 (станция минус OM; точки -3.1 и -2.7)
  облачный bias      -0.6 (точки -0.2 и -1.0)
  метод B: Teq = Td-2, tau = 2.5 ч, минимум ~ восход - 2.4 ч
  правило сценария: расхождение факт-модель <= -0.5 -> холодный (A+B),
                    >= +0.5 -> облачный (A2), иначе по облачности/ветру OM
"""
import argparse
import csv
import fcntl
import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "weather.db")
STATE_F = os.path.join(BASE, "frost_state.json")
LOG_F = os.path.join(BASE, "frost_log.csv")
KUMA_CONF = os.path.join(BASE, "frost_push.conf")
ENV_F = os.path.join(BASE, "report.env")

LAT, LON = 61.191654, 32.395859
OM_HOST, OM_PIN = "api.open-meteo.com", "5.9.98.12"

BIAS_RAD, BIAS_CLD = -2.9, -0.6
TAU_B, TEQ_OFF, MIN_BEFORE_SUNRISE = 2.5, 2.0, 2.4
SURFACE_OFF = 2.5
VERSION = "1.0.1"

LOG_HEADER = ["date", "label", "pred_evening", "pred_midnight", "pred_used",
              "risk", "om_tmin", "fact_tmin", "fact_at", "bias_fact_pred",
              "bias_fact_om", "wind_night", "cloud_night", "note"]


def say(s=""):
    print(s)


def now_msk():
    return datetime.now(MSK)


def dt_str(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------- станция ---

def db_connect():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=10)
    return con


def st_last(con):
    r = con.execute(
        "SELECT ts,outdoor_temp_c,outdoor_hum_pct,dew_point_c,"
        "pressure_rel_mmhg,wind_avg2_ms FROM weather "
        "WHERE outdoor_temp_c IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
    if not r:
        raise RuntimeError("weather.db пуст")
    return {"ts": r[0], "T": r[1], "RH": r[2], "Td": r[3], "P": r[4], "w": r[5]}


def st_avg(con, t0, t1):
    """Средние T/Td/ветер/давление в окне [t0,t1]."""
    rows = con.execute(
        "SELECT outdoor_temp_c,dew_point_c,wind_avg2_ms,pressure_rel_mmhg "
        "FROM weather WHERE ts BETWEEN ? AND ? AND outdoor_temp_c IS NOT NULL",
        (int(t0), int(t1))).fetchall()
    if not rows:
        return None
    def av(i, nd=1):
        vals = [r[i] for r in rows if r[i] is not None]
        return round(sum(vals) / len(vals), nd) if vals else None
    return {"T": av(0), "Td": av(1), "w": av(2, 2), "P": av(3), "n": len(rows)}


def st_night_fact(con, t0, t1):
    """Факт ночи: Tmin/Tmax + время, RH/Td на минимуме, ветер, пересечения 0."""
    rows = con.execute(
        "SELECT ts,outdoor_temp_c,outdoor_hum_pct,dew_point_c,wind_avg2_ms "
        "FROM weather WHERE ts BETWEEN ? AND ? AND outdoor_temp_c IS NOT NULL "
        "ORDER BY ts", (int(t0), int(t1))).fetchall()
    if not rows:
        return None
    lo = min(rows, key=lambda x: x[1])
    zeros = [x for x in rows if x[1] < 0]
    wv = [x[4] for x in rows if x[4] is not None]
    return {
        "tmin": lo[1], "tmin_at": datetime.fromtimestamp(lo[0], MSK).strftime("%H:%M"),
        "tmin_rh": lo[2], "tmin_td": lo[3],
        "tmax": max(x[1] for x in rows),
        "first_below0": datetime.fromtimestamp(zeros[0][0], MSK).strftime("%H:%M") if zeros else None,
        "last_below0": datetime.fromtimestamp(zeros[-1][0], MSK).strftime("%H:%M") if zeros else None,
        "wind_night": round(sum(wv) / len(wv), 2) if wv else None,
        "n": len(rows),
    }


# ------------------------------------------------------------------- OM -----

def om_fetch():
    """Свежий прогон OM через curl --resolve (pin 5.9.98.12)."""
    import urllib.parse
    params = {
        "latitude": LAT, "longitude": LON,
        "hourly": "temperature_2m,dew_point_2m,relative_humidity_2m,"
                  "cloud_cover,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset",
        "timezone": "Europe/Moscow", "past_days": 1, "forecast_days": 2,
        "wind_speed_unit": "ms",
    }
    url = "https://%s/v1/forecast?%s" % (OM_HOST, urllib.parse.urlencode(params))
    cmd = ["curl", "-4", "-s", "--max-time", "30",
           "--resolve", "%s:443:%s" % (OM_HOST, OM_PIN), url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError("OM curl fail rc=%s %s" % (p.returncode, p.stderr[:120]))
    js = json.loads(p.stdout)
    ho, da = js["hourly"], js["daily"]
    series = []
    for i, t in enumerate(ho["time"]):
        series.append({
            "t": t, "T": ho["temperature_2m"][i], "Td": ho["dew_point_2m"][i],
            "RH": ho["relative_humidity_2m"][i], "cloud": ho["cloud_cover"][i],
            "w": ho["wind_speed_10m"][i],
        })
    return {"series": series, "daily": da}


def om_window(om, t_start, t_end):
    return [h for h in om["series"] if t_start <= h["t"] < t_end
            and h["T"] is not None]


def om_daily(om, date_str, key):
    if date_str in om["daily"]["time"]:
        i = om["daily"]["time"].index(date_str)
        return om["daily"][key][i]
    return None


# ---------------------------------------------------------------- алерты ----

def load_env(path):
    cfg = {}
    if not os.path.exists(path):
        return cfg
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def kuma_push(status, msg, dry=False):
    """Push в Kuma (frost_url из frost_push.conf). True = ok."""
    url = None
    try:
        with open(KUMA_CONF, encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("frost_url="):
                    url = ln.split("=", 1)[1].strip()
                    break
    except OSError:
        pass
    if not url:
        say("[KUMA] frost_push.conf не найден/пуст — push пропущен")
        return False
    if dry:
        say("[KUMA][dry] status=%s msg=%s" % (status, msg))
        return True
    try:
        p = subprocess.run(
            ["curl", "-s", "-G", "--max-time", "8", url,
             "--data-urlencode", "status=" + status,
             "--data-urlencode", "msg=" + msg],
            capture_output=True, text=True, timeout=15)
        ok = '"ok":true' in p.stdout
        say("[KUMA] %s (%s)" % ("ok" if ok else "FAIL", p.stdout[:80]))
        return ok
    except Exception as e:
        say("[KUMA] исключение: %s" % e)
        return False


def tg_send(text, dry=False):
    """Telegram через relay (report.env). Возвращает True при ok:true."""
    cfg = load_env(ENV_F)
    base = cfg.get("TELEGRAM_API_BASE") or "https://api.telegram.org"
    token = cfg.get("TELEGRAM_BOT_TOKEN")
    chat = cfg.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        say("[TG] нет TELEGRAM_BOT_TOKEN/CHAT_ID в report.env — пропуск")
        return False
    if dry:
        say("[TG][dry] %s" % text)
        return True
    url = base.rstrip("/") + "/bot" + token + "/sendMessage"
    payload = json.dumps({"chat_id": chat, "text": text})
    try:
        p = subprocess.run(
            ["curl", "-4", "-s", "--max-time", "15", "-X", "POST", url,
             "-H", "Content-Type: application/json", "--data", payload],
            capture_output=True, text=True, timeout=25)
        ok = '"ok":true' in p.stdout
        say("[TG] %s" % ("ok" if ok else "FAIL: " + p.stdout[:100]))
        return ok
    except Exception as e:
        say("[TG] исключение: %s" % e)
        return False


# ---------------------------------------------------------------- оценка ----

def evening_anchor(st):
    """Какую ночь оценивает evening.
    >= 12:00 -> ближайшая ночь (сегодня->завтра, production 21:30).
    06..11   -> ближайшая ночь (ручные/тестовые прогоны).
    00..05   -> идущая ночь (догон по Persistent): если state покрывает её
                - берём из state, иначе вчера->сегодня (спас-прогон)."""
    now = now_msk()
    today = now.strftime("%Y-%m-%d")
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if now.hour >= 6:
        return today
    if st and st.get("night", {}).get("start_date") == yday:
        return yday
    return yday


def risk_class(pred):
    if pred is None:
        return "none"
    if pred <= -2.0:
        return "hard"      # сильный заморозок
    if pred <= 0.0:
        return "frost"     # заморозок
    if pred <= 1.5:
        return "risk"      # риск (минус возможен)
    return "none"


RISK_TXT = {"hard": "СИЛЬНЫЙ ЗАМОРОЗОК", "frost": "ЗАМОРОЗОК",
            "risk": "риск заморозка", "none": "без заморозка"}


def assess(con, om, start_date):
    """Оценка ночи start_date -> start_date+1 (окно 18:00 -> 10:00).
    Возвращает dict с методами, сценарием, вердиктом."""
    now = now_msk()
    t0 = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=MSK) + timedelta(hours=18)
    t1 = t0 + timedelta(hours=16)  # -> 10:00 следующих суток
    win = om_window(om, dt_str(t0), dt_str(t1))
    # окно от текущего часа (не переоценивать прошедшие часы)
    now_h = now.strftime("%Y-%m-%dT%H:00")
    win_rest = [h for h in win if h["t"] >= now_h]
    if not win_rest:
        win_rest = win[-8:]
    om_tmin = min(h["T"] for h in win_rest)
    om_tmin_at = next(h["t"] for h in win_rest if h["T"] == om_tmin)
    # текущий час OM
    om_now = None
    for h in om["series"]:
        if h["t"] <= now_h and h["T"] is not None:
            om_now = h
    st = st_avg(con, now.timestamp() - 1800, now.timestamp())
    T_now = st["T"] if st and st["T"] is not None else st_last(con)["T"]
    divergence = round(T_now - om_now["T"], 1) if om_now else None
    # облачность/ветер ночи (22:00 -> 05:00) по OM
    cl = [h["cloud"] for h in win
          if h["cloud"] is not None and h["t"][11:13] in
          ("22", "23", "00", "01", "02", "03", "04")]
    wn = [h["w"] for h in win
          if h["w"] is not None and h["t"][11:13] in
          ("22", "23", "00", "01", "02", "03", "04", "05")]
    cloud_night = round(sum(cl) / len(cl), 0) if cl else None
    wind_night = round(sum(wn) / len(wn), 2) if wn else None

    rad_cond = (cloud_night is not None and wind_night is not None
                and cloud_night <= 25 and wind_night <= 1.2)
    cld_cond = ((cloud_night is not None and cloud_night >= 55)
                or (wind_night is not None and wind_night >= 2.0))
    # правило 00:00 (расхождение <= -0.5 -> холодный) калибровано на ясной ночи;
    # при высокой облачности расхождение говорит о тумане/адвекции, а не о
    # радиационном выхолаживании — гейтим облачностью (v1.0.1, ночь 13->14.10)
    if (divergence is not None and divergence <= -0.5
            and (cloud_night is None or cloud_night <= 50)):
        scenario = "cold"
    elif divergence is not None and divergence >= 0.5:
        scenario = "cloudy"
    elif rad_cond:
        scenario = "cold"
    elif cld_cond:
        scenario = "cloudy"
    else:
        scenario = "mixed"

    methods = {}
    methods["A"] = round(om_tmin + BIAS_RAD, 1)
    methods["A2"] = round(om_tmin + BIAS_CLD, 1)
    # B: экспоненциальное затухание от текущего T к Teq=Td-2
    sunrise = om_daily(om, (t0 + timedelta(days=1)).strftime("%Y-%m-%d"), "sunrise")
    b_ok = False
    if st and st["Td"] is not None and sunrise:
        teq = st["Td"] - TEQ_OFF
        sr = datetime.strptime(sunrise, "%Y-%m-%dT%H:%M").replace(tzinfo=MSK)
        t_min = sr - timedelta(hours=MIN_BEFORE_SUNRISE)
        dt_h = (t_min - now).total_seconds() / 3600.0
        if dt_h > 0.2:
            methods["B"] = round(teq + (T_now - teq) * math.exp(-dt_h / TAU_B), 1)
            b_ok = True
    if scenario == "cold":
        # B (Teq=Td-2) — радиационная модель; при плотной облачности
        # равновесие выше — не включаем (v1.0.1)
        b_act = b_ok and (cloud_night is None or cloud_night <= 40)
        active = ["A", "B"] if b_act else ["A"]
    elif scenario == "cloudy":
        active = ["A2"]
    else:
        active = ["A", "A2", "B"] if b_ok else ["A", "A2"]
    vals = [methods[m] for m in active]
    pred = round(sum(vals) / len(vals), 1)
    corridor = [round(min(vals) - 0.3, 1), round(max(vals) + 0.3, 1)]
    tmin_at = om_tmin_at[11:16]
    return {
        "start_date": start_date,
        "night_label": "%s->%s" % (
            t0.strftime("%d.%m"), (t0 + timedelta(days=1)).strftime("%d.%m")),
        "station": {"T": T_now, "Td": st["Td"] if st else None,
                    "w": st["w"] if st else None},
        "om": {"tmin": om_tmin, "tmin_at": om_tmin_at[11:16],
               "om_now_T": om_now["T"] if om_now else None,
               "cloud_night": cloud_night, "wind_night": wind_night,
               "sunrise": sunrise},
        "divergence": divergence, "scenario": scenario,
        "methods": methods, "active": active, "pred": pred,
        "corridor": corridor, "tmin_at": tmin_at,
        "surface": round(pred - SURFACE_OFF, 1),
        "risk": risk_class(pred),
    }


def verdict_text(a, prefix=""):
    return ("[FROST]%s ночь %s: %s, Tmin воздуха ~%+.1f° (коридор %+.1f..%+.1f), "
            "минимум ~%s, поверхность ~%+.1f°. Ветер ~%.1f м/с, облачность "
            "ночью ~%.0f%%." % (
                prefix, a["night_label"], RISK_TXT[a["risk"]], a["pred"],
                a["corridor"][0], a["corridor"][1], a["tmin_at"],
                a["surface"], a["om"]["wind_night"] or 0,
                a["om"]["cloud_night"] or 0))


# ------------------------------------------------------- state / журнал -----

def load_state():
    try:
        with open(STATE_F, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(st):
    tmp = STATE_F + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_F)


def log_append(row):
    new = not os.path.exists(LOG_F)
    with open(LOG_F, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_HEADER)
        w.writerow([row.get(k, "") for k in LOG_HEADER])


# ------------------------------------------------------------------ режимы --

def run_evening(dry):
    con = db_connect()
    om = om_fetch()
    now = now_msk()
    start_date = evening_anchor(load_state())
    a = assess(con, om, start_date)
    say("=== РЕЖИМ EVENING %s | ночь %s | %s ===" % (
        VERSION, a["night_label"], now.strftime("%d.%m %H:%M")))
    say("станция: T %+.1f Td %+.1f ветер %.2f | OM сейчас %+.1f -> расхождение %s" % (
        a["station"]["T"], a["station"]["Td"] or -99, a["station"]["w"] or -1,
        a["om"]["om_now_T"] if a["om"]["om_now_T"] is not None else -99,
        a["divergence"]))
    say("OM: Tmin %+.1f @%s, ночь: обл %s%% ветер %s м/с, восход %s" % (
        a["om"]["tmin"], a["om"]["tmin_at"], a["om"]["cloud_night"],
        a["om"]["wind_night"], a["om"]["sunrise"]))
    say("сценарий: %s | методы %s | активные %s" % (
        a["scenario"], a["methods"], "+".join(a["active"])))
    say("ВЕРДИКТ: %s, Tmin %+.1f (%+.1f..%+.1f) @~%s, поверхность %+.1f" % (
        RISK_TXT[a["risk"]], a["pred"], a["corridor"][0], a["corridor"][1],
        a["tmin_at"], a["surface"]))
    st = {"version": VERSION, "mode": "evening",
          "ts": now.strftime("%Y-%m-%d %H:%M"), "night": a,
          "pred_evening": a["pred"], "pred_midnight": None,
          "pred_used": a["pred"], "risk": a["risk"]}
    save_state(st)
    if a["risk"] == "none":
        kuma_push("up", "нет заморозка (%s): Tmin>+%s" % (
            a["night_label"], a["pred"]), dry)
    else:
        kuma_push("down", verdict_text(a).replace("[FROST] ", ""), dry)
        tg_send(verdict_text(a), dry)
    con.close()
    return 0


def run_midnight(dry):
    st = load_state()
    if not st or not st.get("night"):
        say("[X] нет frost_state.json от evening — midnight требует evening-прогон")
        return 1
    start_date = st["night"]["start_date"]
    con = db_connect()
    om = om_fetch()
    a = assess(con, om, start_date)
    say("=== РЕЖИМ MIDNIGHT %s | ночь %s | %s ===" % (
        VERSION, a["night_label"], now_msk().strftime("%d.%m %H:%M")))
    say("расхождение факт-модель: %s (правило: <=-0.5 холодный, >=+0.5 облачный)" %
        a["divergence"])
    say("сценарий: %s | активные %s | методы %s" % (
        a["scenario"], "+".join(a["active"]), a["methods"]))
    say("ВЕРДИКТ: %s, Tmin %+.1f (%+.1f..%+.1f) @~%s (вечером было %+.1f)" % (
        RISK_TXT[a["risk"]], a["pred"], a["corridor"][0], a["corridor"][1],
        a["tmin_at"], st["pred_evening"]))
    changed = (a["risk"] != st["risk"]) or (abs(a["pred"] - st["pred_evening"]) >= 1.0)
    st["mode"] = "midnight"
    st["night"] = a
    st["pred_midnight"] = a["pred"]
    st["pred_used"] = a["pred"]
    st["risk"] = a["risk"]
    st["ts"] = now_msk().strftime("%Y-%m-%d %H:%M")
    save_state(st)
    if a["risk"] == "none":
        kuma_push("up", "нет заморозка (%s): Tmin>%+.1f" % (a["night_label"], a["pred"]), dry)
    else:
        kuma_push("down", verdict_text(a, " уточнение").replace("[FROST] ", ""), dry)
    if changed and not dry:
        tg_send(verdict_text(a, " обновление") +
                " Вечерняя оценка: %+.1f." % st["pred_evening"])
    elif changed:
        say("[TG][dry] изменилось -> отправили бы обновление")
    con.close()
    return 0


def run_verify(dry):
    con = db_connect()
    now = now_msk()
    st = load_state()
    start_date = None
    if st and st.get("night", {}).get("start_date"):
        start_date = st["night"]["start_date"]
    else:
        start_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    t0 = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=MSK) + timedelta(hours=18)
    t1 = t0 + timedelta(hours=16)
    fact = st_night_fact(con, t0.timestamp(), t1.timestamp())
    if not fact:
        say("[X] нет данных станции за ночь %s" % start_date)
        return 2
    label = "%s->%s" % (t0.strftime("%d.%m"), (t0 + timedelta(days=1)).strftime("%d.%m"))
    pred = st.get("pred_used") if st else None
    om_tmin = st.get("night", {}).get("om", {}).get("tmin") if st else None
    was_risk = st.get("risk") in ("hard", "frost", "risk") if st else False
    say("=== РЕЖИМ VERIFY %s | ночь %s | %s ===" % (
        VERSION, label, now.strftime("%d.%m %H:%M")))
    say("факт: Tmin %+.1f @%s (RH %s%%, Td %+.1f), ветер ночь %.2f м/с, "
        "ниже 0: %s..%s" % (
            fact["tmin"], fact["tmin_at"], fact["tmin_rh"], fact["tmin_td"] or -99,
            fact["wind_night"] or -1, fact["first_below0"], fact["last_below0"]))
    if pred is not None:
        say("прогноз: %+.1f -> ошибка %+.1f° (OM был %s -> bias станции-vs-OM %+.1f)" % (
            pred, fact["tmin"] - pred,
            ("%+.1f" % om_tmin) if om_tmin is not None else "-",
            (fact["tmin"] - om_tmin) if om_tmin is not None else 0))
    note = "ok" if st else "нет state (прогон вечеринки пропущен?)"
    log_append({
        "date": start_date, "label": label,
        "pred_evening": st.get("pred_evening", "") if st else "",
        "pred_midnight": st.get("pred_midnight", "") if st else "",
        "pred_used": pred if pred is not None else "",
        "risk": st.get("risk", "") if st else "",
        "om_tmin": om_tmin if om_tmin is not None else "",
        "fact_tmin": fact["tmin"], "fact_at": fact["tmin_at"],
        "bias_fact_pred": round(fact["tmin"] - pred, 1) if pred is not None else "",
        "bias_fact_om": round(fact["tmin"] - om_tmin, 1) if om_tmin is not None else "",
        "wind_night": fact["wind_night"], "cloud_night": "", "note": note,
    })
    say("[LOG] строка добавлена в frost_log.csv (%s)" % note)
    kuma_push("up", "ночь %s: факт Tmin %+.1f @%s (прогноз %s)" % (
        label, fact["tmin"], fact["tmin_at"],
        ("%+.1f" % pred) if pred is not None else "n/a"), dry)
    if was_risk and not dry:
        msg = ("[FROST] ночь %s: факт Tmin %+.1f° @ %s (прогноз %+.1f, ошибка "
               "%+.1f°). Поверхность держала ~%+.1f°." % (
                   label, fact["tmin"], fact["tmin_at"], pred,
                   fact["tmin"] - pred, fact["tmin"] - SURFACE_OFF))
        tg_send(msg)
    elif was_risk:
        say("[TG][dry] был риск -> отправили бы итог")
    con.close()
    return 0


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser(description="ночной заморозочный дежурный")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--evening", action="store_true")
    g.add_argument("--midnight", action="store_true")
    g.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="без Kuma/Telegram (печать вердикта)")
    args = ap.parse_args()

    lock_path = os.path.join(BASE, ".frost.lock")
    try:
        lf = open(lock_path, "w")
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        say("[!] другой экземпляр frost_calc уже работает — выход")
        return 0

    try:
        if args.evening:
            rc = run_evening(args.dry_run)
        elif args.midnight:
            rc = run_midnight(args.dry_run)
        else:
            rc = run_verify(args.dry_run)
    except Exception as e:
        say("[X] %s" % e)
        rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
