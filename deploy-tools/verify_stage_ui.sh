#!/usr/bin/env bash
# verify_stage_ui.sh — упаковка смоука weather-ui (U7-5, спека v1.2.7 §11;
# U6-расширение — спека v1.2.8 §11/§5.9/§5.10/§5.11).
#
# Режимы (первый параметр):
#   local       — клон репо + копия живой БД: тестовый server.py на 127.0.0.1
#                 (порт TEST_PORT, по умолч. 8199); полный прогон, вкл.
#                 фикстуры НА КОПИИ, empty-DB -> 503 initMode. Основной сервер
#                 стартует с --export-max-bytes 100000 (U6-T1: 413-путь с
#                 малым лимитом), empty-сервер — с --check-timeout 0 (U6-T2:
#                 таймаут-путь).
#   deploy      — живой сервер: ТОЛЬКО read-only GET-проверки (health,
#                 версия, страницы, заголовки, DOM-контейнеры, API-конверты,
#                 вербы). БЕЗ инъекций в живую БД, БЕЗ рестартов, БЕЗ POST
#                 (check-db в deploy не шлётся — U7-правило сохраняется).
#   unit-test   — останавливает юнит weather-ui, гоняет полный прогон на
#                 КОПИИ БД (как local), стартует юнит обратно и проверяет
#                 active + слушатели (заодно — упражнение restart-логики).
#
# ВЕРСИОННЫЙ ГЕЙТ U6 (v1.2.8): U6-проверки (settings/export/check-db/DOM)
# активны только когда Server-заголовок целевого сервера >= 0.5.0; иначе —
# SKIP с WARN (деплой U6 выполняется ПОСЛЕ приёмки новым деплой-скриптом,
# который сам прогонит verify). EXPECT_SERVER_VERSION/EXPECT_UI_VERSION —
# env-оверрайды: pre-acceptance deploy-прогон против 0.4.1 запускается как
#   EXPECT_SERVER_VERSION=0.4.1 EXPECT_UI_VERSION=0.4.0 verify_stage_ui.sh deploy
# после деплоя 0.5.0 — дефолты (0.5.0/0.5.0).
#
# Креды (deploy): UI_USER/UI_PASS через env (§11) ЛИБО UI_CRED_FILE
# (файл формата "user:pass", по умолч. /home/auditbot/.weather-ui-credentials).
# Пароль только через env/файл — в репо и в лог не попадает.
#
# Примеры:
#   verify_stage_ui.sh local
#   verify_stage_ui.sh deploy http://192.168.8.146:8089
#   verify_stage_ui.sh unit-test
#
# Exit: 0 — все проверки OK; 1 — есть FAIL (exit по финальному счётчику).
set -uo pipefail

MODE="${1:-}"
case "$MODE" in
  local|deploy|unit-test) ;;
  *) echo "usage: $0 local|deploy|unit-test [BASE_URL]"; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SRC_DB="${SRC_DB:-/home/auditbot/weather-dash/weather.db}"
TEST_PORT="${TEST_PORT:-8199}"
BASE="${2:-${VERIFY_BASE:-}}"
EXPECT_SERVER_VERSION="${EXPECT_SERVER_VERSION:-0.5.0}"
EXPECT_UI_VERSION="${EXPECT_UI_VERSION:-0.5.0}"
WORK="$(mktemp -d /tmp/verify_ui.XXXXXX)"
TMPJSON="$WORK/body.json"
UNIT_WAS_ACTIVE=0

# --- счётчик и сквозная нумерация V001+ (циркульные кончились на U4) ---
N=0
FAILS=0
FAILED_LIST=""

ok()   { N=$((N+1)); printf "  OK   V%03d %s\n" "$N" "$1"; }
bad()  { N=$((N+1)); FAILS=$((FAILS+1)); FAILED_LIST="$FAILED_LIST V$(printf '%03d' "$N")";
         printf " FAIL  V%03d %s\n" "$N" "$1"; }
skip() { N=$((N+1)); printf " SKIP  V%03d %s\n" "$N" "$1"; }
warn() { N=$((N+1)); printf " WARN  V%03d %s\n" "$N" "$1"; }

assert_eq() { # name got want
  if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1 (got: '$2', want: '$3')"; fi
}
assert_contains() { # name haystack needle
  if [[ "$2" == *"$3"* ]]; then ok "$1"; else bad "$1 (не найдено: '$3')"; fi
}
assert_jq() { # name json filter
  local err
  if err=$(printf '%s' "$2" | jq -e "$3" 2>&1); then ok "$1"; else bad "$1 (jq: $err)"; fi
}

# curl: код в $RC, тело в $TMPJSON
RC=""
cget() { RC=$(curl -s -o "$TMPJSON" -w '%{http_code}' --max-time 8 "$@" 2>/dev/null); }

# --- guard'ы окружения (как в §11 спеки) ---
echo "== G0. Окружение =="
command -v curl >/dev/null 2>&1 && ok "curl в PATH" || { bad "curl отсутствует"; exit 1; }
command -v jq >/dev/null 2>&1 && ok "jq в PATH" || { bad "jq отсутствует"; exit 1; }

AUTH=()
UI_USER=""
UI_PASS=""
if [[ "$MODE" == "deploy" ]]; then
  if [[ -n "${UI_PASS:-}" ]]; then
    UI_USER="${UI_USER:-weather}"
    ok "креды из env UI_USER/UI_PASS"
  elif [[ -n "${UI_CRED_FILE:-}" && -f "$UI_CRED_FILE" ]]; then
    IFS=: read -r UI_USER UI_PASS < "$UI_CRED_FILE"
    ok "креды из UI_CRED_FILE=$UI_CRED_FILE"
  elif [[ -f /home/auditbot/.weather-ui-credentials ]]; then
    IFS=: read -r UI_USER UI_PASS < /home/auditbot/.weather-ui-credentials
    ok "креды из дефолта /home/auditbot/.weather-ui-credentials"
  else
    bad "креды не заданы (UI_PASS env или UI_CRED_FILE)"; exit 1
  fi
  AUTH=(-u "$UI_USER:$UI_PASS")
  BASE="${BASE:-http://127.0.0.1:8089}"
