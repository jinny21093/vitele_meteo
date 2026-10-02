#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_report.py — U8 «Суточный ИИ-отчёт» (v1.2.1).

v1.2.1 (U8.4, литературный нарратив — решение владельца о продукте):
  • Продукт: ДВЕ отправки с разными ролями — шапка = приборы (SQL,
    точность, неизменно), нарратив = литературный пересказ БЕЗ
    побуквенного контроля чисел.
  • _SYSTEM_PROMPT_LINES: четыре пункта о числах («только числа из
    фактов», «копия из фактов», «никаких около/примерно», «минус
    дословно») заменены ОДНИМ дословным пунктом владельца («Ты —
    наблюдатель… Числа используй свободно… НО: не выдумывай ЯВЛЕНИЙ…»);
    остальные пункты (null, day_partial, norm, events_truncated, язык,
    стиль 2–3 абзаца, связка фактов, Видлица) сохранены.
  • Чекер §7.1 (двухуровневый тест чисел) УДАЛЁН вместе с причиной
    отказа invalid_numbers — enum §3.5 сужен до 5 значений (timeout,
    empty_response, wrong_language, provider_unreachable, provider_5xx).
    Отказы нарратива: wrong_language, пустой ответ, не-текст,
    провайдерские. fallback_message: assert на enum снят — исторические
    строки БД со старым вердиктом invalid_numbers рендерятся как есть.
  • Доставка без изменений (§3.3: шапка Markdown + нарратив плейном,
    пара сообщений).
  • Спека §2/§3.5/§7.1/§7.2 (сценарии 6, 20) временно расходится с
    кодом — правка спеки за ревью (прецедент U8.2/U8.3).

v1.2.0 (U8.3, retry-таймер + ok-push Kuma + слот 07:20):
  • CLI --retry-pending: ТОЛЬКО досылка всех pending_retry (тем же
    message_for_row/deliver — семантика §3.3 без изменений: один круг
    in-run попыток, +1 прогон к attempts, TTL 3, failed+Kuma на переходе),
    БЕЗ генерации текущего дня; LLM зовётся только на NULL/NULL-строке.
    Для hourly-таймера weather-report-retry (OnCalendar *:05:00 — вне
    топ-минуты, не пересекается с отчётным слотом).
  • ok-push в Kuma (механизм M6, тот же push-URL): каждый переход строки
    в sent (свежая доставка или восстановление) пушит status=up —
    молчание Kuma = инцидент. down — как раньше: ровно один на переход
    в failed. Отдельный монитор не нужен.
  • Слоты (юниты, деплой после приёмки): weather-report.timer
    06:50 → 07:20 (решение владельца; снапшот/бэкап остаются раньше),
    weather-report-retry.timer — :05 каждого часа.

v1.1.1 (U8.2, локация в промпте — фикс галлюцинации «в Москве»):
  • _SYSTEM_PROMPT_LINES +1 пункт дословно: место — дача в Видлице
    (частная метеостанция), писать «на даче в Видлице»/«в Видлице»,
    никаких городов и регионов, кроме Видлицы, Москву не упоминать
    никогда. Константа кода (не env, не конфиг); чекер чисел §7.1
    словами НЕ расширяется (решение ревьюера): галлюцинацию локации
    ловим промптом и наблюдением штатных прогонов 06:50.

v1.1.0 (U8.1, регенерация — второй пункт мини-задания U8.1):
  • CLI --regenerate-last / --regenerate DAY_EPOCH: повтор LLM-шага для
    СУЩЕСТВУЮЩЕЙ строки reports: llm_* → NULL, LLM по канону §2 (те же
    проверки §2.5 + чекер §7.1), доставка §3.3 со свежим кругом TTL
    (симметрично resend_reset). Новых строк не создаёт; факты не
    пересобираются; пустой день (n_samples=0) LLM не вызывает (§4.4).

v1.0.3 (U8.1, промпт-фикс — план А ревью по наблюдению live-прогонов:
glm-4.7-flash при t=0.3 склонен считать модуль отрицательной дельты):
  • _SYSTEM_PROMPT_LINES +2 пункта дословно: (1) отрицательные дельты —
    с минусом дословно, без инверсии знака; (2) в конце связать факты
    дня в картину. Чекер §7.1 НЕ ослаблен: «меньше на 2.4 мм» при факте
    −2.4 по-прежнему invalid_numbers, проходит только дословный минус.

v1.0.2 (находки live-прогонов 2026-10-01):
  • fallback_message: reason enum в code-backticks — живой Bot API парсит
    Markdown, `_` значений provider_unreachable/provider_5xx без обёртки
    даёт 400 «can't parse entities» (моки §7.2 парсинг не эмулируют).

v1.0.1 (находки первого live-прогона 2026-10-01):
  • tg_send шлёт User-Agent weather-report/<версия> — CF Bot Fight Mode на
    workers.dev отвечает 403 (error code 1010) на дефолтный Python-urllib UA;
  • чекер чисел §7.1: день месяца текстовой датой («30 сентября 2026 года»)
    — исключение уровня B, иначе естественная дата модели ломает отчёт.

Спека-канон: docs/weather-report-spec.md v1.1.1 (коммит f891a33).
Числа считает SQL, текст пишет LLM (Z.ai, только бесплатные модели whitelist:
glm-4.7-flash основная / glm-4.5-flash резервная). Доставка — Telegram Bot API
через TELEGRAM_API_BASE (дефолт https://api.telegram.org; релей — CF Worker
pass-through, токен не логируется ни релеем, ни здесь).

Порядок прогона §4.3: flock 150с (снятие в finally) → валидация LLM_MODEL ∈
whitelist (fail-fast, до первого запроса к провайдеру) → валидация v_daily по
PRAGMA table_info → досылка pending_retry → факты за вчерашний день MSK →
деградация при >8КБ → INSERT reports(status=pending) → LLM → Telegram →
статусы доставки.

LLM-канон (§2, v1.1.1): temperature 0.3, max_tokens 800, HTTP timeout 60с,
retries 2 (backoff 2с/8с, только timeout/5xx), 4xx/402 — без retry;
thinking={"type":"disabled"} — КОНСТАНТА в каждом запросе (неконфигурируемо,
P1: reasoning_content съедает max_tokens reasoning-модели); сравнение model —
ПОБУКВЕННОЕ (A19 закрыт); пустой content при непустом reasoning_content →
empty_response; finish_reason="length" → WARN и принять; ∉ {stop,length} →
отказ. reasoning_tokens из usage — в лог (решение о колонке в БД —
в pre-flight-отчёте: схема НЕ расширяется без ревью).

