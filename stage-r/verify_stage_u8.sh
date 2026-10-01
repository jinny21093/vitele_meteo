#!/usr/bin/env bash
# verify_stage_u8.sh — смоук U8 «Суточный ИИ-отчёт» (спека weather-report
# v1.1.1 §7.3; образец — deploy-tools/verify_stage_ui.sh: guards, сквозная
# нумерация, режимы).
#
# Режимы (первый параметр):
#   local   — клон репо + приёмочные тесты stage-r/test_u8.py (43 теста:
#             юнит §7.1, 25 сценариев §7.2 на моках, 5 смоуков §7.3) +
#             --dry-run на копии живой БД (SRC_DB) + контроль миграции.
#             Живая БД только ЧИТАЕТСЯ (mode=ro); записи — только в tmp.
#   deploy  — живая VM: ТОЛЬКО read-only проверки (юнит/таймер active,
#             journalctl без секретов и без ERROR, таблица reports и
#             schema_migrations.version=4, --dry-run на живой БД).
#             БЕЗ --resend-last (ушлёт сообщение в Telegram), БЕЗ рестартов,
#             БЕЗ записей в БД.
#
# Примеры:
#   verify_stage_u8.sh local
#   SRC_DB=/home/auditbot/weather-dash/weather.db verify_stage_u8.sh local
#   verify_stage_u8.sh deploy
#
# Exit: 0 — все проверки OK; 1 — есть FAIL.
set -uo pipefail

MODE="${1:-}"
case "$MODE" in
  local|deploy) ;;
  *) echo "usage: $0 local|deploy"; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SRC_DB="${SRC_DB:-/home/auditbot/weather-dash/weather.db}"
UNIT_DIR="$REPO_DIR/stage-r/systemd"
REPORT_PY="$REPO_DIR/stage-r/weather_report.py"
MIGRATION="$REPO_DIR/docs/migrations/004_reports.sql"
SECRET_GREP='api[-]?key|bot[-]?token'   # паттерн §6.2 (как в тестах 13/14)

N=0
FAILS=0
FAILED_LIST=""

ok()   { N=$((N+1)); printf "  OK   U%03d %s\n" "$N" "$1"; }
bad()  { N=$((N+1)); FAILS=$((FAILS+1)); FAILED_LIST="$FAILED_LIST U$(printf '%03d' "$N")";
         printf " FAIL  U%03d %s\n" "$N" "$1"; }
skip() { N=$((N+1)); printf " SKIP  U%03d %s\n" "$N" "$1"; }

echo "== U8 verify: режим $MODE =="
echo "    REPO_DIR=$REPO_DIR"
echo "    SRC_DB=$SRC_DB"

# --- G0. Окружение ---
echo "== G0. Окружение =="
command -v python3 >/dev/null 2>&1 && ok "python3 в PATH" || { bad "python3 отсутствует"; exit 1; }
[[ -f "$REPORT_PY" ]] && ok "weather_report.py на месте" || bad "нет $REPORT_PY"
[[ -f "$MIGRATION" ]] && ok "миграция 004_reports.sql на месте" || bad "нет $MIGRATION"
python3 -m py_compile "$REPORT_PY" && ok "py_compile weather_report.py" \
  || bad "py_compile weather_report.py"

if [[ "$MODE" == "deploy" ]]; then
  command -v systemctl >/dev/null 2>&1 && ok "systemctl в PATH" || bad "systemctl отсутствует"
  command -v journalctl >/dev/null 2>&1 && ok "journalctl в PATH" || bad "journalctl отсутствует"
else
  command -v git >/dev/null 2>&1 && ok "git в PATH" || bad "git отсутствует"
  [[ -d "$REPO_DIR/.git" ]] && ok "репо-клон найден" || bad "нет .git в REPO_DIR"
fi

