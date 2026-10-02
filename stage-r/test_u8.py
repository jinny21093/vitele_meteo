#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_u8.py — приёмочные тесты U8 (спека weather-report v1.1.1 §7).

Запуск:  python3 test_u8.py [-v]
Состав:
  TestNumbersUnit  — инварианты (d_epoch, кириллица, деградация фактов,
                     fallback) + пункты системного промпта дословно;
                     юнит-пары §7.1 удалены вместе с чекером (v1.2.1/U8.4,
                     литературный нарратив — invalid_numbers не ставится);
  U8ScenarioTests  — сценарии §7.2 (мок-LLM/мок-Telegram/мок-Kuma на
                     127.0.0.1, БД-фикстуры в tmp); нумерация = сценарии
                     спеки, пропуски 6/20/26 — удалённые тесты
                     invalid_numbers-ветки (U8.4) + смоуки §7.3
                     (test_s1…test_s5; бюджет Z.ai — ручной после деплоя)
                     + test_s6 (U8.4: округлённый нарратив проходит).

Секреты: только тестовые значения (фейковый токен/ключ), в лог не попадают.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REPORT = os.path.join(HERE, "weather_report.py")
MIGRATION = os.path.join(REPO, "docs", "migrations", "004_reports.sql")

sys.path.insert(0, HERE)
import u8_mocks  # noqa: E402

TZ = 10800
PINNED_NOW = 1790881045                      # 2026-10-01 ~22:37 MSK (детерминизм)
D = ((PINNED_NOW + TZ) // 86400 - 1) * 86400 - TZ   # 1790715600 = 30.09.2026
assert D % 86400 == 75600

MODULE_LOGS = []          # весь stdout/stderr прогонов (тесты 14 / s4)
SECRET_GREP = re.compile(r"(?i)api[-]?key|bot[-]?token")

PREV_T = [6.9, 6.5, 7.2, 5.8, 8.0, 7.1, 6.6]   # D-1..D-7 t_out_avg
PREV_R = [1.8, 0.0, 0.5, 3.2, 0.0, 1.1, 2.0]   # D-1..D-7 rain_mm

V_DAILY_COLS = ("day_epoch,t_out_min,t_out_max,t_out_avg,t_out_min_time,"
                "t_out_max_time,t_out_amp,p_min,p_max,p_amp,wind_avg,wind_max,"
                "gust_max,wind_run_km,wind_dir_mode,rain_mm,rain_hours,"
                "rain_max_rate,solar_sum_wh_m2,uvi_max,sun_hours,gdd_day,"
                "frost_flag,hard_freeze_flag,fog_flag,thunder_flag,"
                "degree_days_heat,degree_days_cool,n_samples")
V_DAILY_DDL = ("CREATE TABLE v_daily ("
               + ",".join(f"{c} {'INTEGER' if c in ('day_epoch','t_out_min_time','t_out_max_time','rain_hours','frost_flag','hard_freeze_flag','fog_flag','thunder_flag','n_samples') else ('TEXT' if c == 'wind_dir_mode' else 'REAL')}"
                          for c in V_DAILY_COLS.split(",")) + ")")
EVENTS_DDL = ("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "ts_start INTEGER NOT NULL, ts_end INTEGER, event_type TEXT NOT NULL, "
              "severity TEXT, value REAL, context TEXT, acknowledged INTEGER DEFAULT 0)")
WMETA_DDL = ("CREATE TABLE wmeta (key TEXT PRIMARY KEY, value TEXT, "
             "updated_at INTEGER)")


def insert_day(con, d, **over):
    vals = dict(t_out_min=3.8, t_out_max=12.1, t_out_avg=7.9,
                t_out_min_time=d + 8 * 3600 + 720, t_out_max_time=d + 15 * 3600 + 2400,
                t_out_amp=8.3, p_min=758.1, p_max=762.4, p_amp=4.3,
                wind_avg=1.4, wind_max=3.6, gust_max=5.8, wind_run_km=121.0,
                wind_dir_mode="WSW", rain_mm=0.0, rain_hours=0, rain_max_rate=0.0,
                solar_sum_wh_m2=1240.0, uvi_max=2.1, sun_hours=8.2, gdd_day=2.4,
                frost_flag=0, hard_freeze_flag=0, fog_flag=0, thunder_flag=0,
                degree_days_heat=10.1, degree_days_cool=0.0, n_samples=1440)
    vals.update(over)
    cols = ",".join(vals)
    ph = ",".join("?" * (len(vals) + 1))
    con.execute(f"INSERT INTO v_daily(day_epoch,{cols}) VALUES({ph})",
                (d, *vals.values()))


def build_db(path, *, with_d=True, d_over=None, prev_full=7, n_events=1,
             open_frost=False, broken_schema=False):
    if os.path.exists(path):
        os.remove(path)  # повторное построение той же фикстуры — с нуля
    con = sqlite3.connect(path)
    # schema_migrations существует в живой БД (создана этапом A); фиксстура
    # повторяет состояние: версии 2 и 3, затем применяется миграция 004
    con.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                "applied_at INTEGER, description TEXT)")
    con.execute("INSERT INTO schema_migrations VALUES(2, 1789505860, 'этап A')")
    con.execute("INSERT INTO schema_migrations VALUES(3, 1789544905, 'stage B')")
    with open(MIGRATION, encoding="utf-8") as f:
        con.executescript(f.read())
    ddl = V_DAILY_DDL
    if broken_schema:
        ddl = ddl.replace(",fog_flag INTEGER", "")
    con.execute(ddl)
    if broken_schema:
        # сломанная схема: только таблицы, без данных — валидация PRAGMA
        # обязана упасть раньше любого чтения (тест 16)
        con.commit()
        con.close()
        return
    con.execute(EVENTS_DDL)
    con.execute(WMETA_DDL)
    for i in range(7):
        dd = D - (i + 1) * 86400
        insert_day(con, dd, t_out_avg=PREV_T[i], rain_mm=PREV_R[i],
                   n_samples=1440 if i < prev_full else 100)
    if with_d:
        insert_day(con, D, **(d_over or {}))
    if open_frost:
        # тест 10: FROST открыт 23:00 D-1, закрыт 03:00 D
        con.execute("INSERT INTO events(ts_start,ts_end,event_type,severity,value,"
                    "context,acknowledged) VALUES(?,?,?,?,?,?,0)",
                    (D - 3600, D + 3 * 3600, "FROST", "high", -0.3, "{}"))
    for i in range(n_events - (1 if open_frost else 0)):
        sev = ["low", "high", "mid"][i % 3]
        con.execute("INSERT INTO events(ts_start,ts_end,event_type,severity,value,"
                    "context,acknowledged) VALUES(?,?,?,?,?,?,0)",
                    (D + 3600 + i * 600, D + 4500 + i * 600,
                     "STRONG_WIND", sev, 9.0 + i, "{}"))
    if n_events == 1 and not open_frost:
        con.execute("INSERT INTO events(ts_start,ts_end,event_type,severity,value,"
                    "context,acknowledged) VALUES(?,?,?,?,?,?,0)",
                    (D + 3600, D + 7200, "FROST", "high", -0.3, "{}"))
    con.commit()
    con.close()