elif [[ "$MODE" == "unit-test" ]]; then
  command -v systemctl >/dev/null 2>&1 && ok "systemctl в PATH" || bad "systemctl отсутствует"
  # БЕЗ grep -q в пайпе: pipefail + ранний выход grep -> SIGPIPE у systemctl -> ложный FAIL
  [[ -n "$(systemctl list-unit-files 2>/dev/null | grep '^weather-ui\.service')" ]] \
    && ok "юнит weather-ui.service установлен" || bad "юнит weather-ui.service не найден"
fi
if [[ "$MODE" != "deploy" ]]; then
  command -v sqlite3 >/dev/null 2>&1 && ok "sqlite3 в PATH" || bad "sqlite3 отсутствует"
  command -v python3 >/dev/null 2>&1 && ok "python3 в PATH" || bad "python3 отсутствует"
  [[ -f "$SRC_DB" ]] && ok "исходная БД найдена: $SRC_DB" || bad "нет SRC_DB: $SRC_DB"
fi

UI_TEST="$WORK/ui"

cleanup() {
  [[ -f "$WORK/server.pid" ]] && kill "$(cat "$WORK/server.pid")" 2>/dev/null
  [[ -f "$WORK/server2.pid" ]] && kill "$(cat "$WORK/server2.pid")" 2>/dev/null
  if [[ "$MODE" == "unit-test" && "$UNIT_WAS_ACTIVE" == "1" ]]; then
    sudo systemctl start weather-ui 2>/dev/null
    if systemctl is-active weather-ui >/dev/null 2>&1; then
      echo "[i] юнит weather-ui возвращён в active"
    else
      echo "[!] ВНИМАНИЕ: юнит weather-ui не поднялся — проверить вручную"
    fi
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

write_test_config() { # $1=uidir $2=db $3=port
  cat > "$1/config.py" <<EOF
DB_PATH = "$2"
BIND_HOSTS = ("127.0.0.1",)
PORT = $3
PORT_FALLBACK = $3
CRED_FILE = "$WORK/cred"
REALM = "Weather"
RATE_LIMIT = 10
RATE_WINDOW = 60
MAX_CONCURRENT = 20
HANDLER_TIMEOUT = 10
JSON_MAX_BYTES = 10485760
EXPORT_MAX_BYTES = 314572800
CHECK_DB_TIMEOUT = 60
CHECK_DB_RATE = 1
CHECK_DB_RATE_WINDOW = 60
BASE_DIR = "$1"
STATIC_ROOT = "$UI_TEST/static"
TZ_FALLBACK = 10800
EOF
}

prepare_env() { # $1=uidir $2=db $3=port
  printf 'weather:verify-pass-123\n' > "$WORK/cred" && chmod 600 "$WORK/cred"
  write_test_config "$1" "$2" "$3"
}

start_server() { # $1=uidir $2=port $3=pidfile  (EXTRA_SERVER_ARGS — доп. аргументы)
  # пре-килл: слушатели порта от прошлых прогонов (сироты с удалённой БД
  # отвечали 503 и отравляли прогон — урок v0.4.1-verify)
  local orph
  orph=$(ss -tlnp 2>/dev/null | grep ":$2 " | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
  if [[ -n "$orph" ]]; then
    kill $orph 2>/dev/null
    sleep 0.6
  fi
  # exec-паттерн: субшелл заменяется python'ом -> $! = реальный PID
  # (иначе pidfile ловил PID субшелла и cleanup-kill промахивался)
  ( cd "$1" && exec nohup python3 server.py ${EXTRA_SERVER_ARGS:-} > "$WORK/server_$2.log" 2>&1 ) &
  echo $! > "$WORK/$3"
  local i
  for i in $(seq 1 60); do
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$2/api/health" 2>/dev/null && break
    sleep 0.25
  done
  if grep -q 'ERROR bind failed' "$WORK/server_$2.log" 2>/dev/null; then
    return 1   # порт занят/бинд не удался — не верить ответам чужого процесса
  fi
  curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$2/api/health" 2>/dev/null
}

FIXTURES=0
NOW=0
if [[ "$MODE" != "deploy" ]]; then
  echo "== G1. Подготовка стенда (копия БД + тестовый config) =="
  mkdir -p "$UI_TEST"
  cp "$REPO_DIR/ui/server.py" "$REPO_DIR/ui/config.py" "$UI_TEST/" || { bad "копирование ui/"; exit 1; }
  cp -r "$REPO_DIR/ui/static" "$UI_TEST/static" || { bad "копирование static/"; exit 1; }
  sqlite3 "$SRC_DB" ".backup '$WORK/test.db'" || { bad ".backup копия БД"; exit 1; }
  # --- фикстуры ТОЛЬКО на копии (урок deploy-режима: живая БД не трогается) ---
  sqlite3 "$WORK/test.db" "ALTER TABLE forecast ADD COLUMN letter TEXT;" 2>/dev/null
  ISSUED=$(sqlite3 "$WORK/test.db" "SELECT MAX(issued_at) FROM forecast;" 2>/dev/null)
  WMAX=$(sqlite3 "$WORK/test.db" "SELECT MAX(ts) FROM weather;" 2>/dev/null)
  # U6-T1: CSV-injection фикстуры на КОПИИ (§5.9: TEXT-колонки -> апостроф)
  HMAX=$(sqlite3 "$WORK/test.db" "SELECT MAX(hour_epoch) FROM v_hourly;" 2>/dev/null)
  HMAX_FIX=0; WMAX_FIX=0
  if [[ -n "$HMAX" && "$HMAX" != "NULL" ]]; then
    sqlite3 "$WORK/test.db" "UPDATE v_hourly SET wind_dir_mode='=1+2' WHERE hour_epoch=$HMAX;"
    HMAX_FIX=1
  fi
  if [[ -n "$WMAX" && "$WMAX" != "NULL" ]]; then
    sqlite3 "$WORK/test.db" "UPDATE weather SET battery_raw='=1+1' WHERE ts=$WMAX;"
    WMAX_FIX=1
  fi
  if [[ -n "$ISSUED" && "$ISSUED" != "NULL" && -n "$WMAX" && "$WMAX" != "NULL" ]]; then
    # буква + значения zambretti-строки последнего прогона
    sqlite3 "$WORK/test.db" "UPDATE forecast SET letter='B', t_out_c=12.0, p_rel_mmhg=772.0
      WHERE issued_at=$ISSUED AND source='zambretti';"
    # база Δ: замер СТРОГО последний по времени (реальная погода плотная, поэтому
    # issued_at уводим за фикстурный замер — иначе последний реальный замер
    # забьёт фикстуру в SELECT ts <= issued_at)
    TS_FIX=$((WMAX+60))
    NEW_ISSUED=$((TS_FIX+60))
    sqlite3 "$WORK/test.db" "INSERT INTO weather (ts, outdoor_temp_c, outdoor_hum_pct,
      indoor_temp_c, pressure_rel_mmhg, wind_ms) VALUES ($TS_FIX, 10.0, 80.0, 20.0, 770.0, 1.2);"
    sqlite3 "$WORK/test.db" "UPDATE forecast SET issued_at=$NEW_ISSUED WHERE issued_at=$ISSUED;"
    FIXTURES=1
  fi
  NOW=$(date +%s)
  sqlite3 "$WORK/test.db" "INSERT INTO events (ts_start, ts_end, event_type, severity, value, context)
    VALUES ($NOW-600, NULL, 'SENSOR_MISSING', 'mid', 700, '{\"prev_ts\": $NOW}');"
  prepare_env "$UI_TEST" "$WORK/test.db" "$TEST_PORT"
  # U6-T1: малый лимит экспорта на ОСНОВНОМ сервере — 413-путь покрывается
  # без тяжёлой фикстуры (fix(u6): CLI-оверрайд EXPORT_MAX_BYTES)
  EXTRA_SERVER_ARGS="--export-max-bytes 100000"
  if start_server "$UI_TEST" "$TEST_PORT" "server.pid"; then
    ok "тестовый server.py поднялся на 127.0.0.1:$TEST_PORT (копия БД)"
  else
    bad "тестовый server.py не поднялся (лог $WORK/server_$TEST_PORT.log)"; exit 1
  fi
  BASE="http://127.0.0.1:$TEST_PORT"
  AUTH=(-u weather:verify-pass-123)
fi

if [[ "$MODE" == "unit-test" ]]; then
  echo "== G2. Остановка юнита (полный прогон на копии — без помех) =="
  if systemctl is-active --quiet weather-ui; then
    UNIT_WAS_ACTIVE=1
    if sudo systemctl stop weather-ui; then
      ok "юнит остановлен (systemctl stop weather-ui)"
    else
      bad "systemctl stop weather-ui"
    fi
  else
    skip "юнит не был active — stop пропущен"
  fi
fi

B="$BASE"
# --- ВЕРСИОННЫЙ ГЕЙТ U6 (v1.2.8): U6-проверки — только на сервере >= 0.5.0 ---
SRVHDR=$(curl -s -D - -o /dev/null --max-time 8 "${AUTH[@]}" "$B/api/now" 2>/dev/null | tr -d '\r' | grep -i '^Server:' | head -1)
# Нюанс: BaseHTTPRequestHandler.version_string() = server_version + ' ' + sys_version,
# при sys_version="" хвостовой ПРОБЕЛ остаётся — sed допускает [[:space:]]*$
SRVVER=$(printf '%s' "$SRVHDR" | sed -n 's/^[Ss]erver: weather-ui\/\([0-9.]*\)[[:space:]]*$/\1/p')
U6_LIVE=0
if [[ -n "$SRVVER" ]] && [[ "$(printf '%s\n%s\n' "0.5.0" "$SRVVER" | sort -V | tail -1)" == "$SRVVER" ]]; then
  U6_LIVE=1
  ok "версия сервера $SRVVER >= 0.5.0 — U6-проверки активны"
else
  warn "версия сервера ${SRVVER:-неизвестна} < 0.5.0 — U6-проверки будут SKIP (деплой U6 после приёмки)"
fi

echo "== G3. Health и auth =="
cget "$B/api/health"
assert_eq "health 200 без auth" "$RC" "200"
assert_jq "health JSON: status=ok" "$(cat "$TMPJSON")" '.status == "ok"'
HDR401=$(curl -s -D - -o /dev/null --max-time 8 "$B/api/now" | tr -d '\r')
assert_contains "401 /api/now без auth содержит WWW-Authenticate" "$HDR401" 'WWW-Authenticate: Basic realm="Weather"'
assert_contains "401 несёт charset=UTF-8 в realm (диалог в браузере)" "$HDR401" 'charset="UTF-8"'
cget -u wrong:wrong "$B/api/now"
assert_eq "/api/now неверный пароль -> 401" "$RC" "401"
cget "${AUTH[@]}" "$B/api/now"
assert_eq "/api/now с auth -> 200" "$RC" "200"
assert_jq "/api/now: .current.ts != null (§5.1: payload в .current)" "$(cat "$TMPJSON")" '.current.ts != null'

echo "== G4. Страницы и версия =="
for p in / /day /month /events /forecast /settings; do
  cget "${AUTH[@]}" "$B$p"
  assert_eq "страница $p -> 200" "$RC" "200"
done
cget "${AUTH[@]}" "$B/static/app.js"
assert_contains "app.js: UI_VERSION = \"$EXPECT_UI_VERSION\"" "$(cat "$TMPJSON")" "UI_VERSION = \"$EXPECT_UI_VERSION\""
cget "${AUTH[@]}" "$B/static/page-forecast.js"
assert_contains "page-forecast.js содержит issued_values (база Δ)" "$(cat "$TMPJSON")" "issued_values"
if [[ "$U6_LIVE" == "1" ]]; then
  cget "${AUTH[@]}" "$B/static/page-settings.js"
  assert_contains "page-settings.js содержит /api/settings (U6)" "$(cat "$TMPJSON")" "/api/settings"
else
  skip "page-settings.js: сервер < 0.5.0 (U6 не деплоен)"
fi

echo "== G5. Заголовки (CSP на всех 6 страницах, no-cache, nosniff) =="
for p in / /day /month /events /forecast /settings; do
  HDR=$(curl -s -D - -o /dev/null --max-time 8 "${AUTH[@]}" "$B$p" | tr -d '\r')
  assert_contains "CSP на $p" "$HDR" "Content-Security-Policy:"
done
HDR=$(curl -s -D - -o /dev/null --max-time 8 "${AUTH[@]}" "$B/static/app.js" | tr -d '\r')
assert_contains "app.js Cache-Control no-cache (M-3)" "$HDR" "no-cache"
HDR=$(curl -s -D - -o /dev/null --max-time 8 "${AUTH[@]}" "$B/" | tr -d '\r')
assert_contains "nosniff на /" "$HDR" "X-Content-Type-Options: nosniff"
SRV=$(printf '%s' "$HDR" | grep -i '^Server:' | head -1)
assert_contains "Server: weather-ui/$EXPECT_SERVER_VERSION" "$SRV" "weather-ui/$EXPECT_SERVER_VERSION"

echo "== G6. DOM-контейнеры страниц (урок events.html-эпизода) =="
grep_page() { # name page needle
  cget "${AUTH[@]}" "$B$2"
  assert_contains "$1 [$2 id=\"$3\"]" "$(cat "$TMPJSON")" "id=\"$3\""
}
grep_page "index: сетка карточек"     /        "cards"
grep_page "index: карточка улицы"     /        "card-outdoor"
grep_page "index: спарклайн T"        /        "spark-t"
grep_page "day: график температуры"   /day     "chart-t"
grep_page "day: график давления"      /day     "chart-p"
grep_page "day: график дождя"         /day     "chart-rain"
grep_page "day: график солнца"        /day     "chart-sun"
grep_page "day: график ветра"         /day     "chart-wind"
grep_page "day: таблица статистики"   /day     "stat-body"
grep_page "month: heatmap-сетка"      /month   "hm-grid"
grep_page "month: легенда heatmap"    /month   "hm-legend"
grep_page "month: календарь"          /month   "cal-box"
grep_page "month: тренд-график"       /month   "chart-trend"
grep_page "events: таймлайн"          /events  "timeline"
grep_page "events: чипы типов"        /events  "type-chips"
grep_page "events: чипы severity"     /events  "sev-chips"
grep_page "forecast: init-плашка"     /forecast "fc-init"
grep_page "forecast: блок данных"     /forecast "fc-data"
grep_page "forecast: замбретти-блок"  /forecast "fc-zam"
grep_page "forecast: таблица"         /forecast "fc-table"
grep_page "forecast: сагер-блок"      /forecast "fc-sager"
grep_page "forecast: свежесть"        /forecast "fc-fresh"
if [[ "$U6_LIVE" == "1" ]]; then
  grep_page "settings: карточка экспорта"   /settings "set-export"
  grep_page "settings: кнопка экспорта"     /settings "set-export-btn"
  grep_page "settings: wmeta-блок"          /settings "set-wmeta"
  grep_page "settings: блок схемы"          /settings "set-mig"
  grep_page "settings: журнал коллектора"   /settings "set-clog"
  grep_page "settings: db_health-блок"      /settings "set-dbh"
  grep_page "settings: кнопка проверки БД"  /settings "set-check-btn"
  grep_page "settings: модалка результата"  /settings "set-modal"
else
  skip "settings DOM-грепы: сервер < 0.5.0 (U6 не деплоен)"
fi

echo "== G7. API-конверты (read-only) =="
NOWS=$(date +%s)
cget "${AUTH[@]}" "$B/api/hourly?from=$((NOWS-7*86400))&to=$NOWS"
assert_eq "/api/hourly окно 7д -> 200" "$RC" "200"
assert_jq "/api/hourly: конверт {from,to,rows}" "$(cat "$TMPJSON")" 'has("from") and has("to") and has("rows")'
cget "${AUTH[@]}" "$B/api/history?from=$((NOWS-8*86400))&to=$NOWS"
assert_eq "/api/history окно 8д -> 400 (лимит 7д, §11)" "$RC" "400"
cget "${AUTH[@]}" "$B/api/daily?from=$((NOWS-30*86400))&to=$NOWS"
assert_eq "/api/daily окно 30д -> 200" "$RC" "200"
assert_jq "/api/daily: конверт {from,to,rows}" "$(cat "$TMPJSON")" 'has("from") and has("to") and has("rows")'
cget "${AUTH[@]}" "$B/api/events?from=$((NOWS-7*86400))&to=$NOWS"
assert_eq "/api/events окно 7д -> 200" "$RC" "200"
assert_jq "/api/events: контракт {from,to,rows,truncated}" "$(cat "$TMPJSON")" 'has("from") and has("to") and has("rows") and has("truncated")'
cget "${AUTH[@]}" "$B/api/forecast"
assert_eq "/api/forecast -> 200" "$RC" "200"
FC="$(cat "$TMPJSON")"
assert_jq "/api/forecast: available:true" "$FC" '.available == true'
assert_jq "/api/forecast: ключ issued_values (v0.4.0)" "$FC" 'has("issued_values")'
assert_jq "/api/forecast: zambretti.letter ключ" "$FC" '.zambretti | has("letter")'
assert_jq "/api/forecast: persistence x3 (1/3/6 ч; 24 ч отклонён)" "$FC" '(.persistence | length) == 3'
assert_jq "/api/forecast: calc_ts не null" "$FC" '.calc_ts != null'
cget "${AUTH[@]}" "$B/api/meta"
assert_jq "/api/meta: db_health присутствует (почва U6/U7-8)" "$(cat "$TMPJSON")" 'has("db_health")'
# --- U6-S1: /api/export.csv (§5.9 v1.2.8; версионный гейт — v1.2.7-заглушка 404 только на серверах < 0.5.0) ---
if [[ "$U6_LIVE" == "1" ]]; then
  if [[ "$MODE" == "deploy" ]]; then
    EXPWIN=7200        # deploy: лёгкое окно 2 ч — минимальная нагрузка на прод
  else
    EXPWIN=3600        # local/unit-test: малое окно (основной сервер несёт --export-max-bytes 100000)
  fi
  CODE=$(curl -s -D "$WORK/exp.h" -o "$WORK/exp.csv" -w '%{http_code}' --max-time 8 "${AUTH[@]}" "$B/api/export.csv?from=$((NOWS-EXPWIN))&to=$NOWS")
  assert_eq "export.csv малое окно -> 200 (U6-S1)" "$CODE" "200"
  EXP_H=$(tr -d '\r' < "$WORK/exp.h")
  assert_contains "export.csv: Transfer-Encoding chunked" "$EXP_H" "Transfer-Encoding: chunked"
  if printf '%s' "$EXP_H" | grep -qi '^Content-Length:'; then
    bad "export.csv: Content-Length отсутствует (chunked §5.9)"
  else
    ok "export.csv: Content-Length отсутствует"
  fi
  assert_contains "export.csv: Content-Type text/csv; charset=utf-8" "$EXP_H" "text/csv; charset=utf-8"
  assert_contains "export.csv: Cache-Control no-store" "$EXP_H" "no-store"
  assert_contains "export.csv: filename weather-history-*" "$EXP_H" 'filename="weather-history-'
  assert_contains "export.csv: заголовок первой строкой (separator=; дефолт, RFC 4180)" "$(head -1 "$WORK/exp.csv")" "ts;"
  cget "${AUTH[@]}" "$B/api/export.csv?from=$((NOWS-3600))&to=$NOWS&fields=ts,nope"
  assert_eq "export.csv: fields неизвестное -> 400" "$RC" "400"
  cget "${AUTH[@]}" "$B/api/export.csv?from=$((NOWS-3600))&to=$NOWS&type=bogus"
  assert_eq "export.csv: type bogus -> 400" "$RC" "400"
  cget "${AUTH[@]}" "$B/api/export.csv?from=$((NOWS-3600))&to=$NOWS&separator=%7C"
  assert_eq "export.csv: separator | -> 400 (whitelist ;|,)" "$RC" "400"
else
  cget "${AUTH[@]}" "$B/api/export.csv"
  assert_eq "/api/export.csv -> 404-заглушка (сервер < 0.5.0, почва U6)" "$RC" "404"
fi

echo "== G8. Вердикты методов и обход пути =="
HDR=$(curl -s -D - -o /dev/null --max-time 8 -X POST "${AUTH[@]}" "$B/api/now" | tr -d '\r')
assert_contains "POST с auth -> 405 (§3)" "$HDR" "405"
assert_contains "POST 405 несёт Connection: close" "$HDR" "Connection: close"
cget -X POST "$B/api/now"
assert_eq "POST без auth -> 401 (m-12)" "$RC" "401"
HCODE=$(curl -s --head -o /dev/null -w '%{http_code}' --max-time 8 "$B/api/health")
assert_eq "HEAD -> 501 (не-GET/POST не поддерживаются, U7-6)" "$HCODE" "501"
cget --path-as-is "${AUTH[@]}" "$B/static/../server.py"
assert_eq "обход пути /static/../server.py -> 404" "$RC" "404"
cget --path-as-is "${AUTH[@]}" "$B/static/%2e%2e/server.py"
assert_eq "обход пути encoded -> 404" "$RC" "404"

echo "== G14. U6: GET /api/settings + POST /api/check-db (версионный гейт, v1.2.8 §5.10/§5.11) =="
if [[ "$U6_LIVE" == "1" ]]; then
  cget "${AUTH[@]}" "$B/api/settings"
  assert_eq "/api/settings -> 200" "$RC" "200"
  assert_jq "/api/settings: конверт {wmeta, schema_migrations, collector_log, db_health}" "$(cat "$TMPJSON")" 'has("wmeta") and has("schema_migrations") and has("collector_log") and has("db_health")'
  assert_jq "/api/settings: фильтр wmeta — station_ip НЕ отдаётся (§5.10, сервер-фильтр)" "$(cat "$TMPJSON")" '(.wmeta | has("station_ip")) == false'
  assert_jq "/api/settings: фильтр wmeta — station_mac НЕ отдаётся" "$(cat "$TMPJSON")" '(.wmeta | has("station_mac")) == false'
  assert_jq "/api/settings: whitelist-ключи присутствуют (units или tz_offset_seconds)" "$(cat "$TMPJSON")" '(.wmeta | has("units")) or (.wmeta | has("tz_offset_seconds"))'
else
  skip "/api/settings: сервер < 0.5.0 (U6 не деплоен)"
fi
if [[ "$MODE" == "deploy" ]]; then
  skip "POST /api/check-db: deploy-режим = только GET (U7-правило сохраняется; после деплоя смоук кнопкой/вручную)"
elif [[ "$U6_LIVE" == "1" ]]; then
  cget -X POST "${AUTH[@]}" "$B/api/check-db"
  assert_eq "POST check-db -> 200 (quick_check, копия БД)" "$RC" "200"
  assert_jq "check-db: status ok" "$(cat "$TMPJSON")" '.status == "ok"'
  cget -X POST "${AUTH[@]}" "$B/api/check-db"
  assert_eq "второй POST в пределах минуты -> 429 (rate 1/мин, U6-T2)" "$RC" "429"
  cget -X POST "$B/api/check-db"
  assert_eq "POST check-db без auth -> 401 (m-12)" "$RC" "401"
else
  skip "POST /api/check-db: сервер < 0.5.0"
fi

echo "== G9. Слушатели живого сервера (только deploy на VM) =="
if [[ "$MODE" == "deploy" ]] && command -v ss >/dev/null 2>&1 && [[ -d /home/auditbot/weather-dash/ui ]]; then
  SSOUT=$(ss -tln 2>/dev/null | grep ':8089 ' || true)
  assert_contains "слушатель 127.0.0.1:8089 (loopback, v0.4.1)" "$SSOUT" "127.0.0.1:8089"
  assert_contains "слушатель LAN 192.168.8.146:8089" "$SSOUT" "192.168.8.146:8089"
  assert_contains "слушатель ZT 10.147.17.101:8089" "$SSOUT" "10.147.17.101:8089"
  if printf '%s' "$SSOUT" | grep -qE '0\.0\.0\.0:8089|\*:8089'; then
    bad "найден бинд 0.0.0.0/*:8089 — запрещено §2.3"
  else
    ok "нет бинда 0.0.0.0/*:8089"
  fi
else
  skip "G9 вне контекста deploy-on-VM"
fi

echo "== G12. Дрейф runtime-vs-repo (deploy на VM; А5 ревьюера — урок Х-1) =="
# Манифест = md5 файлов РЕПО на момент коммита этого скрипта. Совпало -> ok;
# не совпало -> WARN-строка с именем файла (НЕ FAIL: либо синк на VM забыт —
# файл runtime != репо, либо манифест отстал от свежего коммита — оба случая
# требуют взгляда). Пересборка манифеста: md5sum stage-a/*.py stage-b/*.py
# ui/server.py ui/config.py ui/static/app.js ui/static/page-forecast.js
# ui/weather-ui.service. Синк расходящихся: deploy-tools/weather_ui_deploy_v041.py
if [[ "$MODE" == "deploy" ]]; then
  drift() { # relpath runtime_path expected_md5
    if [[ ! -f "$2" ]]; then skip "дрейф $1: на VM нет $2"; return; fi
    local got; got=$(md5sum "$2" | cut -d' ' -f1)
    if [[ "$got" == "$3" ]]; then
      ok "md5 $1 = репо"
    else
      warn "ДРЕЙФ $1: runtime ${got:0:10} != репо ${3:0:10} (синк не выполнен — см. weather_ui_deploy_v041.py, или манифест устарел)"
    fi
  }
  DASHDIR=/home/auditbot/weather-dash
  drift stage-a/migrate_v1_v2.py     "$DASHDIR/migrate_v1_v2.py"            248f872558f99fe25330b8ee2106a618
  drift stage-a/weather_collector.py "$DASHDIR/weather_collector.py"        d36d974cd623d05d1ba0009cc4881448
  drift stage-b/weather_aggregator.py "$DASHDIR/weather_aggregator.py"      1fb40948b5d2713e681e3c7970333f31
  drift stage-b/weather_api.py       "$DASHDIR/weather_api.py"              2ebcef7dd11b9fdd05628ffe64a66633
  drift stage-b/weather_zam.py       "$DASHDIR/weather_zam.py"              f12fe675848094db37b9a52cf1b79026
  drift ui/server.py                 "$DASHDIR/ui/server.py"                95acbc040501694bbfdb5575b7ca070e
  drift ui/config.py                 "$DASHDIR/ui/config.py"                0bcfbe1faa563bbd4f65725071cb7b86
  drift ui/static/app.js             "$DASHDIR/ui/static/app.js"            7c6adcc7edc00e35f3a7a9ecfbb294ae
  drift ui/static/page-forecast.js   "$DASHDIR/ui/static/page-forecast.js"  23a73e00e330b55c3711772abae0401d
  drift ui/static/settings.html      "$DASHDIR/ui/static/settings.html"     1ad49448801bd91376cc77cbe443ab7b
  drift ui/static/page-settings.js   "$DASHDIR/ui/static/page-settings.js"  fe15bf0ab7ad977c64b5fe5dacb8ac99
  if [[ -f /etc/systemd/system/weather-ui.service ]]; then
    UGOT=$(md5sum /etc/systemd/system/weather-ui.service | cut -d' ' -f1)
    if [[ "$UGOT" == "e1d6953d452f33e72227096c5137d94f" ]]; then
      ok "md5 ui/weather-ui.service = репо (/etc/systemd/system)"
    else
      warn "ДРЕЙФ ui/weather-ui.service (/etc): runtime ${UGOT:0:10} != репо e1d6953d45"
    fi
  else
    skip "дрейф ui/weather-ui.service: /etc-копии нет"
  fi
else
  skip "дрейф runtime-vs-repo — вне deploy-режима"
fi

# --- фикстурные проверки (ТОЛЬКО local/unit-test, на копии) ---
if [[ "$MODE" != "deploy" ]]; then
  echo "== G10. Фикстуры на копии (letter / issued_values / stale / events) =="
  if [[ "$FIXTURES" == "1" ]]; then
    FC2=$(curl -s --max-time 8 "${AUTH[@]}" "$B/api/forecast")
    assert_jq "letter фикстуры доходит до конверта (B)" "$FC2" '.zambretti.letter == "B"'
    assert_jq "issued_values.t_out_c = 10.0 (база на issued_at, НЕ «сейчас»)" "$FC2" '.issued_values.t_out_c == 10.0'
    assert_jq "issued_values.p_rel_mmhg = 770.0" "$FC2" '.issued_values.p_rel_mmhg == 770.0'
    # stale: возраст расчёта > 2 прогонов (2 ч). Сдвигаем ВСЕ прогоны копии
    # на 30 суток (без UNIQUE-коллизий по issued_at: вся таблица уезжает в
    # пустую область; 3-часовой сдвиг коллизировал с часовыми прогонами)
    sqlite3 "$WORK/test.db" "UPDATE forecast SET issued_at = issued_at - 2592000;"
    FC3=$(curl -s --max-time 8 "${AUTH[@]}" "$B/api/forecast")
    assert_jq "stale:true после сдвига issued_at на -30 сут (U5-S2: age > 2 ч)" "$FC3" '.stale == true'
    sqlite3 "$WORK/test.db" "UPDATE forecast SET issued_at = issued_at + 2592000;"
  else
    skip "прогонов forecast/погоды в копии нет — letter/issued_values-фикстуры пропущены"
  fi
  EV=$(curl -s --max-time 8 "${AUTH[@]}" "$B/api/events?from=$((NOW-3600))&to=$NOW")
  assert_jq "фикстурное событие SENSOR_MISSING видно в окне 1 ч" "$EV" '.rows | length > 0'
  assert_jq "overlap: открытое событие (ts_end NULL) в окне" "$EV" '.rows | length > 0'

  echo "== G11. Empty-DB -> 503 initMode (чистая sqlite со схемой) =="
  sqlite3 "$SRC_DB" ".schema" > "$WORK/schema.sql" 2>/dev/null
  sqlite3 "$WORK/empty.db" < "$WORK/schema.sql" 2>/dev/null
  UI_EMPTY="$WORK/ui_empty"
  mkdir -p "$UI_EMPTY"
  cp "$REPO_DIR/ui/server.py" "$UI_EMPTY/"
  prepare_env "$UI_EMPTY" "$WORK/empty.db" "$((TEST_PORT+1))"
  # U6-T2: таймаут-путь check-db (--check-timeout 0 -> детерминированный 503)
  EXTRA_SERVER_ARGS="--check-timeout 0"
  if start_server "$UI_EMPTY" "$((TEST_PORT+1))" "server2.pid"; then
    E1=$(curl -s -o "$TMPJSON" -w '%{http_code}' --max-time 8 -u weather:verify-pass-123 "http://127.0.0.1:$((TEST_PORT+1))/api/now")
    assert_eq "empty-DB: /api/now -> 503 initMode" "$E1" "503"
    assert_contains "empty-DB: тело 'current is empty'" "$(cat "$TMPJSON")" "current is empty"
    E2=$(curl -s -o "$TMPJSON" -w '%{http_code}' --max-time 8 -u weather:verify-pass-123 "http://127.0.0.1:$((TEST_PORT+1))/api/forecast")
    assert_eq "empty-DB: /api/forecast -> 200 (не 503)" "$E2" "200"
    assert_jq "empty-DB: /api/forecast available:false" "$(cat "$TMPJSON")" '.available == false'
    E3=$(curl -s -o "$TMPJSON" -w '%{http_code}' --max-time 8 -u weather:verify-pass-123 -X POST "http://127.0.0.1:$((TEST_PORT+1))/api/check-db")
    assert_eq "empty-DB: POST check-db (--check-timeout 0) -> 503 (U6-T2 таймаут-путь)" "$E3" "503"
    assert_contains "empty-DB: тело 'check timed out'" "$(cat "$TMPJSON")" "check timed out"
    E4=$(curl -s -o "$TMPJSON" -w '%{http_code}' --max-time 8 -u weather:verify-pass-123 "http://127.0.0.1:$((TEST_PORT+1))/api/export.csv?from=$((NOW-3600))&to=$NOW")
    assert_eq "empty-DB: export.csv -> 200 (только заголовок, без строк)" "$E4" "200"
    N_EMPTY=$(tr -d '\r' < "$TMPJSON" | grep -c ';')
    assert_eq "empty-DB: export — ровно 1 строка (заголовок)" "$N_EMPTY" "1"
  else
    bad "empty-DB сервер не поднялся (лог $WORK/server_$((TEST_PORT+1)).log)"
  fi

  echo "== G15. U6 local/unit-test: 413-путь, инъекции, пробник (§5.9 v1.2.8) =="
  if [[ "$U6_LIVE" == "1" ]]; then
    if [[ -n "$WMAX" && "$WMAX" != "NULL" ]]; then
      # 413-путь: полный диапазон данных + адаптивный эстимейт (строки x поля x 10).
      # Фикстура может быть разреженной (мало строк в свежем окне) — считаем
      # эстимацию по факту и при малом размере честно SKIP'аем (не FAIL).
      WMIN=$(sqlite3 "$WORK/test.db" "SELECT MIN(ts) FROM weather;" 2>/dev/null)
      NCOLS=$(sqlite3 "$WORK/test.db" "SELECT COUNT(*) FROM pragma_table_info('weather') WHERE name NOT IN ('id','schema_version');" 2>/dev/null)
      NROWS=$(sqlite3 "$WORK/test.db" "SELECT COUNT(*) FROM weather WHERE ts>=${WMIN:-0} AND ts<=$WMAX;" 2>/dev/null)
      EST=$(( ${NROWS:-0} * ${NCOLS:-0} * 10 ))
      if [[ "$EST" -gt 100000 ]]; then
        RC=$(curl -s -D "$WORK/h413.h" -o "$TMPJSON" -w '%{http_code}' --max-time 8 "${AUTH[@]}" "$B/api/export.csv?from=$WMIN&to=$WMAX" 2>/dev/null)
        assert_eq "export полного диапазона при --export-max-bytes 100000 -> 413 (pre-COUNT до байтов CSV)" "$RC" "413"
        assert_jq "413 тело: rows/estimated_bytes/limit_bytes" "$(cat "$TMPJSON")" 'has("rows") and has("estimated_bytes") and has("limit_bytes")'
        assert_contains "413 несёт X-Export-Rows=$NROWS (заголовок, §5.9 — образец u6_smoke)" "$(tr -d '\r' < "$WORK/h413.h")" "X-Export-Rows: ${NROWS}"
        cget "${AUTH[@]}" "$B/api/export.csv?from=$WMIN&to=$WMAX&limit=1"
        assert_eq "пробник limit=1 на переразмерном окне -> 413 (та же pre-COUNT-проверка, U6-C2)" "$RC" "413"
      else
        skip "G15 413-путь: фикстура мала (est=${EST}B <= 100000, строк=${NROWS:-0})"
      fi
      cget "${AUTH[@]}" "$B/api/export.csv?from=$((WMAX-3600))&to=$WMAX&limit=1"
      assert_eq "пробник limit=1 на допустимом окне -> 200" "$RC" "200"
      N_PROBE=$(tr -d '\r' < "$TMPJSON" | grep -c ';')
      assert_eq "пробник = заголовок + 1 строка" "$N_PROBE" "2"
    else
      skip "G15 413-путь: данных weather в копии нет"
    fi
    if [[ "$HMAX_FIX" == "1" ]]; then
      cget "${AUTH[@]}" "$B/api/export.csv?from=$((HMAX-3600))&to=$HMAX&type=hourly&fields=hour_epoch,wind_dir_mode"
      assert_eq "hourly export (фикс. окно) -> 200" "$RC" "200"
      assert_contains "CSV-injection hourly: '=1+2 в файле (§5.9)" "$(cat "$TMPJSON")" "'=1+2"
    else
      skip "CSV-injection hourly: v_hourly пуста"
    fi
    if [[ "$WMAX_FIX" == "1" ]]; then
      cget "${AUTH[@]}" "$B/api/export.csv?from=$((WMAX-3600))&to=$WMAX&fields=ts,battery_raw&separator=,"
      assert_eq "history export с separator=, -> 200" "$RC" "200"
      assert_contains "CSV-injection history: '=1+1 в файле (§5.9)" "$(cat "$TMPJSON")" "'=1+1"
      assert_contains "separator=, опция (заголовок с запятой)" "$(head -1 "$TMPJSON")" "ts,battery_raw"
    else
      skip "CSV-injection history: weather пуста"
    fi
  else
    skip "G15: сервер < 0.5.0 (U6 не деплоен)"
  fi
fi

# --- юнит обратно + вердикт ---
if [[ "$MODE" == "unit-test" && "$UNIT_WAS_ACTIVE" == "1" ]]; then
  echo "== G13. Юнит обратно (упражнение restart-логики) =="
  if sudo systemctl start weather-ui; then
    ok "systemctl start weather-ui"
  else
    bad "systemctl start weather-ui"
  fi
  sleep 1
  assert_eq "юнит active после старта" "$(systemctl is-active weather-ui)" "active"
  cget "http://127.0.0.1:8089/api/health"
  assert_eq "health 200 через loopback после старта юнита" "$RC" "200"
  if command -v ss >/dev/null 2>&1; then
    SSOUT=$(ss -tln 2>/dev/null | grep ':8089 ' || true)
    assert_contains "слушатели восстановлены: 127.0.0.1:8089" "$SSOUT" "127.0.0.1:8089"
    assert_contains "слушатели восстановлены: 192.168.8.146:8089" "$SSOUT" "192.168.8.146:8089"
    assert_contains "слушатели восстановлены: 10.147.17.101:8089" "$SSOUT" "10.147.17.101:8089"
  fi
fi

echo
if [[ "$FAILS" -gt 0 && -f "$WORK/server_$TEST_PORT.log" ]]; then
  echo "--- server.log (тестовый, последние 15 строк) ---"
  tail -15 "$WORK/server_$TEST_PORT.log"
  echo "--- конец server.log ---"
fi
echo "====================================================="
if [[ "$FAILS" -gt 0 ]]; then
  echo "VERIFY $MODE: FAIL — $((N-FAILS)) OK / $FAILS FAIL из $N (провалены:$FAILED_LIST)"
  exit 1
fi
echo "VERIFY $MODE: ALL PASSED — $N OK / 0 FAIL (SKIP включены в счёт)"
exit 0