if [[ "$MODE" == "local" ]]; then
  # --- L1. Приёмочные тесты (43) ---
  echo "== L1. Приёмочные тесты stage-r/test_u8.py (§7.1 юнит + §7.2 + §7.3) =="
  if python3 "$REPO_DIR/stage-r/test_u8.py" > /tmp/u8_verify_tests.log 2>&1; then
    ok "test_u8.py: $(grep -E '^Ran ' /tmp/u8_verify_tests.log), вердикт: $(grep -E '^(OK|FAILED)' /tmp/u8_verify_tests.log | head -1)"
  else
    bad "test_u8.py: есть провалы (лог: /tmp/u8_verify_tests.log)"
  fi

  # --- L2. Секреты в git (§7.2 тест 13) ---
  echo "== L2. Секреты не в git =="
  if git -C "$REPO_DIR" log -p | grep -iqE "$SECRET_GREP"; then
    bad "git log -p содержит секрет-паттерн ($SECRET_GREP)"
  else
    ok "git log -p | grep -iE '$SECRET_GREP' → пусто"
  fi

  # --- L3. --dry-run на копии живой БД (§7.3 п.1) ---
  echo "== L3. --dry-run на копии живой БД (SRC_DB, только чтение) =="
  WORK="$(mktemp -d /tmp/verify_u8.XXXXXX)"
  if [[ -f "$SRC_DB" ]]; then
    ok "исходная БД найдена: $SRC_DB"
    cp "$SRC_DB" "$WORK/dbcopy.db" 2>/dev/null && ok "копия БД снята в tmp" \
      || bad "копия БД не снялась"
    DRY_OUT="$WORK/dryrun.log"
    # dry-run: mode=ro, без LLM/доставки; секреты — тестовые заглушки
    if LLM_API_KEY=dummy LLM_BASE_URL=https://example.invalid \
       LLM_MODEL=glm-4.7-flash TELEGRAM_BOT_TOKEN=000:dummy \
       TELEGRAM_CHAT_ID=000 WEATHER_REPORT_DB="$WORK/dbcopy.db" \
       python3 "$REPORT_PY" --dry-run > "$DRY_OUT" 2>&1; then
      ok "--dry-run exit 0"
      grep -q '"day_epoch": ' "$DRY_OUT" && ok "dry-run печатает facts JSON" \
        || bad "в dry-run нет facts JSON"
      grep -q 'Погода за' "$DRY_OUT" && ok "dry-run печатает шапку-шаблон" \
        || bad "в dry-run нет шапки-шаблона"
      grep -q 'размер фактов' "$DRY_OUT" && ok "dry-run печатает размер фактов" \
        || bad "в dry-run нет размера фактов"
    else
      bad "--dry-run завершился не с 0 (лог: $DRY_OUT)"
    fi
  else
    skip "SRC_DB не найден ($SRC_DB) — dry-run на копии пропущен"
  fi

  # --- L4. Миграция: идемпотентность на tmp-копии ---
  echo "== L4. Миграция 004: применение дважды на tmp-БД =="
  if [[ -f "$SRC_DB" && -f "$WORK/dbcopy.db" ]]; then
    python3 - "$WORK/dbcopy.db" "$MIGRATION" <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.executescript(open(sys.argv[2], encoding="utf-8").read())
con.executescript(open(sys.argv[2], encoding="utf-8").read())  # повторно
v = con.execute("SELECT version, description FROM schema_migrations "
                "WHERE version=4").fetchall()
cols = con.execute("PRAGMA table_info(reports)").fetchall()
print(f"MIG_OK version4={v} cols={len(cols)}")
assert len(v) == 1 and v[0][1] == "U8 reports" and len(cols) == 14
PYEOF
    [[ $? -eq 0 ]] && ok "миграция: v4 однократна, reports 14 колонок, 2-е применение — no-op" \
      || bad "миграция: идемпотентность нарушена"
  else
    skip "миграционный чек пропущен (нет SRC_DB)"
  fi
  rm -rf "$WORK"
fi