def q(path, sql, params=()):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def w(path, sql, params=()):
    """Запись с явным commit (q() предназначен для SELECT — без commit
    DML откатывается при close, что маскирует правку фикстуры)."""
    con = sqlite3.connect(path)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


LLM, TG, KUMA = u8_mocks.start_all()


def control(port, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/control",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def stats(port):
    return control(port, {"action": "stats"})["stats"]


def reset_all():
    for port in (LLM.port, TG.port, KUMA.port):
        control(port, {"action": "reset"})


def make_env(db, kuma_conf):
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LLM_API_KEY": "zai-test-KEYVALUE",
        "LLM_BASE_URL": f"http://127.0.0.1:{LLM.port}",
        "LLM_MODEL": "glm-4.7-flash",
        "TELEGRAM_BOT_TOKEN": "88005553535:TEST-TOKEN",
        "TELEGRAM_CHAT_ID": "100500",
        "TELEGRAM_API_BASE": f"http://127.0.0.1:{TG.port}",
        "KUMA_CONF": kuma_conf,
        "WEATHER_REPORT_NOW": str(PINNED_NOW),
        "WEATHER_REPORT_RETRY_SCALE": "0",
        "LLM_TIMEOUT_S": "20",
        "TG_TIMEOUT_S": "10",
        "WEATHER_REPORT_DB": db,
    }


def run_report(args, env, timeout=180):
    p = subprocess.run([sys.executable, REPORT, *args], env=env,
                       capture_output=True, text=True, timeout=timeout, cwd=REPO)
    MODULE_LOGS.append(p.stdout + p.stderr)
    return p


class U8Base(unittest.TestCase):
    def setUp(self):
        reset_all()
        self.work = tempfile.mkdtemp(prefix="u8test-")
        self.db = os.path.join(self.work, "test.db")
        self.kuma_conf = os.path.join(self.work, "kuma_push.conf")
        with open(self.kuma_conf, "w") as f:
            f.write(f"backup_url=http://127.0.0.1:{KUMA.port}/push?token=KUMASECRET123\n")
        self.env = make_env(self.db, self.kuma_conf)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def gen(self, **build_kw):
        build_db(self.db, **build_kw)
        return run_report([], self.env)

    def rows(self):
        return q(self.db, "SELECT * FROM reports ORDER BY id")