Доставка (§3): шапка SQL с Telegram Markdown (parse_mode=Markdown) + LLM-текст
плейном ВТОРЫМ сообщением без parse_mode (A15: спецсимволы LLM не экранируются
— смешивать в одном сообщении нельзя). In-run 3 попытки (2/8/30с), таймаут
попытки 15с; исчерпаны → pending_retry; TTL 3 следующих прогона, на 4-й
неудаче → failed + один Kuma push на переход (endpoint kuma_push.conf,
механизм weather_backup.sh, M6). Причины fallback — enum §3.5
(5 значений, v1.2.1; исторический invalid_numbers из старых строк БД
рендерится fallback-ом как есть).

Идемпотентность (§4.4): idempotency_key = "{day_epoch}:{chat_id}" UNIQUE;
rerun-правила по llm_text/llm_error. CLI (§5.3): --dry-run (без LLM и
доставки, exit 0), --resend-last (нет строк → exit 1), --resend DAY_EPOCH,
--regenerate-last / --regenerate DAY_EPOCH (U8.1: повтор LLM-шага
существующей строки, llm_* → NULL, новых строк не создаёт),
--retry-pending (U8.3: только досылка pending_retry, без генерации).

Секреты (§6.2): только env (EnvironmentFile юнита) или файл по
LLM_SECRETS_FILE (600, KEY=VALUE); env приоритетен. В логи/argv/git — никогда.
Ресурсы: Nice=19 и TimeoutStartSec=300 задаёт systemd-юнит; stdlib-only.
"""
import argparse
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

WEATHER_REPORT_VERSION = "1.2.1"
DEFAULT_DB = "/home/auditbot/weather-dash/weather.db"
LOCK_PATH = "/var/lock/weather-report.lock"
LOCK_TIMEOUT_S = 150          # §4.2: блокирующий flock с таймаутом 150с
TZ_OFF = 10800                # Europe/Moscow, fixed_offset (§4.5)
MSK_MIDNIGHT_REM = 75600      # инвариант day_epoch % 86400 == 75600 (§1.1)
FULL_DAY_SAMPLES = 720        # «полный день» для нормы (§1.2)
NOMINAL_SAMPLES = 1440        # покрытие суток при опросе 60с (§1.4)
FACTS_MAX_BYTES = 8 * 1024    # лимит facts_json; превышение — деградация (§1.4)
PROMPT_MAX_BYTES = 16 * 1024  # промпт ≤ 16 КБ (§6.3)
RESPONSE_MAX_BYTES = 8 * 1024 # ответ LLM ≤ 8 КБ (§6.3)
EVENTS_CAP = 15               # кап событий в фактах (§1.2)
NORM_DAYS_MAX = 7             # норма: до 7 предыдущих дней (§1.2)
NORM_MIN_DAYS = 3             # N<3 → блок нормы отсутствует (§1.2)

# §2.1: whitelist бесплатных моделей, verbatim (A17/A18). Нарушение — ERROR+exit 1.
ALLOWED_FREE_MODELS = ("glm-4.7-flash", "glm-4.5-flash")
# §2.2: константы генерации.
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 800
LLM_TIMEOUT_S = 60            # env-оверрайд LLM_TIMEOUT_S — только для стендов
LLM_RETRIES = 2
LLM_BACKOFF_S = (2, 8)
THINKING_DISABLED = {"type": "disabled"}  # v1.1.1: константа, в КАЖДЫЙ запрос
# §3.3: in-run доставка.
TG_ATTEMPTS = 3
TG_BACKOFF_S = (2, 8, 30)
TG_TIMEOUT_S = 15             # env-оверрайд TG_TIMEOUT_S — только для стендов
PENDING_TTL_RUNS = 3          # pending_retry живёт ≤3 прогонов, на 4-й — failed
# §3.5 (v1.2.1/U8.4): enum причин отказа — 5 значений; invalid_numbers
# удалён (нарратив без побуквенного контроля чисел). Исторические строки
# БД могут хранить старое значение — fallback_message рендерит как есть.
LLM_ERROR_ENUM = ("timeout", "empty_response", "wrong_language",
                  "provider_unreachable", "provider_5xx")
SEVERITY_RANK = {"high": 0, "mid": 1, "low": 2}
KUMA_CONF = "/home/auditbot/weather-dash/kuma_push.conf"  # M6 (weather_backup.sh)

# §1.2: 22 канонических поля v_daily (verbatim ui/server.py DAILY_FIELDS).
DAILY_FIELDS = ("day_epoch", "t_out_min", "t_out_max", "t_out_avg", "t_out_min_time",
                "t_out_max_time", "p_min", "p_max", "wind_avg", "wind_max", "gust_max",
                "wind_run_km", "wind_dir_mode", "rain_mm", "rain_hours",
                "solar_sum_wh_m2", "uvi_max", "gdd_day", "frost_flag",
                "hard_freeze_flag", "fog_flag", "n_samples")
# §1.2: каталог 21 типа событий (verbatim ui/server.py EVENT_TYPES).
EVENT_TYPES = ("FROST", "HARD_FREEZE", "FOG", "STORM_APPROACH", "THUNDER_RISK",
               "HEAVY_RAIN", "DOWNPOUR", "STRONG_WIND", "HURRICANE_GUST",
               "HEATWAVE", "DRY_SPELL", "CALM", "RAPID_TEMP_DROP",
               "RAPID_TEMP_RISE", "PRESSURE_CRASH", "RAIN_COUNTER_RESET",
               "SENSOR_MISSING", "SENSOR_STUCK", "SENSOR_DRIFT", "SENSOR_ANOMALY",
               "BATTERY_LOW")
# Тест 14 §7.2: журнал не содержит api[-]?key / bot[-]?token (паттерн §6.2).
# В логах эти подстроки не используются; динамический текст — через sanitize().
_SECRET_LOG_RE = re.compile(r"(?i)\S*(?:(?:api[-]?)?key|bot[-]?token)\S*")


class LLMRefusal(Exception):
    """Отказ LLM (§2.5): reason — значение enum §3.5."""

    def __init__(self, reason):
        super().__init__(reason)
        assert reason in LLM_ERROR_ENUM, reason
        self.reason = reason


class TGError(Exception):
    """Неуспешная попытка доставки (любая ошибка попытки, §3.3)."""


def log(msg):
    print(f"[report {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sanitize(text):
    """Маскировка подстрок, похожих на секреты, перед печатью (тест 14)."""
    return _SECRET_LOG_RE.sub("***", str(text))


def now_epoch():
    """Тестовые стенды могут фиксировать время (env WEATHER_REPORT_NOW)."""
    override = os.environ.get("WEATHER_REPORT_NOW")
    return int(override) if override else int(time.time())


def d_epoch_of(now):
    """§1.1: D_epoch = ((now + 10800) // 86400 − 1) · 86400 − 10800."""
    d = ((now + TZ_OFF) // 86400 - 1) * 86400 - TZ_OFF
    assert d % 86400 == MSK_MIDNIGHT_REM, f"инвариант дня нарушен: {d}"
    return d


def day_label(d, year=False):
    fmt = "%d.%m.%Y" if year else "%d.%m"
    return time.strftime(fmt, time.gmtime(d + TZ_OFF))


def hhmm(ts):
    return time.strftime("%H:%M", time.gmtime(ts + TZ_OFF))


def open_db(path, readonly=False):
    mode = "ro" if readonly else "rw"
    con = sqlite3.connect(f"file:{path}?mode={mode}", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.isolation_level = None           # ручные транзакции (практика этапа B)
    con.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        con.execute("PRAGMA synchronous=NORMAL")
    return con


def load_env_file(path):
    """Файл 600 формата KEY=VALUE (§6.2): env приоритетен, файл — дополнение."""
    vals = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def load_cfg():
    """§6.2: LLM_* и TELEGRAM_* — env, при пустоте файл LLM_SECRETS_FILE.
    Валидация whitelist модели — fail-fast (§2.1, A18). Значения не печатаются."""
    file_vals = {}
    secrets_path = os.environ.get("LLM_SECRETS_FILE", "").strip()
    if secrets_path:
        st = os.stat(secrets_path)
        if st.st_mode & 0o777 != 0o600:
            log(f"WARN файл секретов {sanitize(secrets_path)}: права "
                f"{oct(st.st_mode & 0o777)} — ожидается 600")
        file_vals = load_env_file(secrets_path)

    def get(name):
        v = os.environ.get(name, "").strip()
        if v:
            return v
        v = file_vals.get(name, "").strip()
        if v:
            log(f"конфиг: {name} взят из файла секретов (env пуст)")
        return v

    cfg = {k: get(k) for k in
           ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    cfg["TELEGRAM_API_BASE"] = get("TELEGRAM_API_BASE") or "https://api.telegram.org"
    missing = [k for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
                           "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not cfg[k]]
    if missing:
        raise RuntimeError(f"конфиг неполон, нет значений: {', '.join(missing)}")
    if cfg["LLM_MODEL"] not in ALLOWED_FREE_MODELS:
        # §2.1/тест 21: платная/незнакомая модель — ERROR до любого запроса.
        log(f"ERROR model not in free whitelist: {cfg['LLM_MODEL']}; "
            f"allowed={list(ALLOWED_FREE_MODELS)}")
        raise SystemExit(1)
    log(f"конфиг ок: модель {cfg['LLM_MODEL']} (whitelist), "
        f"базовые URL заданы, доставка в мессенджер настроена")
    return cfg


def validate_v_daily(con):
    """§1.2/D2: все 22 канонических поля должны существовать в v_daily
    (PRAGMA table_info). Расхождение → ERROR + exit 1 (тест 16)."""
    actual = {r[1] for r in con.execute("PRAGMA table_info(v_daily)")}
    missing = [c for c in DAILY_FIELDS if c not in actual]
    if missing:
        log(f"ERROR схема v_daily не совпала с каноном, нет полей: {missing}")
        raise SystemExit(1)
    log("PRAGMA v_daily: 22 канонических поля на месте")


def ensure_reports(con):
    """Таблица reports должна существовать (миграция применена при деплое)."""
    have = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='reports'").fetchone()
    if not have:
        log("ERROR таблица reports отсутствует — примените миграцию "
            "docs/migrations/004_reports.sql")
        raise SystemExit(1)


def acquire_lock():
    """§4.2: flock /var/lock/weather-report.lock, таймаут 150с, снятие в finally.
    Истёк таймаут → WARN + None (выход 0, не ошибка)."""
    f = open(LOCK_PATH, "a")
    deadline = time.time() + LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.time() >= deadline:
                f.close()
                log("WARN previous run still active — пропускаю прогон (выход 0)")
                return None
            time.sleep(1)


def fetch_facts(con, d):
    """§1: факты за закрытые сутки D. -> (facts dict, meta dict)."""
    cols = ",".join(DAILY_FIELDS)
    row = con.execute(f"SELECT {cols} FROM v_daily WHERE day_epoch=?", (d,)).fetchone()
    meta = {"row_present": row is not None}

    def val(name):
        return row[name] if row is not None else None

    n_samples = int(val("n_samples") or 0)
    t_min, t_max = val("t_out_min"), val("t_out_max")
    p_min, p_max = val("p_min"), val("p_max")
    t_avg = val("t_out_avg")
    facts = {
        "day_epoch": d,
        "day_label": day_label(d, year=True),
        # §1.4: n_samples < 720 → day_partial=true (литерально; для пустого
        # дня нарратива не будет — текст «данных нет», LLM не вызывается)
        "day_partial": n_samples < FULL_DAY_SAMPLES,
        "n_samples": n_samples,
        "temperature": {
            "t_out_min": t_min, "t_out_min_time": val("t_out_min_time"),
            "t_out_max": t_max, "t_out_max_time": val("t_out_max_time"),
            "t_out_avg": t_avg,
            "t_delta": round(t_max - t_min, 2) if (t_max is not None and t_min is not None) else None,
        },
        "pressure": {
            "p_min": p_min, "p_max": p_max,
            "p_delta": round(p_max - p_min, 2) if (p_max is not None and p_min is not None) else None,
        },
        "wind": {
            "wind_avg": val("wind_avg"), "wind_max": val("wind_max"),
            "gust_max": val("gust_max"), "wind_dir_mode": val("wind_dir_mode"),
            "wind_run_km": val("wind_run_km"),
        },
        "rain": {"rain_mm": val("rain_mm"), "rain_hours": val("rain_hours")},
        "solar": {"solar_sum_wh_m2": val("solar_sum_wh_m2"), "uvi_max": val("uvi_max")},
        "flags": {
            "frost_flag": val("frost_flag"), "hard_freeze_flag": val("hard_freeze_flag"),
            "fog_flag": val("fog_flag"), "gdd_day": val("gdd_day"),
        },
    }

    # §1.2 события: overlap-семантика [ts_start, ts_end] ∩ [D, D+86400).
    # Открытые (ts_end IS NULL) считаются пересекающими окно конца дня.
    evrows = con.execute(
        "SELECT event_type, severity, value, ts_start, ts_end FROM events "
        "WHERE ts_start < ? AND COALESCE(ts_end, ?) >= ?",
        (d + 86400, d + 86400, d)).fetchall()
    events = []
    for e in evrows:
        if e["event_type"] not in EVENT_TYPES:
            # m-13: неизвестный тип — WARN + включить как есть, отчёт не падает
            log(f"WARN событие неизвестного типа: {sanitize(e['event_type'])} — "
                f"включено в факты как есть")
        events.append({
            "event_type": e["event_type"], "severity": e["severity"],
            "ts_start": e["ts_start"], "ts_end": e["ts_end"],
            "duration_s": (e["ts_end"] - e["ts_start"]) if e["ts_end"] is not None else None,
            "value": e["value"],
        })
    # severity DESC (high→mid→low), внутри — ts_start ASC
    events.sort(key=lambda x: (SEVERITY_RANK.get(x["severity"], 3), x["ts_start"]))
    facts["events"] = events[:EVENTS_CAP]
    facts["events_total"] = len(events)
    facts["events_truncated"] = len(events) > EVENTS_CAP

    # §1.3 comparison: блок отсутствует целиком, если строки D-1 нет (m-6)
    prev = con.execute(
        "SELECT t_out_avg, rain_mm FROM v_daily WHERE day_epoch=?", (d - 86400,)).fetchone()
    if prev is not None:
        t_vs = (round(t_avg - prev["t_out_avg"], 2)
                if (t_avg is not None and prev["t_out_avg"] is not None) else None)
        r_vs = (round((val("rain_mm") or 0.0) - (prev["rain_mm"] or 0.0), 2)
                if (val("rain_mm") is not None and prev["rain_mm"] is not None) else None)
        facts["comparison"] = {"prev_day_epoch": d - 86400, "t_vs_prev": t_vs,
                               "rain_vs_prev": r_vs}

    # §1.2 норма: D-1..D-7, только полные дни (n_samples>=720); N<3 → нет блока
    prows = con.execute(
        "SELECT t_out_avg, rain_mm, n_samples FROM v_daily WHERE day_epoch>=? "
        "AND day_epoch<?", (d - NORM_DAYS_MAX * 86400, d)).fetchall()
    full = [r for r in prows if (r["n_samples"] or 0) >= FULL_DAY_SAMPLES]
    if len(full) >= NORM_MIN_DAYS:
        ta = [r["t_out_avg"] for r in full if r["t_out_avg"] is not None]
        ra = [r["rain_mm"] for r in full if r["rain_mm"] is not None]
        facts["norm"] = {
            "norm_n": min(len(full), NORM_DAYS_MAX),
            "norm_t_avg": round(sum(ta) / len(ta), 2) if ta else None,
            "norm_rain_mm": round(sum(ra) / len(ra), 2) if ra else None,
        }
    return facts, meta


def facts_json_bytes(facts):
    return len(json.dumps(facts, ensure_ascii=False).encode("utf-8"))


def degrade_facts(facts):
    """§1.4: факты >8КБ → обрезать events до 0, events_truncated=true,
    events_total сохранить. Никогда не ERROR (MAJ-2)."""
    if facts_json_bytes(facts) <= FACTS_MAX_BYTES:
        return json.dumps(facts, ensure_ascii=False)
    total = facts.get("events_total", 0)
    facts["events"] = []
    facts["events_truncated"] = True
    log(f"WARN факты {facts_json_bytes(facts)}Б > 8КБ — деградация: события "
        f"обрезаны до 0 (events_total={total}), отчёт продолжается")
    return json.dumps(facts, ensure_ascii=False)


# U8.4 (решение владельца о продукте): двухуровневый тест чисел §7.1 и
# причина отказа invalid_numbers УДАЛЕНЫ — нарратив литературный, числа
# используются свободно (округление, перефразирование), побуквенный
# контроль снят. За фактическую корректность отвечают промпт (запрет
# выдуманных ЯВЛЕНИЙ, null — «данных нет») и оставшиеся проверки:
# wrong_language, пустой ответ, не-текст, провайдерские.


_CYR_RE = re.compile(r"[\u0400-\u04FF]")


def cyr_share(text):
    """§2.3: доля кириллицы среди буквенных символов (эвристика A12)."""
    alpha = sum(1 for c in text if c.isalpha())
    if not alpha:
        return 0.0
    return sum(1 for c in text if _CYR_RE.match(c)) / alpha


def looks_non_text(text):
    """§2.5 «ответ — не текст (JSON, markdown-код, объект)»."""
    t = text.strip()
    if t.startswith("```"):
        return True
    if t.startswith(("{", "[")):
        try:
            json.loads(t)
            return True
        except ValueError:
            return False
    return False


# --- §2.4 промпт-дисциплина (дословные формулировки спеки) -------------------
_SYSTEM_PROMPT_LINES = (
    # U8.4 (v1.2.1): пункт о числах переписан ДОСЛОВНО по директиве
    # владельца (литературный нарратив; числа свободны; НО — без
    # выдуманных явлений; место — только Видлица; язык — русский).
    "Ты — наблюдатель на метеостанции на даче в Видлице. Пиши живой, "
    "литературный рассказ о погоде дня: вьюга, ветер, дождь — образно и "
    "по-человечески. Числа используй свободно: округляй (\"почти на четыре "
    "градуса\", \"около минус одного\"), перефразируй (\"к ночи "
    "подморозило\"). Направления и характер явлений передавай словами, "
    "не таблицей. НО: не выдумывай ЯВЛЕНИЙ, которых не было (не было "
    "дождя — не пишем дождь; null — \"данных нет\"); место — только "
    "Видлица; язык — русский.",
    "Если поле факта null — не утверждай про него ничего. \"Дождя не было\" "
    "при rain_mm = null — ошибка. Пиши \"данные недоступны\".",
    "Если day_partial = true — упомяни, что данные неполные, "
    "с покрытием {n_samples}/1440.",
    "Если norm_n присутствует и norm_n < 7 — упомяни \"норма по N дням\". "
    "Если блока norm нет — не упоминай норму и \"типичную погоду\" вообще.",
    "Если events_truncated = true — упомяни, что события показаны не все.",
    "Ответ — только на русском. Только текст, без markdown-обёрток, без JSON.",
    "Стиль: спокойный, повествовательный, 2–3 абзаца, для человека.",
    "В конце 2–3 абзацев свяжи факты дня в картину: разброс температур, "
    "изменение давления, события, сравнение с нормой.",
    "Место: дача в Видлице (частная метеостанция). Пиши \"на даче в "
    "Видлице\" или \"в Видлице\"; никаких городов и регионов, кроме "
    "Видлицы; Москву не упоминай никогда.",
)


def build_messages(facts):
    """Системный промпт §2.4 + user с JSON-фактов. Лимит промпта ≤16КБ (§6.3)."""
    system = "\n".join(_SYSTEM_PROMPT_LINES).replace(
        "{n_samples}", str(facts.get("n_samples", 0)))
    user = (f"Факты за {facts['day_label']} (MSK, day_epoch={facts['day_epoch']}):\n"
            + json.dumps(facts, ensure_ascii=False))
    size = len(system.encode("utf-8")) + len(user.encode("utf-8"))
    if size > PROMPT_MAX_BYTES:
        log(f"ERROR промпт {size}Б > 16КБ — нарушение лимита §6.3")
        raise LLMRefusal("provider_unreachable")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def call_llm_raw(cfg, messages):
    """Один цикл запросов к провайдеру: до 1+retries попыток, backoff 2с/8с —
    ТОЛЬКО для timeout/5xx (§2.2); любые 4xx (включая 402) — сразу отказ
    provider_unreachable без retry (m-9/§2.5). -> dict ответа."""
    url = cfg["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["LLM_MODEL"],
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
        # v1.1.1 §2.2: thinking={"type":"disabled"} — константа в КАЖДОМ
        # запросе, неконфигурируема (P1: иначе reasoning_content съедает
        # max_tokens; для не-reasoning glm-4.5-flash флаг безвреден).
        "thinking": THINKING_DISABLED,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    timeout = float(os.environ.get("LLM_TIMEOUT_S") or LLM_TIMEOUT_S)
    scale = float(os.environ.get("WEATHER_REPORT_RETRY_SCALE") or 1.0)
    last_net = None
    for attempt in range(LLM_RETRIES + 1):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cfg["LLM_API_KEY"]})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(512).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            if 400 <= e.code < 500:
                # 4xx (401/402/403/408, ...) — без retry, §2.5 (M5: verbatim в лог)
                log(f"LLM HTTP {e.code} — 4xx без retry: {sanitize(detail[:300])}")
                raise LLMRefusal("provider_unreachable") from e
            # 5xx — retry по §2.2 (тест 25: 5xx, потом восстановление)
            last_net = f"HTTP {e.code}"
            log(f"WARN LLM попытка {attempt + 1}/{LLM_RETRIES + 1}: "
                f"HTTP {e.code} (5xx) — retry по backoff")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_net = sanitize(f"{type(e).__name__}: {e}")
            log(f"WARN LLM попытка {attempt + 1}/{LLM_RETRIES + 1} неуспешна: "
                f"{last_net}")
        if attempt < LLM_RETRIES:
            time.sleep(LLM_BACKOFF_S[attempt] * scale)
    # все попытки — сеть/таймаут либо 5xx-исчерпание
    raise LLMRefusal("timeout" if _looks_timeout(last_net) else "provider_5xx")


def _looks_timeout(err_text):
    return bool(err_text) and ("timed out" in err_text.lower()
                               or "timeout" in err_text.lower()
                               or "timedout" in err_text.lower())


def call_llm(cfg, facts):
    """Полный LLM-шаг: запрос + все проверки §2.5. -> dict с llm_text и
    метаданными. Отказ -> LLMRefusal(reason из enum §3.5)."""
    messages = build_messages(facts)
    resp = call_llm_raw(cfg, messages)

    # §2.5: подмена модели — побуквенное сравнение (A19 закрыт, P1)
    resp_model = resp.get("model")
    if resp_model != cfg["LLM_MODEL"]:
        log(f"ERROR подмена модели: запрошено {cfg['LLM_MODEL']}, "
            f"ответило {sanitize(resp_model)!r} — отказ без retry")
        raise LLMRefusal("provider_unreachable")
    # §2.5: признак платного тарифа (tier/plan != free)
    for k in ("tier", "plan"):
        if resp.get(k) not in (None, "", "free"):
            log(f"ERROR провайдер вернул {k}={resp[k]!r} — не free")
            raise LLMRefusal("provider_unreachable")

    choices = resp.get("choices") or []
    if not choices:
        log("ERROR ответ без choices")
        raise LLMRefusal("empty_response")
    ch = choices[0]
    msg = ch.get("message") or {}
    finish = ch.get("finish_reason")
    usage = resp.get("usage") or {}

    # §2.2: reasoning_tokens из usage — в лог (хранение в БД — pre-flight-
    # отчёт: схема не расширяется без ревью)
    log(f"LLM ok: finish_reason={finish}, "
        f"prompt/completion={usage.get('prompt_tokens')}/{usage.get('completion_tokens')}, "
        f"reasoning_tokens={usage.get('reasoning_tokens')}")

    if finish not in ("stop", "length"):
        # m-12: content_filter и прочее → отказ (маппинг enum — см. отчёт U8)
        log(f"ERROR finish_reason={finish!r} ∉ {{stop,length}} — отказ")
        raise LLMRefusal("provider_unreachable")
    if finish == "length":
        # §2.2/тест 17: текст принимается, WARN, fallback не триггерится
        log("WARN finish_reason=length — текст принят (обрезка хуже отсутствия)")

    content = msg.get("content")
    reasoning = msg.get("reasoning_content")
    if content is None or not str(content).strip():
        # §2.5 v1.1.1: пустой ответ, включая пустой content при непустом
        # reasoning_content (P1: reasoning-модель может вернуть только мысли)
        if reasoning:
            log("LLM: пустой content при непустом reasoning_content")
        raise LLMRefusal("empty_response")

    text = str(content).strip()
    if len(text.encode("utf-8")) > RESPONSE_MAX_BYTES:
        log(f"ERROR ответ {len(text.encode('utf-8'))}Б > 8КБ — нарушение §6.3")
        raise LLMRefusal("empty_response")
    if looks_non_text(text):
        log("LLM вернул не текст (JSON/markdown-код)")
        raise LLMRefusal("empty_response")
    if cyr_share(text) < 0.70:
        log(f"LLM: доля кириллицы {cyr_share(text):.0%} < 70%")
        raise LLMRefusal("wrong_language")
    # U8.4: побуквенный тест чисел §7.1 снят — нарратив литературный,
    # числа свободны (округление/перефразирование по пункту владельца);
    # invalid_numbers больше не ставится.

    return {
        "llm_text": text,
        "llm_model": resp_model,
        "llm_tokens_in": usage.get("prompt_tokens"),
        "llm_tokens_out": usage.get("completion_tokens"),
    }


# --- §3.4 сообщения SQL-слоя --------------------------------------------------
def header_markdown(facts):
    """Шапка §3.4 (MAJ-4): SQL-слой форматирует, ключевые числа — жирным
    (Telegram Markdown). Без финальной фразы о нарративе."""
    t = facts["temperature"]
    line = (f"Погода за {day_label(facts['day_epoch'])}: "
            f"Tmin *{fmt_num(t['t_out_min'])}* °C ({hhmm(t['t_out_min_time']) if t['t_out_min_time'] else '--:--'}), "
            f"Tmax *{fmt_num(t['t_out_max'])}* °C ({hhmm(t['t_out_max_time']) if t['t_out_max_time'] else '--:--'}), "
            f"осадки *{fmt_num(facts['rain']['rain_mm'])}* мм, "
            f"ветер до *{fmt_num(facts['wind']['gust_max'])}* м/с.")
    if facts.get("day_partial") and facts.get("n_samples", 0) > 0:
        line += f" Данные неполные: покрытие {facts['n_samples']}/{NOMINAL_SAMPLES}."
    return line


def fmt_num(v):
    """Число для шаблона: без хвостовых нулей float, целое — как int."""
    if v is None:
        return "н/д"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def fallback_message(facts, reason):
    """§3.4: шаблон + reason из enum §3.5. v1.0.2: reason в code-backticks —
    живой Bot API парсит Markdown, `_` enum-значений (provider_unreachable /
    provider_5xx) без обёртки даёт 400 «can't parse entities» (находка
    деплоя live; моки §7.2 парсинг Markdown не эмулируют). U8.4: assert на
    enum снят — исторические строки БД могут хранить вердикт
    invalid_numbers (в v1.2.1 из enum удалён), fallback обязан
    рендериться для ЛЮБОГО сохранённого вердикта."""
    return header_markdown(facts) + f" Нарратив недоступен: `{reason}`"


def nodata_message(con, d):
    """Тест 15 §7.2: нет строки v_daily → «данных нет»; строка есть с
    n_samples=0 → «данных нет (0 замеров)» — причины различаются."""
    ddmm = day_label(d)
    present = con.execute("SELECT 1 FROM v_daily WHERE day_epoch=?", (d,)).fetchone()
    if present:
        return f"Отчёт за {ddmm}: данных нет (0 замеров)"
    return f"Отчёт за {ddmm}: данных нет"


# --- §3 доставка ---------------------------------------------------------------
def tg_send(cfg, text, markdown=False):
    """Одна попытка sendMessage. TELEGRAM_API_BASE + /bot<TOKEN>/sendMessage.
    Токен в URL; URL/токен/чат не логируются (§3.1/§6.2)."""
    url = cfg["TELEGRAM_API_BASE"].rstrip("/") + "/bot" + cfg["TELEGRAM_BOT_TOKEN"] \
        + "/sendMessage"
    payload = {"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": text}
    if markdown:
        payload["parse_mode"] = "Markdown"
    timeout = float(os.environ.get("TG_TIMEOUT_S") or TG_TIMEOUT_S)
    # v1.0.1: CF Bot Fight Mode на workers.dev отвечает 403 (error code 1010)
    # на дефолтный Python-urllib UA (находка деплоя live 2026-10-01);
    # нейтральный UA подтверждён живым getMe (200 ok=true).
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "weather-report/" + WEATHER_REPORT_VERSION},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(256).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        raise TGError(f"HTTP {e.code}: {sanitize(detail[:200])}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TGError(sanitize(f"{type(e).__name__}: {e}")) from e
    if not resp.get("ok"):
        raise TGError(sanitize("ok=false: " + str(resp.get("description"))))
    log(f"доставка: сообщение принято (message_id={resp.get('result', {}).get('message_id')})")


def kuma_url():
    """M6: endpoint Kuma-push из kuma_push.conf (строка backup_url=), механизм
    weather_backup.sh. Нет строки/файла — push-и тихо пропускаются. URL с
    токеном мониторинга никогда не логируется."""
    conf = os.environ.get("KUMA_CONF") or KUMA_CONF
    try:
        with open(conf, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("backup_url="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def kuma_push(status, msg):
    """§3.2: один push на переход в failed. Формат weather_backup.sh (M6):
    curl -G "$URL" --data-urlencode status=… --data-urlencode msg=…"""
    url = kuma_url()
    if not url:
        log("Kuma-push пропущен: endpoint не задан")
        return
    from urllib.parse import urlencode
    full = url + ("&" if "?" in url else "?") + urlencode(
        {"status": status, "msg": msg})
    try:
        with urllib.request.urlopen(full, timeout=5) as r:
            r.read(64)
        log(f"Kuma push {status}: {sanitize(msg)}")
    except Exception as e:  # noqa: BLE001 — push не влияет на исход прогона
        log(f"WARN Kuma push не прошёл: {sanitize(e)}")


def deliver(con, row, cfg, msgs):
    """In-run доставка §3.3: до 3 попыток (2/8/30с), таймаут попытки 15с.
    Все неуспешны → pending_retry; TTL 3 прогона, на 4-й неудаче → failed +
    один Kuma push на переход. Обновляет delivery_status/attempts/error.
    U8.3: переход в sent (свежая доставка/восстановление) даёт один
    Kuma ok-push (status=up, тот же endpoint M6) — молчание Kuma =
    инцидент; повторная успешная досылка sent-строки тоже считается
    успешной досылкой и пушит up."""
    rid, d = row["id"], row["day_epoch"]
    prev_status = row["delivery_status"]
    attempts = int(row["delivery_attempts"] or 0)
    scale = float(os.environ.get("WEATHER_REPORT_RETRY_SCALE") or 1.0)
    ok, err_desc = False, None
    for att in range(1, TG_ATTEMPTS + 1):
        try:
            for text, markdown in msgs:
                tg_send(cfg, text, markdown)
            ok = True
            break
        except TGError as e:
            err_desc = str(e)
            log(f"WARN доставка: попытка {att}/{TG_ATTEMPTS} неуспешна: {err_desc}")
            if att < TG_ATTEMPTS:
                time.sleep(TG_BACKOFF_S[att - 1] * scale)
    attempts += 1
    if ok:
        status, err = "sent", None
        log("доставка: sent")
    elif attempts > PENDING_TTL_RUNS:
        status = "failed"
        err = err_desc
        log(f"ERROR доставка: исчерпан TTL ({attempts} прогонов) — failed")
    else:
        status, err = "pending_retry", err_desc
        log(f"доставка: попытки исчерпаны → pending_retry "
            f"(прогон {attempts}, TTL {PENDING_TTL_RUNS})")
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE reports SET delivery_status=?, delivery_attempts=?, "
                "delivery_error=? WHERE id=?",
                (status, attempts, err, rid))
    con.execute("COMMIT")
    if status == "failed" and prev_status != "failed":
        kuma_push("down", f"weather-report failed day_epoch={d} (TTL {PENDING_TTL_RUNS})")
    if status == "sent" and prev_status != "sent":
        # U8.3: ok-push на успешную доставку/восстановление (механизм M6,
        # тот же push-URL): молчание Kuma = инцидент
        kuma_push("up", f"weather-report sent day_epoch={d} (attempts={attempts})")
    return status


# --- §4.3/§4.4: сценарии прогона ----------------------------------------------
def message_for_row(con, row, cfg):
    """Композиция сообщений по rerun-правилам §4.4 + состояния дня:
    -> (msgs [(text, markdown)], llm_update | None).
    llm_update — словарь для UPDATE reports после (повторного) LLM-шага."""
    facts = json.loads(row["facts_json"])
    d = row["day_epoch"]
    if int(facts.get("n_samples") or 0) == 0:
        # пустой день: LLM не вызывается ни при каком rerun (тест 15)
        return [(nodata_message(con, d), False)], None
    if row["llm_text"]:
        # нарратив сохранён — используем его
        return [(header_markdown(facts), True), (row["llm_text"], False)], None
    if row["llm_error"]:
        # отказ известен — LLM не пробуем, сразу fallback (MAJ-5)
        log(f"LLM ранее отказал ({row['llm_error']}) — fallback без нового запроса")
        return [(fallback_message(facts, row["llm_error"]), True)], None
    # NULL/NULL → повторить LLM (крах между INSERT и LLM-запросом)
    try:
        res = call_llm(cfg, facts)
    except LLMRefusal as e:
        log(f"LLM отказ: {e.reason} → fallback-шаблон (enum §3.5)")
        upd = {"llm_text": None, "llm_model": None, "llm_tokens_in": None,
               "llm_tokens_out": None, "llm_error": e.reason}
        return [(fallback_message(facts, e.reason), True)], upd
    upd = {"llm_text": res["llm_text"], "llm_model": res["llm_model"],
           "llm_tokens_in": res["llm_tokens_in"],
           "llm_tokens_out": res["llm_tokens_out"], "llm_error": None}
    return [(header_markdown(facts), True), (res["llm_text"], False)], upd


def apply_llm_update(con, rid, upd):
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE reports SET llm_text=?, llm_model=?, llm_tokens_in=?, "
                "llm_tokens_out=?, llm_error=? WHERE id=?",
                (upd["llm_text"], upd["llm_model"], upd["llm_tokens_in"],
                 upd["llm_tokens_out"], upd["llm_error"], rid))
    con.execute("COMMIT")


def refetch(con, rid):
    return con.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()


def resend_pending(con, cfg, exclude_day=None):
    """§3.3 next-run: СНАЧАЛА досылка всех pending_retry (по одному кругу
    in-run retry на каждый), ПОТОМ — генерация нового отчёта. Строки дня
    exclude_day (день текущей генерации) остаются пути генерации (§4.4
    reuse) — одна доставка на строку за прогон."""
    if exclude_day is None:
        rows = con.execute("SELECT * FROM reports WHERE delivery_status='pending_retry' "
                           "ORDER BY generated_at, id").fetchall()
    else:
        rows = con.execute("SELECT * FROM reports WHERE delivery_status='pending_retry' "
                           "AND day_epoch <> ? ORDER BY generated_at, id",
                           (exclude_day,)).fetchall()
    if rows:
        log(f"досылка pending_retry: {len(rows)} строка(и)")
    for row in rows:
        msgs, upd = message_for_row(con, row, cfg)
        if upd:
            apply_llm_update(con, row["id"], upd)
            row = refetch(con, row["id"])
        deliver(con, row, cfg, msgs)


def run_generation(con, cfg, d, now):
    """Генерация отчёта за D (§4.3 шаги 5–11) с идемпотентностью §4.4."""
    key = f"{d}:{cfg['TELEGRAM_CHAT_ID']}"
    row = con.execute("SELECT * FROM reports WHERE idempotency_key=?", (key,)).fetchone()
    if row is not None:
        st = row["delivery_status"]
        if st == "sent":
            log("отчёт за этот день уже sent — без действий, exit 0 (§4.4)")
            return 0
        if st == "failed":
            log("WARN отчёт failed — восстановление только через --resend "
                "(m-11), exit 0")
            return 0
        log(f"строка за день уже есть ({st}) — повтор доставки (§4.4)")
        msgs, upd = message_for_row(con, row, cfg)
        if upd:
            apply_llm_update(con, row["id"], upd)
            row = refetch(con, row["id"])
        deliver(con, row, cfg, msgs)
        return 0
    # §4.3: факты → деградация → INSERT pending → LLM → статусы
    facts, _ = fetch_facts(con, d)
    if int(facts.get("n_samples") or 0) == 0:
        log("за день нет данных (строки v_daily нет либо 0 замеров) — "
            "нарратив не запрашивается (тест 15)")
    facts_json = degrade_facts(facts)
    con.execute("BEGIN IMMEDIATE")
    cur = con.execute(
        "INSERT INTO reports(day_epoch, generated_at, facts_json, delivery_status, "
        "delivery_attempts, delivery_error, idempotency_key, schema_version) "
        "VALUES(?,?,?,'pending',0,NULL,?,1)", (d, now, facts_json, key))
    rid = cur.lastrowid
    con.execute("COMMIT")
    log(f"reports: INSERT id={rid}, day={d}, facts={facts_json_bytes(facts)}Б, "
        f"status=pending")
    row = refetch(con, rid)
    msgs, upd = message_for_row(con, row, cfg)
    if upd:
        apply_llm_update(con, rid, upd)
        row = refetch(con, rid)
    deliver(con, row, cfg, msgs)
    return 0


def resend_reset(con, rid):
    """Явный --resend: свежий круг TTL (восстановление из failed, m-11)."""
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE reports SET delivery_status='pending', delivery_attempts=0 "
                "WHERE id=?", (rid,))
    con.execute("COMMIT")


def run_resend_day(con, cfg, day_epoch):
    """§5.3 --resend <day_epoch>: переотправка конкретного дня; новой строки
    не создаёт, обновляет существующую (delivery_status, delivery_attempts)."""
    row = con.execute("SELECT * FROM reports WHERE day_epoch=? ORDER BY id DESC "
                      "LIMIT 1", (day_epoch,)).fetchone()
    if row is None:
        log(f"ERROR строки reports для day_epoch={day_epoch} нет — exit 1")
        return 1
    log(f"--resend day_epoch={day_epoch}: строка id={row['id']} "
        f"({row['delivery_status']})")
    resend_reset(con, row["id"])
    row = refetch(con, row["id"])
    msgs, upd = message_for_row(con, row, cfg)
    if upd:
        apply_llm_update(con, row["id"], upd)
        row = refetch(con, row["id"])
    deliver(con, row, cfg, msgs)
    return 0


def run_resend_last(con, cfg):
    """§5.3 --resend-last: последняя строка по day_epoch (любой статус);
    нет строк → exit 1 (m-3/m-18)."""
    row = con.execute("SELECT * FROM reports ORDER BY day_epoch DESC, id DESC "
                      "LIMIT 1").fetchone()
    if row is None:
        log("нет строк в reports — exit 1 (m-3)")
        return 1
    log(f"--resend-last: строка id={row['id']}, day={row['day_epoch']} "
        f"({row['delivery_status']})")
    resend_reset(con, row["id"])
    row = refetch(con, row["id"])
    msgs, upd = message_for_row(con, row, cfg)
    if upd:
        apply_llm_update(con, row["id"], upd)
        row = refetch(con, row["id"])
    deliver(con, row, cfg, msgs)
    return 0


def regenerate_reset(con, rid):
    """U8.1 --regenerate: сброс LLM-вердиктов строки перед повтором:
    llm_text/llm_model/llm_tokens_in/llm_tokens_out/llm_error → NULL;
    доставка — свежий круг TTL (симметрично resend_reset: без сброса
    attempts ручная регенерация sent-строки с attempts=3 при неудаче
    доставки мгновенно получила бы failed + ложный Kuma-push)."""
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE reports SET llm_text=NULL, llm_model=NULL, "
                "llm_tokens_in=NULL, llm_tokens_out=NULL, llm_error=NULL, "
                "delivery_status='pending', delivery_attempts=0 WHERE id=?",
                (rid,))
    con.execute("COMMIT")


def run_regenerate(con, cfg, row):
    """Общий хвост --regenerate*: сброс llm_* → повтор LLM по канону §2
    (message_for_row на NULL/NULL-строке) + доставка §3.3. Строка УЖЕ
    существует — INSERT не выполняется (новых строк нет)."""
    regenerate_reset(con, row["id"])
    row = refetch(con, row["id"])
    msgs, upd = message_for_row(con, row, cfg)
    if upd:
        apply_llm_update(con, row["id"], upd)
        row = refetch(con, row["id"])
    deliver(con, row, cfg, msgs)
    return 0


def run_regenerate_last(con, cfg):
    """§5.3 U8.1 --regenerate-last: повтор LLM-шага последней строки по
    day_epoch (любой статус); нет строк → exit 1; новых строк не создаёт."""
    row = con.execute("SELECT * FROM reports ORDER BY day_epoch DESC, id DESC "
                      "LIMIT 1").fetchone()
    if row is None:
        log("нет строк в reports — exit 1 (m-3)")
        return 1
    log(f"--regenerate-last: строка id={row['id']}, day={row['day_epoch']} "
        f"({row['delivery_status']}, llm_error={row['llm_error']}) — "
        f"повтор LLM-шага")
    return run_regenerate(con, cfg, row)


def run_regenerate_day(con, cfg, day_epoch):
    """§5.3 U8.1 --regenerate <day_epoch>: повтор LLM-шага строки конкретного
    дня; строки нет → exit 1; новых строк не создаёт."""
    row = con.execute("SELECT * FROM reports WHERE day_epoch=? ORDER BY id DESC "
                      "LIMIT 1", (day_epoch,)).fetchone()
    if row is None:
        log(f"ERROR строки reports для day_epoch={day_epoch} нет — exit 1")
        return 1
    log(f"--regenerate day_epoch={day_epoch}: строка id={row['id']} "
        f"({row['delivery_status']}, llm_error={row['llm_error']}) — "
        f"повтор LLM-шага")
    return run_regenerate(con, cfg, row)


def run_retry_pending(con, cfg):
    """U8.3 --retry-pending: ТОЛЬКО досылка всех pending_retry (для
    hourly-таймера weather-report-retry), БЕЗ генерации текущего дня.
    Семантика §3.3 — тот же deliver(): один круг in-run попыток на строку,
    +1 прогон к attempts, TTL/failed/Kuma без изменений; нарратив берётся
    из строки (LLM зовётся только на NULL/NULL). Нет pending_retry →
    без действий, exit 0."""
    rows = con.execute("SELECT * FROM reports WHERE delivery_status='pending_retry' "
                       "ORDER BY generated_at, id").fetchall()
    log(f"--retry-pending: pending_retry строк: {len(rows)}")
    for row in rows:
        msgs, upd = message_for_row(con, row, cfg)
        if upd:
            apply_llm_update(con, row["id"], upd)
            row = refetch(con, row["id"])
        deliver(con, row, cfg, msgs)
    return 0


def run_dry(con, now):
    """§5.3 m-4: --dry-run — без LLM и доставки; печатает facts JSON +
    заполненный шаблон; exit 0. Только чтение (mode=ro, без lock/записей)."""
    d = d_epoch_of(now)
    facts, _ = fetch_facts(con, d)
    print("=== facts JSON ===")
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    print(f"=== размер фактов: {facts_json_bytes(facts)}Б (лимит {FACTS_MAX_BYTES}Б) ===")
    print("=== шаблон доставки (шапка — Telegram Markdown) ===")
    print(header_markdown(facts))
    if int(facts.get("n_samples") or 0) == 0:
        print(nodata_message(con, d))
    else:
        print(" Нарратив недоступен: <{reason} — enum §3.5; LLM в dry-run "
              "не вызывается>")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="weather-report",
        description=f"U8 «Суточный ИИ-отчёт» v{WEATHER_REPORT_VERSION} "
                    f"(спека weather-report v1.1.1)")
    p.add_argument("--dry-run", action="store_true",
                   help="без LLM и доставки: facts JSON + заполненный шаблон")
    p.add_argument("--resend-last", action="store_true",
                   help="переотправить последнюю строку (нет строк → exit 1)")
    p.add_argument("--resend", type=int, metavar="DAY_EPOCH",
                   help="переотправить конкретный день")
    p.add_argument("--regenerate-last", action="store_true",
                   help="повторить LLM-шаг последней строки (U8.1: llm_* → "
                        "NULL, новых строк не создаёт)")
    p.add_argument("--regenerate", type=int, metavar="DAY_EPOCH",
                   help="повторить LLM-шаг строки конкретного дня (U8.1)")
    p.add_argument("--retry-pending", action="store_true",
                   help="только досылка pending_retry, без генерации текущего "
                        "дня (U8.3, для weather-report-retry.timer)")
    p.add_argument("--db", default=os.environ.get("WEATHER_REPORT_DB") or DEFAULT_DB,
                   help=argparse.SUPPRESS)  # тестовые стенды; прод — дефолт
    args = p.parse_args(argv)
    chosen = [args.resend_last, args.resend is not None,
              args.regenerate_last, args.regenerate is not None,
              args.retry_pending]
    if sum(bool(x) for x in chosen) > 1:
        print("флаги --resend-last/--resend/--regenerate-last/--regenerate/"
              "--retry-pending взаимоисключающие", file=sys.stderr)
        return 2

    if args.dry_run:
        # m-4: только чтение — без lock, без записей; whitelist-валидация
        # модели остаётся (fail-fast стандарта §2.1)
        cfg = load_cfg()
        con = open_db(args.db, readonly=True)
        try:
            validate_v_daily(con)
            return run_dry(con, now_epoch())
        finally:
            con.close()

    # §4.3 шаг 1: старт, flock (снятие в finally); nice=19 задаёт юнит
    lock = acquire_lock()
    if lock is None:
        return 0
    try:
        # §4.3 шаг 2: валидация модели ∈ whitelist (до первого запроса)
        cfg = load_cfg()
        con = open_db(args.db)
        # §4.3 шаг 3: PRAGMA v_daily fail-fast; таблица reports обязательна
        validate_v_daily(con)
        ensure_reports(con)
        now = now_epoch()
        d = d_epoch_of(now)
        if args.resend_last:
            return run_resend_last(con, cfg)
        if args.resend is not None:
            return run_resend_day(con, cfg, args.resend)
        if args.regenerate_last:
            return run_regenerate_last(con, cfg)
        if args.regenerate is not None:
            return run_regenerate_day(con, cfg, args.regenerate)
        if args.retry_pending:
            return run_retry_pending(con, cfg)
        # §4.3 шаг 4: досылка pending_retry (чужих дней), затем генерация за D
        resend_pending(con, cfg, exclude_day=d)
        return run_generation(con, cfg, d, now)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — journald фиксирует, юнит = failure
        log("ERROR " + sanitize(f"{type(e).__name__}: {e}"))
        return 1
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock.close()


if __name__ == "__main__":
    log(f"U8 weather-report {WEATHER_REPORT_VERSION} старт")
    sys.exit(main())