if [[ "$MODE" == "deploy" ]]; then
  # --- D1. Юниты (read-only) ---
  echo "== D1. Юниты systemd =="
  systemctl is-active --quiet weather-report.service \
    && ok "weather-report.service active (static — oneshot)" \
    || ok "weather-report.service не запущен (oneshot — норма вне прогона)"
  systemctl is-enabled --quiet weather-report.timer \
    && ok "weather-report.timer enabled" || bad "weather-report.timer не enabled"
  systemctl is-active --quiet weather-report.timer \
    && ok "weather-report.timer active" || bad "weather-report.timer не active"
  systemctl cat weather-report.timer 2>/dev/null | grep -q \
    "OnCalendar=\*-\*-\* 06:50:00 Europe/Moscow" \
    && ok "OnCalendar=*-*-* 06:50:00 Europe/Moscow" \
    || bad "OnCalendar таймера не совпал"
  systemctl cat weather-report.service 2>/dev/null | grep -q "Nice=19" \
    && ok "Nice=19" || bad "Nice=19 не найден"
  systemctl cat weather-report.service 2>/dev/null | grep -q "TimeoutStartSec=300" \
    && ok "TimeoutStartSec=300" || bad "TimeoutStartSec=300 не найден"
  systemctl cat weather-report.service 2>/dev/null | grep -q \
    "EnvironmentFile=/home/auditbot/weather-dash/report.env" \
    && ok "EnvironmentFile=report.env" || bad "EnvironmentFile не найден"
  systemctl cat weather-report.service 2>/dev/null | grep -q \
    "OnFailure=weather-report-alert.service" \
    && ok "OnFailure=weather-report-alert.service" || bad "OnFailure не найден"

  # --- D2. БД: миграция применена (read-only) ---
  echo "== D2. БД: reports / schema_migrations (read-only) =="
  DBCHK="$(python3 - "$SRC_DB" <<'PYEOF'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    cols = len(con.execute("PRAGMA table_info(reports)").fetchall())
    v4 = con.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=4").fetchone()[0]
    print(f"OK cols={cols} v4={v4}")
except Exception as e:
    print(f"FAIL {type(e).__name__}: {e}")
PYEOF
)"
  [[ "$DBCHK" == "OK cols=14 v4=1" ]] && ok "reports: 14 колонок, schema_migrations v4=1" \
    || bad "состояние миграции на живой БД: $DBCHK"

  # --- D3. Журнал: секреты/ERROR (§7.2 тесты 14, §7.3 п.4) ---
  echo "== D3. journalctl weather-report =="
  if journalctl -u weather-report --since "-30 days" 2>/dev/null \
      | grep -iqE "$SECRET_GREP"; then
    bad "журнал содержит секрет-паттерн ($SECRET_GREP)"
  else
    ok "journalctl | grep -iE '$SECRET_GREP' → пусто"
  fi
  ERRN="$(journalctl -u weather-report --since "-30 days" 2>/dev/null | grep -c ERROR || true)"
  [[ "${ERRN:-0}" -eq 0 ]] && ok "ERROR в журнале: 0" \
    || bad "ERROR в журнале: $ERRN"

  # --- D4. --dry-run на живой БД (read-only, §7.3 п.1) ---
  echo "== D4. --dry-run на живой БД =="
  if LLM_API_KEY=dummy LLM_BASE_URL=https://example.invalid \
     LLM_MODEL=glm-4.7-flash TELEGRAM_BOT_TOKEN=000:dummy \
     TELEGRAM_CHAT_ID=000 \
     python3 "$REPORT_PY" --dry-run > /tmp/u8_verify_dry_deploy.log 2>&1; then
    ok "deploy --dry-run exit 0 (без LLM и доставки)"
    grep -q '"day_epoch": ' /tmp/u8_verify_dry_deploy.log \
      && ok "facts JSON собран с живой БД" || bad "facts JSON пуст"
  else
    bad "deploy --dry-run не прошёл (лог: /tmp/u8_verify_dry_deploy.log)"
  fi

  # --- D5. --resend-last: сознательный SKIP ---
  skip "--resend-last в deploy не выполняется (ушлёт сообщение владельцу; §7.3 п.3 проверяется в local)"
fi

echo "== ИТОГО: $N проверок, FAILS=$FAILS =="
[[ -n "$FAILED_LIST" ]] && echo "   провалы:$FAILED_LIST"
[[ "$FAILS" -eq 0 ]] && echo "VERIFY_U8: ALL PASSED" || echo "VERIFY_U8: FAILED"
exit $(( FAILS > 0 ? 1 : 0 ))