class TestNumbersUnit(unittest.TestCase):
    """Инварианты + промпт-пункты дословно (без сети). Юнит-пары §7.1
    удалены вместе с чекером (U8.4); нумерация сохранена по истории."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, HERE)
        import weather_report as wr
        cls.wr = wr
        cls.facts = {
            "day_epoch": D, "day_partial": False, "n_samples": 1440,
            "temperature": {"t_out_min": 3.8, "t_out_max": 12.1, "t_out_avg": 7.9,
                            "t_delta": 8.3, "t_out_min_time": D + 29 * 60,
                            "t_out_max_time": D + 56 * 3600},
            "pressure": {"p_min": 758.1, "p_max": 762.4, "p_delta": 4.3},
            "wind": {"wind_avg": 1.4, "wind_max": 3.6, "gust_max": 5.8,
                     "wind_dir_mode": "WSW", "wind_run_km": 121.0},
            "rain": {"rain_mm": 0.0, "rain_hours": 0},
            "solar": {"solar_sum_wh_m2": 1240.0, "uvi_max": 2.1},
            "flags": {"frost_flag": 0, "hard_freeze_flag": 0, "fog_flag": 0,
                      "gdd_day": 2.4},
            "events": [{"event_type": "FROST", "severity": "high",
                        "ts_start": D + 3600, "ts_end": D + 7200,
                        "duration_s": 3600, "value": -0.3}],
            "events_total": 1, "events_truncated": False,
            "comparison": {"prev_day_epoch": D - 86400, "t_vs_prev": 1.0,
                           "rain_vs_prev": -1.8},
            "norm": {"norm_n": 7, "norm_t_avg": 6.87, "norm_rain_mm": 1.23},
        }

    def test_11_epoch_invariant(self):
        self.assertEqual(self.wr.d_epoch_of(PINNED_NOW), D)
        self.assertEqual(D % 86400, 75600)

    def test_12_cyr_share(self):
        self.assertGreaterEqual(self.wr.cyr_share("Привет, мир."), 0.7)
        self.assertLess(self.wr.cyr_share("hello world"), 0.7)
        self.assertLess(self.wr.cyr_share("Привет, temperature"), 0.7)

    def test_13_degrade_truncates_events(self):
        big = {"day_epoch": D, "n_samples": 1,
               "events": [{"value": float(i), "x": "y" * 30} for i in range(400)],
               "events_total": 400, "events_truncated": False}
        out = json.loads(self.wr.degrade_facts(big))
        self.assertEqual(out["events"], [])
        self.assertTrue(out["events_truncated"])
        self.assertEqual(out["events_total"], 400)
        self.assertLessEqual(self.wr.facts_json_bytes(out), 8192)

    def test_16_fallback_reason_backticks(self):
        # находка деплоя live (2026-10-01, прогон 2): `_` enum-значений ломает
        # legacy-Markdown живого Bot API (400 can't parse entities)
        msg = self.wr.fallback_message(self.facts, "provider_unreachable")
        self.assertTrue(msg.endswith("`provider_unreachable`"), msg)
        self.assertIn("*3.8*", msg, "MAJ-4: жирная шапка сохраняется")
        # U8.4: исторический вердикт invalid_numbers (из enum удалён) —
        # fallback обязан рендериться без assert, в тех же backticks
        legacy = self.wr.fallback_message(self.facts, "invalid_numbers")
        self.assertTrue(legacy.endswith("`invalid_numbers`"), legacy)

    def test_19_prompt_literary_verbatim(self):
        # U8.4: пункт владельца о литературном нарративе в промпте ДОСЛОВНО;
        # старые пункты о числах («копия из фактов», «не округляй», «минус
        # дословно») в промпте БОЛЬШЕ НЕ стоят; связка фактов сохранена
        target_lit = ('Ты — наблюдатель на метеостанции на даче в Видлице. '
                      'Пиши живой, литературный рассказ о погоде дня: вьюга, '
                      'ветер, дождь — образно и по-человечески. Числа '
                      'используй свободно: округляй ("почти на четыре '
                      'градуса", "около минус одного"), перефразируй ("к '
                      'ночи подморозило"). Направления и характер явлений '
                      'передавай словами, не таблицей. НО: не выдумывай '
                      'ЯВЛЕНИЙ, которых не было (не было дождя — не пишем '
                      'дождь; null — "данных нет"); место — только '
                      'Видлица; язык — русский.')
        target_synth = ("В конце 2–3 абзацев свяжи факты дня в картину: "
                        "разброс температур, изменение давления, события, "
                        "сравнение с нормой.")
        self.assertIn(target_lit, self.wr._SYSTEM_PROMPT_LINES)
        self.assertIn(target_synth, self.wr._SYSTEM_PROMPT_LINES)
        system = "\n".join(self.wr._SYSTEM_PROMPT_LINES)
        for gone in ("копия из фактов", "не округляй", "половина градуса",
                     "минусом дословно"):
            self.assertNotIn(gone, system,
                             f"старый пункт о числах ещё стоит: {gone}")
        self.assertEqual(self.wr.build_messages(
            {"day_label": "30.09.2026", "day_epoch": D, "n_samples": 1440}
        )[0]["content"].count(target_lit), 1)

    def test_20_prompt_location_verbatim(self):
        # U8.2: пункт локации присутствует в системном промпте ДОСЛОВНО
        # (образец — test_19; решение владельца: «на даче в Видлице»).
        # Чекер чисел словами НЕ расширяется (решение ревьюера):
        # галлюцинацию локации ловим промптом, наблюдаем штатные 06:50.
        target_loc = ("Место: дача в Видлице (частная метеостанция). "
                      "Пиши \"на даче в Видлице\" или \"в Видлице\"; "
                      "никаких городов и регионов, кроме Видлицы; Москву "
                      "не упоминай никогда.")
        self.assertIn(target_loc, self.wr._SYSTEM_PROMPT_LINES)
        system = "\n".join(self.wr._SYSTEM_PROMPT_LINES)
        self.assertEqual(system.count("Москв"), 1,
                         "корень «Москв-» в промпте — только в запрете")
        self.assertEqual(self.wr.build_messages(
            {"day_label": "30.09.2026", "day_epoch": D, "n_samples": 1440}
        )[0]["content"].count(target_loc), 1)


class U8ScenarioTests(U8Base):

    def test_01_normal_day(self):
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["llm_text"])
        self.assertIsNone(rows[0]["llm_error"])
        self.assertEqual(rows[0]["delivery_status"], "sent")
        self.assertEqual(rows[0]["llm_model"], "glm-4.7-flash")
        tg = stats(TG.port)
        self.assertEqual(tg["requests"], 2, "шапка (Markdown) + текст плейном")
        self.assertEqual(tg["bodies"][0]["body"].get("parse_mode"), "Markdown")
        self.assertNotIn("parse_mode", tg["bodies"][1]["body"])
        self.assertEqual(tg["bodies"][1]["body"]["text"], rows[0]["llm_text"])
        self.assertTrue(tg["bodies"][0]["body"]["text"].startswith("Погода за 30.09:"))
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 1,
                         "U8.3: успешная доставка — ровно один ok-push")
        self.assertIn("status=up", kq[0])

    def test_02_partial_day(self):
        control(LLM.port, {"target": "llm", "content":
                "Днём до 12.1 °C, ночью 3.8 °C. Данные неполные: покрытие 400/1440."})
        p = self.gen(d_over={"n_samples": 400})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        facts = json.loads(self.rows()[0]["facts_json"])
        self.assertTrue(facts["day_partial"])
        self.assertIn("неполные", self.rows()[0]["llm_text"])
        self.assertEqual(self.rows()[0]["delivery_status"], "sent")

    def test_03_rain_null_no_claim(self):
        control(LLM.port, {"target": "llm", "content":
                "Данные о дожде недоступны. Температура была от 3.8 до 12.1 °C."})
        p = self.gen(d_over={"rain_mm": None, "rain_hours": None})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        text = self.rows()[0]["llm_text"]
        self.assertNotIn("дождя не было", text.lower())
        self.assertIn("недоступны", text)
        sysmsg = stats(LLM.port)["bodies"][0]["messages"][0]["content"]
        self.assertIn("данные недоступны", sysmsg, "правило NULL≠0 в промпте")
        self.assertEqual(self.rows()[0]["delivery_status"], "sent")

    def test_04_llm_timeout(self):
        control(LLM.port, {"target": "llm", "sleep_s": 2.0})
        env = dict(self.env, LLM_TIMEOUT_S="0.5")
        build_db(self.db)
        p = run_report([], env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertIsNone(row["llm_text"])
        self.assertEqual(row["llm_error"], "timeout")
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(stats(LLM.port)["requests"], 3, "1+2 retry")
        tg = stats(TG.port)
        self.assertEqual(tg["requests"], 1)
        self.assertTrue(tg["bodies"][0]["body"]["text"].endswith(
            "Нарратив недоступен: `timeout`"))

    def test_05_wrong_language(self):
        control(LLM.port, {"target": "llm", "content":
                "Weather was quite pleasant today with mild temperatures."})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.rows()[0]["llm_error"], "wrong_language")
        self.assertIsNone(self.rows()[0]["llm_text"])
        self.assertEqual(self.rows()[0]["delivery_status"], "sent")
        tg = stats(TG.port)
        self.assertIn("Нарратив недоступен: `wrong_language`",
                      tg["bodies"][0]["body"]["text"])

    # test_06 (спека §7.2: invalid_numbers) УДАЛЁН — U8.4 снял чекер чисел

    def test_07_tg_down_pending_retry(self):
        control(TG.port, {"target": "tg", "fail_always": True})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(row["delivery_status"], "pending_retry")
        self.assertEqual(row["delivery_attempts"], 1)
        self.assertEqual(stats(TG.port)["requests"], 3, "in-run 3 попытки")
        self.assertEqual(stats(KUMA.port)["queries"], [])
        self.assertIsNotNone(row["llm_text"], "LLM-часть сохранена для досылки")

    def test_08_ttl4_failed_one_kuma(self):
        control(TG.port, {"target": "tg", "fail_always": True})
        build_db(self.db)
        for i in range(4):
            p = run_report([], self.env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(row["delivery_status"], "failed")
        self.assertEqual(row["delivery_attempts"], 4)
        self.assertEqual(stats(TG.port)["requests"], 12, "4 прогона × 3 попытки")
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 1, "ровно один Kuma push на переход")
        self.assertIn("status=down", kq[0])

    def test_09_double_restart_one_row(self):
        self.gen()
        p = run_report([], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.rows()), 1, "idempotency_key: без дублей")
        self.assertEqual(stats(TG.port)["requests"], 2, "одно сообщение (пара) всего")

    def test_10_frost_open_over_midnight(self):
        p = self.gen(n_events=2, open_frost=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        facts = json.loads(self.rows()[0]["facts_json"])
        frost = [e for e in facts["events"] if e["event_type"] == "FROST"
                 and e["ts_start"] == D - 3600]
        self.assertEqual(len(frost), 1, "открытое на D-1 событие попадает в отчёт D")
        self.assertEqual(frost[0]["duration_s"], 4 * 3600)

    def test_11_norm_lt3_absent(self):
        control(LLM.port, {"target": "llm", "content":
                "Днём до 12.1 °C, ночью 3.8 °C. Осадков не было: 0.0 мм."})
        p = self.gen(prev_full=1)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        facts = json.loads(self.rows()[0]["facts_json"])
        self.assertNotIn("norm", facts, "N<3 → блока norm нет целиком")
        self.assertNotIn("норм", self.rows()[0]["llm_text"].lower())
        self.assertEqual(self.rows()[0]["delivery_status"], "sent")

    def test_12_norm5_mentioned(self):
        control(LLM.port, {"target": "llm", "content":
                "Днём 12.1 °C, ночью 3.8 °C. Осадки 0.0 мм. Теплее, норма по 5 дням."})
        p = self.gen(prev_full=5)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        facts = json.loads(self.rows()[0]["facts_json"])
        self.assertEqual(facts["norm"]["norm_n"], 5)
        self.assertIn("норма по 5 дням", self.rows()[0]["llm_text"])
        self.assertEqual(self.rows()[0]["delivery_status"], "sent")

    def test_13_secrets_not_in_git(self):
        p = subprocess.run("git log -p | grep -iE 'api[-]?key|bot[-]?token' || true",
                           shell=True, capture_output=True, text=True, cwd=REPO)
        self.assertEqual(p.stdout.strip(), "", "секретов нет во всей истории git")

    def test_14_secrets_not_in_logs(self):
        self.assertTrue(MODULE_LOGS, "перед этой проверкой были прогоны")
        for i, chunk in enumerate(MODULE_LOGS):
            for line in chunk.splitlines():
                self.assertIsNone(SECRET_GREP.search(line),
                                  f"секрет-паттерн в логе прогона #{i}: {line}")

    def test_15_empty_day_two_wordings(self):
        # (а) строки v_daily нет
        p = self.gen(with_d=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(stats(TG.port)["bodies"][0]["body"]["text"],
                         "Отчёт за 30.09: данных нет")
        self.assertIsNone(row["llm_text"])
        self.assertIsNone(row["llm_error"])
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(stats(LLM.port)["requests"], 0, "LLM для пустого дня не зовётся")
        # (б) строка есть, n_samples=0
        control(TG.port, {"action": "reset"})
        control(LLM.port, {"action": "reset"})
        self.db = os.path.join(self.work, "test2.db")
        self.env = make_env(self.db, self.kuma_conf)
        p = self.gen(d_over={"n_samples": 0})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(stats(TG.port)["bodies"][0]["body"]["text"],
                         "Отчёт за 30.09: данных нет (0 замеров)")

    def test_16_schema_mismatch(self):
        build_db(self.db, broken_schema=True)
        p = run_report([], self.env)
        self.assertEqual(p.returncode, 1, "fail-fast: ERROR + exit 1")
        self.assertIn("ERROR схема v_daily", p.stdout)

    def test_17_finish_length_accepted(self):
        control(LLM.port, {"target": "llm", "finish": "length", "content":
                "Днём воздух прогрелся до 12.1 °C, ночью ожидаются 3.8 °C."})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertIsNotNone(row["llm_text"], "length: текст принимается")
        self.assertIsNone(row["llm_error"])
        self.assertEqual(row["delivery_status"], "sent")
        self.assertIn("WARN finish_reason=length", p.stdout)

    def test_18_resend_no_new_row(self):
        self.gen()
        before = len(self.rows())
        tg_before = stats(TG.port)["requests"]
        p = run_report(["--resend-last"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), before, "--resend не создаёт строку")
        self.assertEqual(rows[0]["delivery_status"], "sent")
        self.assertEqual(rows[0]["delivery_attempts"], 1, "TTL сброшен resend'ом")
        self.assertEqual(stats(TG.port)["requests"], tg_before + 2)

    def test_19_events_truncated_cap15(self):
        p = self.gen(n_events=17)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        facts = json.loads(self.rows()[0]["facts_json"])
        self.assertEqual(len(facts["events"]), 15)
        self.assertEqual(facts["events_total"], 17)
        self.assertTrue(facts["events_truncated"])
        self.assertEqual(facts["events"][0]["severity"], "high",
                         "сортировка severity DESC, ts_start ASC")

    # test_20 (спека §7.2: «5 м/с при факте 8» → invalid_numbers) УДАЛЁН —
    # U8.4 снял чекер чисел (директива: «20-й и связанные»)

    def test_21_paid_model_fail_fast(self):
        build_db(self.db)
        p = run_report([], dict(self.env, LLM_MODEL="glm-4.5"))
        self.assertEqual(p.returncode, 1, "вне whitelist → ERROR + exit 1")
        self.assertIn("model not in free whitelist", p.stdout)
        self.assertEqual(stats(LLM.port)["requests"], 0, "запроса к провайдеру не было")

    def test_22_canon_request_shape(self):
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = stats(LLM.port)["bodies"][0]
        self.assertEqual(body["model"], "glm-4.7-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"},
                         "thinking:disabled — константа в каждом запросе")
        self.assertEqual(body["temperature"], 0.3)
        self.assertEqual(body["max_tokens"], 800)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("только на русском", body["messages"][0]["content"])
        facts = json.loads(self.rows()[0]["facts_json"])
        self.assertIn(facts["day_label"], body["messages"][1]["content"])

    def test_23_model_swap_no_retry(self):
        control(LLM.port, {"target": "llm", "model_override": "glm-4.6"})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(row["llm_error"], "provider_unreachable")
        self.assertIsNone(row["llm_model"])
        self.assertEqual(stats(LLM.port)["requests"], 1, "без retry")
        self.assertEqual(row["delivery_status"], "sent")
        self.assertIn("Нарратив недоступен: `provider_unreachable`",
                      stats(TG.port)["bodies"][0]["body"]["text"])

    def test_24_http_402_no_retry(self):
        control(LLM.port, {"target": "llm", "http_status": 402, "fail_always": True})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(row["llm_error"], "provider_unreachable")
        self.assertEqual(stats(LLM.port)["requests"], 1, "402 — без retry")
        self.assertIn("LLM HTTP 402", p.stdout)

    def test_25_5xx_then_recover(self):
        control(LLM.port, {"target": "llm", "http_status": 500, "fail_times": 2})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(stats(LLM.port)["requests"], 3, "2×5xx + восстановление")
        self.assertIsNone(row["llm_error"])
        self.assertIsNotNone(row["llm_text"])
        self.assertEqual(row["delivery_status"], "sent")

    # test_26 (U8.1 дельта-пара: инверсия/модуль vs дословный минус)
    # УДАЛЁН — invalid_numbers больше не ставится (U8.4)

    def test_27_regenerate_poisoned_to_clean(self):
        # U8.1: отравленная строка → --regenerate-last → чистый нарратив;
        # строка ТА ЖЕ (новых нет), доставка отправлена. U8.4: отравление
        # wrong_language — invalid_numbers больше не ставится (чекер снят)
        control(LLM.port, {"target": "llm", "content":
                "Weather was quite nasty today with freezing rain overnight."})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertEqual(row["llm_error"], "wrong_language")
        self.assertIsNone(row["llm_text"])
        self.assertEqual(row["delivery_attempts"], 1)
        rid = row["id"]
        # регенерация: чистый ответ мока → LLM повторён по канону
        control(LLM.port, {"action": "reset"})
        control(TG.port, {"action": "reset"})
        p = run_report(["--regenerate-last"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 1, "--regenerate не создаёт строку")
        row = rows[0]
        self.assertEqual(row["id"], rid, "обновлена та же строка")
        self.assertIsNone(row["llm_error"], "вердикт сброшен и не вернулся")
        self.assertEqual(row["llm_text"],
                         "Днём воздух прогрелся до 12.1 °C, минимальная "
                         "температура опустилась до 3.8 °C. Осадков "
                         "не зафиксировано: 0.0 мм. Ветер к вечеру "
                         "усиливался до 5.8 м/с при среднем 1.4 м/с.")
        self.assertEqual(row["llm_model"], "glm-4.7-flash")
        self.assertIsNotNone(row["llm_tokens_in"])
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(row["delivery_attempts"], 1, "свежий круг TTL")
        self.assertEqual(stats(LLM.port)["requests"], 1, "ровно один LLM-вызов")
        tg = stats(TG.port)
        self.assertEqual(tg["requests"], 2, "шапка + чистый нарратив")
        self.assertEqual(tg["bodies"][1]["body"]["text"], row["llm_text"])
        self.assertIn("--regenerate-last", p.stdout)

    def test_28_regenerate_day_exit_codes_and_exclusivity(self):
        # U8.1: --regenerate без строки → exit 1; по дню → OK; флаги
        # взаимоисключающие → exit 2
        build_db(self.db)
        p = run_report(["--regenerate", str(D)], self.env)
        self.assertEqual(p.returncode, 1, "строки reports нет → exit 1")
        self.gen()
        tg_before = stats(TG.port)["requests"]
        p = run_report(["--regenerate", str(D)], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.rows()), 1, "новых строк нет")
        self.assertEqual(stats(TG.port)["requests"], tg_before + 2)
        for flags in (["--regenerate-last", "--resend-last"],
                      ["--regenerate", str(D), "--resend-last"],
                      ["--regenerate-last", "--resend", str(D)],
                      ["--regenerate", str(D), "--regenerate-last"]):
            p = run_report(flags, self.env)
            self.assertEqual(p.returncode, 2, f"эксклюзивность: {flags}")
            self.assertIn("взаимоисключающие", p.stderr)

    # --- U8.3: retry-прогон + ok-push Kuma ------------------------------------
    def test_29_retry_pending_redelivers_no_generation(self):
        # --retry-pending: досылка pending_retry из сохранённого llm_text
        # (шапка+нарратив), БЕЗ генерации нового дня и БЕЗ LLM; ровно +1
        # attempt за прогон; ok-push на переход в sent
        control(TG.port, {"target": "tg", "fail_always": True})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        rid = row["id"]
        self.assertEqual(row["delivery_status"], "pending_retry")
        self.assertEqual(row["delivery_attempts"], 1)
        self.assertIsNotNone(row["llm_text"])
        llm_after_gen = stats(LLM.port)["requests"]
        control(TG.port, {"action": "reset"})
        p = run_report(["--retry-pending"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--retry-pending: pending_retry строк: 1", p.stdout)
        rows = self.rows()
        self.assertEqual(len(rows), 1, "без генерации нового дня")
        row = rows[0]
        self.assertEqual(row["id"], rid, "дослана та же строка")
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(row["delivery_attempts"], 2,
                         "retry-прогон — тоже +1 attempt")
        self.assertEqual(stats(LLM.port)["requests"], llm_after_gen,
                         "LLM на досылке не вызывается")
        tg = stats(TG.port)
        self.assertEqual(tg["requests"], 2, "шапка + сохранённый нарратив")
        self.assertEqual(tg["bodies"][1]["body"]["text"], row["llm_text"])
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 1, "ровно один ok-push на восстановление")
        self.assertIn("status=up", kq[0])

    def test_30_retry_pending_empty_is_noop(self):
        build_db(self.db)
        p = run_report(["--retry-pending"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--retry-pending: pending_retry строк: 0", p.stdout)
        self.assertEqual(len(self.rows()), 0, "новых строк нет")
        self.assertEqual(stats(LLM.port)["requests"], 0)
        self.assertEqual(stats(TG.port)["requests"], 0)
        self.assertEqual(stats(KUMA.port)["queries"], [])

    def test_31_retry_ttl_same_as_main(self):
        # retry-прогон тоже +1 attempt; TTL 3 не ускоряется и не стопорится:
        # attempts 1(gen)+2+3 → pending_retry, 4-й → failed + один down
        control(TG.port, {"target": "tg", "fail_always": True})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for i in range(2, 5):
            p = run_report(["--retry-pending"], self.env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            row = self.rows()[0]
            self.assertEqual(row["delivery_attempts"], i,
                             f"retry-прогон {i - 1}: ровно +1 attempt")
            if i < 4:
                self.assertEqual(row["delivery_status"], "pending_retry")
        row = self.rows()[0]
        self.assertEqual(row["delivery_status"], "failed",
                         "attempts=4 > TTL 3 → failed, не залипает")
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 1, "up-ов не было, ровно один down")
        self.assertIn("status=down", kq[0])
        self.assertEqual(stats(LLM.port)["requests"], 1,
                         "LLM только в первом прогоне")

    def test_32_retry_exclusivity_and_sent_row_skipped(self):
        self.gen()
        n = len(self.rows())
        for flags in (["--retry-pending", "--resend-last"],
                      ["--retry-pending", "--regenerate-last"],
                      ["--resend-last", "--retry-pending"]):
            p = run_report(flags, self.env)
            self.assertEqual(p.returncode, 2, f"эксклюзивность: {flags}")
            self.assertIn("взаимоисключающие", p.stderr)
        p = run_report(["--retry-pending"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pending_retry строк: 0", p.stdout,
                      "sent-строка retry-ом не трогается")
        self.assertEqual(len(self.rows()), n)
        self.assertEqual(stats(TG.port)["requests"], 2,
                         "только пара из gen; retry ничего не досылал")

    def test_33_kuma_up_per_successful_delivery(self):
        # ok-push: неудача — тишина; восстановление pending→sent — один up;
        # успешная досылка sent-строки (--resend-last) — тоже up; down нет
        control(TG.port, {"target": "tg", "fail_always": True})
        p = self.gen()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.rows()[0]["delivery_status"], "pending_retry")
        self.assertEqual(stats(KUMA.port)["queries"], [], "неудача — без push-ей")
        control(TG.port, {"action": "reset"})
        p = run_report(["--retry-pending"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 1)
        self.assertIn("status=up", kq[0])
        self.assertIn("sent", kq[0])
        p = run_report(["--resend-last"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 2, "успешная досылка sent-строки — тоже ok-push")
        self.assertTrue(all("status=up" in x for x in kq), "down-ов нет")

    # --- §7.3 smoke (test_s1…test_s5) + U8.4 (test_s6) ------------------------
    def test_s1_dry_run(self):
        build_db(self.db)
        p = run_report(["--dry-run"], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('"day_epoch": ' + str(D), p.stdout)
        self.assertIn("Погода за 30.09:", p.stdout)
        self.assertIn("Нарратив недоступен", p.stdout)
        self.assertEqual(len(self.rows()), 0, "dry-run не пишет в БД")
        self.assertEqual(stats(LLM.port)["requests"], 0)
        self.assertEqual(stats(TG.port)["requests"], 0)

    def test_s2_reports_count_is_one(self):
        self.gen()
        rows = q(self.db, "SELECT COUNT(*) AS n FROM reports WHERE day_epoch=?", (D,))
        self.assertEqual(rows[0]["n"], 1)

    def test_s3_resend_last_exit_codes(self):
        build_db(self.db)
        p = run_report(["--resend-last"], self.env)
        self.assertEqual(p.returncode, 1, "нет строк → exit 1 (m-3/m-18)")
        self.gen()
        p = run_report(["--resend-last"], self.env)
        self.assertEqual(p.returncode, 0, "есть строка → exit 0")

    def test_s4_logs_grep_empty(self):
        p = subprocess.run(
            f"printf '%s\\n' \"$LOGS\" | grep -iE 'api[-]?key|bot[-]?token' || true",
            shell=True, capture_output=True, text=True,
            env={"LOGS": "\n".join(MODULE_LOGS)})
        self.assertEqual(p.stdout.strip(), "", "журнал прогонов без секретов")

    def test_s5_budget_note(self):
        # §7.3 п.5: проверка бюджета Z.ai — вручную после первого прод-прогона
        # (usage за месяц = $0.00). Здесь фиксируем напоминание.
        print("[s5] бюджет Z.ai — проверка ВРУЧНУЮ после первого прод-прогона "
              "(usage за месяц = $0.00); в локальном стенде не автоматизируется")
        self.assertTrue(True)

    def test_s6_literary_rounding_smoke(self):
        # U8.4: литературный нарратив со СВОБОДНЫМИ числами — «около минус
        # одного» при факте t_out_min=-0.9 и «почти на четыре градуса» при
        # t_vs_prev=-3.61 — ПРОХОДИТ полный пайплайн (до v1.2.1 округление
        # отвергалось чекером §7.1 → invalid_numbers → fallback)
        control(LLM.port, {"target": "llm", "content":
                "Ночь на даче в Видлице выдалась студёной: к рассвету было "
                "около минус одного, лужи подёрнулись льдом. Днём заметно "
                "оттепелело — почти на четыре градуса теплее вчерашнего."})
        build_db(self.db, d_over={"t_out_min": -0.9, "t_out_avg": 3.29,
                                  "frost_flag": 1})
        # t_vs_prev = 3.29 (D) − 6.90 (D-1) = −3.61 — «почти на четыре»
        p = run_report([], self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = self.rows()[0]
        self.assertIsNone(row["llm_error"], "округление — больше не отказ")
        self.assertIn("около минус одного", row["llm_text"])
        self.assertIn("почти на четыре градуса", row["llm_text"])
        facts = json.loads(row["facts_json"])
        self.assertEqual(facts["temperature"]["t_out_min"], -0.9)
        self.assertEqual(facts["comparison"]["t_vs_prev"], -3.61)
        self.assertEqual(row["delivery_status"], "sent")
        tg = stats(TG.port)
        self.assertEqual(tg["requests"], 2, "шапка + литературный нарратив")
        self.assertEqual(tg["bodies"][1]["body"]["text"], row["llm_text"])
        kq = stats(KUMA.port)["queries"]
        self.assertEqual(len(kq), 1, "ok-push на успешную доставку")
        self.assertIn("status=up", kq[0])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        LLM.stop()
        TG.stop()
        KUMA.stop()
