#!/usr/bin/env bash
# u6_smoke.sh — целевые прогоны U6-T1/T2/T3 на ЛОКАЛЬНОМ стенде (копия БД).
# В репо с 2026-09-25 (досдача U6: M-1/M-2 ревьювера) — ранее жили в агентской
# песочнице (scripts/u6_smoke.sh, транскрипт state/u6_smoke_out.txt: 56/0).
#
# Роли: A (порт 8301, --export-max-bytes 100000 — 413-путь), B (8302, дефолт —
# 7д->200/settings/check-db 200+429), C (8303, empty-DB + --check-timeout 0 —
# таймаут-путь + export пустой БД).
#
# Запуск (VM не трогается — всё на копии U6_SRC_DB в $U6_WORK):
#   U6_SRC_DB=/path/to/copy-of-weather.db deploy-tools/u6_smoke.sh
# Фикстуры применяются к КОПИИ инлайн: '=1+2 (hourly), '=1+1 (battery_raw),
# wmeta db_health -30 сут. Границы окна/число строк — динамически из копии.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
W="${U6_WORK:-${TMPDIR:-/tmp}/u6_smoke_work}"
SRC_DB="${U6_SRC_DB:?укажите U6_SRC_DB — копия БД с данными (weather/v_hourly/v_daily)}"
UI=$W/ui
DB=$W/test.db
EMPTY=$W/empty.db
CRED=$W/cred
PASS=0; FAIL=0; SKIP=0
ok()   { PASS=$((PASS+1)); echo "  OK   $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL $1 (got: ${2:-})"; }
skip() { SKIP=$((SKIP+1)); echo "  SKIP $1"; }
hdr() { # $1 label $2 want-haystack $3 needle
  if [[ "$2" == *"$3"* ]]; then ok "$1"; else bad "$1 (нет: '$3')" "$2"; fi
}

# --- стенд ---
pkill -f "server.py --db $W" 2>/dev/null; sleep 0.5
rm -rf "$W"; mkdir -p "$UI"
cp "$REPO/ui/server.py" "$REPO/ui/config.py" "$UI/"
cp -r "$REPO/ui/static" "$UI/static"
cat > "$UI/config.py" <<EOF
DB_PATH = "$DB"
BIND_HOSTS = ("127.0.0.1",)
PORT = 8301
PORT_FALLBACK = 8301
CRED_FILE = "$CRED"
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
BASE_DIR = "$UI"
STATIC_ROOT = "$UI/static"
TZ_FALLBACK = 10800
EOF
printf 'weather:u6-pass-123\n' > "$CRED" && chmod 600 "$CRED"
# фикстуры на КОПИИ (урок deploy-режима: источник только читается)
cp "$SRC_DB" "$DB"
T0=$(python3 -c "
import sqlite3, sys
con = sqlite3.connect('$DB')
wmin, wmax = con.execute('SELECT MIN(ts), MAX(ts) FROM weather').fetchone()
hinj = con.execute('SELECT MIN(hour_epoch) FROM v_hourly').fetchone()[0]
dmin, dmax = con.execute('SELECT MIN(day_epoch), MAX(day_epoch) FROM v_daily').fetchone()
ncols = con.execute(\"SELECT COUNT(*) FROM pragma_table_info('weather') WHERE name NOT IN ('id','schema_version')\").fetchone()[0]
nrows = con.execute('SELECT COUNT(*) FROM weather WHERE ts>=? AND ts<=?', (wmin, wmax)).fetchone()[0]
con.execute(\"UPDATE v_hourly SET wind_dir_mode='=1+2' WHERE hour_epoch=?\", (hinj,))
con.execute('UPDATE weather SET battery_raw=\'=1+1\' WHERE ts=?', (wmin,))
import json, time
con.execute(\"INSERT INTO wmeta(key,value,updated_at) VALUES('db_health',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value\",
            (json.dumps({'updated_at': int(time.time()) - 30*86400, 'status': 'ok'}), int(time.time())))
con.commit(); con.close()
print(wmin, wmax, hinj, dmin, dmax, ncols, nrows) ") || { echo "фикстура провалена"; exit 1; }
read -r T0 TMAX HINJ DMIN DMAX NCOLS NROWS <<< "$T0"
EST=$(( NROWS * NCOLS * 10 ))
# empty.db: схема копии без данных
python3 - <<PYEOF
import sqlite3
src = sqlite3.connect("$DB")
dst = sqlite3.connect("$EMPTY")
for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"):
    dst.execute(sql)
dst.commit(); dst.close(); src.close()
print("empty.db OK")
PYEOF

start() { # $1=port $2=db $3=extra-args $4=logfile
  ( cd "$UI" && exec python3 server.py --db "$2" --port "$1" --bind 127.0.0.1 \
      --cred "$CRED" $3 > "$W/$4" 2>&1 ) &
  echo $! > "$W/pid$1"
  for i in $(seq 1 40); do
    curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$1/api/health" 2>/dev/null && return 0
    sleep 0.25
  done
  echo "server $1 NOT UP"; tail -5 "$W/$4"; return 1
}
start 8301 "$DB" "--export-max-bytes 100000" srvA.log || exit 1
start 8302 "$DB" "" srvB.log || exit 1
start 8303 "$EMPTY" "--check-timeout 0" srvC.log || exit 1
AU="-u weather:u6-pass-123"
A=http://127.0.0.1:8301; B=http://127.0.0.1:8302; C=http://127.0.0.1:8303
NOW=$(date +%s)

echo "== U6-T1: export.csv =="
# 1) малое окно -> 200, chunked, нет Content-Length, no-store, filename
CODE=$(curl -s -D "$W/h1.h" -o "$W/exp1.csv" -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+3600))")
[[ "$CODE" == "200" ]] && ok "малое окно (1 ч) -> 200" || bad "малое окно -> 200" "$CODE"
B_=$(tr -d '\r' < "$W/h1.h")
hdr "chunked без Content-Length" "$B_" "Transfer-Encoding: chunked"
if printf '%s' "$B_" | grep -qi '^Content-Length:'; then bad "нет Content-Length" "заголовок присутствует"; else ok "Content-Length отсутствует"; fi
hdr "Content-Type text/csv" "$B_" "text/csv; charset=utf-8"
hdr "Cache-Control no-store" "$B_" "no-store"
hdr "filename weather-history-*" "$B_" 'filename="weather-history-'
hdr "первая строка — заголовок (sep ; дефолт)" "$(head -1 "$W/exp1.csv")" "ts;"
# 2) полное окно -> 413 + X-Export-Rows (pre-COUNT до байтов CSV)
if [[ "$EST" -gt 100000 ]]; then
  CODE=$(curl -s -D "$W/h413.h" -o "$W/exp413.json" -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$TMAX")
  [[ "$CODE" == "413" ]] && ok "полное окно при малом лимите -> 413" || bad "413" "$CODE"
  hdr "413 несёт X-Export-Rows=$NROWS" "$(tr -d '\r' < "$W/h413.h")" "X-Export-Rows: $NROWS"
  grep -q '"error":"export too large"' "$W/exp413.json" && ok "413 JSON-тело (rows/estimated_bytes)" || bad "413 тело" "$(cat "$W/exp413.json")"
  if ! head -c 200 "$W/exp413.json" | grep -q '^ts;'; then ok "413 ДО байтов CSV"; else bad "413 ДО байтов CSV" "CSV пошёл"; fi
  # 3) пробник на «большом» окне -> ТОЖЕ 413 (та же pre-COUNT-проверка — C2: клиент покажет баннер, не качает)
  CODE=$(curl -s -D "$W/probe413.h" -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$TMAX&limit=1")
  [[ "$CODE" == "413" ]] && ok "пробник limit=1 на переразмерном окне -> 413 (та же pre-COUNT-проверка)" || bad "пробник 413" "$CODE"
else
  skip "413-путь: фикстура мала (est=${EST}B <= 100000, строк=$NROWS) — гвард адаптивный, как G15 verify"
fi
# 3b) пробник на допустимом окне -> 200, 2 строки CSV (заголовок + 1 строка)
CODE=$(curl -s -D "$W/probe.h" -o "$W/probe.csv" -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+3600))&limit=1")
[[ "$CODE" == "200" ]] && ok "пробник limit=1 на допустимом окне -> 200" || bad "пробник 200" "$CODE"
[[ $(tr -d '\r' < "$W/probe.csv" | grep -c ';') == "2" ]] && ok "пробник = заголовок + 1 строка" || bad "пробник 2 строки" "$(wc -l < "$W/probe.csv")"
# 4-7) 400-пути
[[ "$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+60))&fields=ts,nope")" == "400" ]] && ok "fields неизвестное -> 400" || bad "fields 400"
[[ "$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+60))&separator=%7C")" == "400" ]] && ok "separator | -> 400" || bad "separator 400"
[[ "$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+60))&type=bogus")" == "400" ]] && ok "type bogus -> 400" || bad "type 400"
[[ "$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+60))&limit=0")" == "400" ]] && ok "limit=0 -> 400" || bad "limit 400"
[[ "$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$T0&to=$((T0+60))&limit=abc")" == "400" ]] && ok "limit=abc (не-число) -> 400, НЕ игнорируется (вердикт B-1)" || bad "limit=abc 400"
# 8) окно > 366 д -> 400 (413 — про размер, 400 — про окно)
[[ "$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=0&to=$NOW")" == "400" ]] && ok "окно > 366 д -> 400" || bad "окно 400"
# 9) hourly: инъекция wind_dir_mode '=1+2' -> в файле ''=1+2
CODE=$(curl -s -D - -o "$W/hourly.csv" -w '%{http_code}' $AU "$A/api/export.csv?from=$HINJ&to=$((HINJ+3600))&type=hourly")
hdr "hourly -> 200" "$CODE" "200"
hdr "CSV-injection hourly: в файле '=1+2" "$(cat "$W/hourly.csv")" "'=1+2"
hdr "hourly заголовок содержит wind_dir_mode" "$(head -1 "$W/hourly.csv")" "wind_dir_mode"
# 10) daily
CODE=$(curl -s -o /dev/null -w '%{http_code}' $AU "$A/api/export.csv?from=$DMIN&to=$DMAX&type=daily")
[[ "$CODE" == "200" ]] && ok "daily -> 200" || bad "daily 200" "$CODE"
# 11) separator=, + battery-инъекция
curl -s $AU "$A/api/export.csv?from=$T0&to=$((T0+60))&fields=ts,battery_raw&separator=," > "$W/sep.csv"
hdr "separator=, в заголовке" "$(head -1 "$W/sep.csv")" "ts,battery_raw"
hdr "CSV-injection history: в файле '=1+1" "$(cat "$W/sep.csv")" "'=1+1"

DAYS=$(( (TMAX - T0) / 86400 ))
echo "== U6-T1b: окно 7 д при дефолтном лимите (300 МБ) -> 200 =="
CODE=$(curl -s -D "$W/b7.h" -o "$W/b7.csv" -w '%{http_code}' $AU "$B/api/export.csv?from=$((TMAX-7*86400))&to=$TMAX")
[[ "$CODE" == "200" ]] && ok "окно 7 д -> 200 (дефолт)" || bad "7д 200" "$CODE"
B_=$(tr -d '\r' < "$W/b7.h")
hdr "7д chunked" "$B_" "Transfer-Encoding: chunked"
[[ $(tr -d '\r' < "$W/b7.csv" | grep -c '^') -ge 3 ]] && ok "7д: заголовок + данные (фикстура разрежена)" || bad "7д строки" "$(wc -l < "$W/b7.csv")"
# полное окно при дефолтном лимите: все NROWS строк
CODE=$(curl -s -D "$W/ball.h" -o "$W/ball.csv" -w '%{http_code}' $AU "$B/api/export.csv?from=$T0&to=$TMAX")
[[ "$CODE" == "200" ]] && ok "полное окно (${DAYS} д, $NROWS строк) -> 200 (дефолт)" || bad "полное 200" "$CODE"
[[ $(tr -d '\r' < "$W/ball.csv" | grep -c '^') == "$((NROWS+1))" ]] && ok "полное окно: $((NROWS+1)) строк ($NROWS+заголовок)" || bad "полное $((NROWS+1)) строк" "$(wc -l < "$W/ball.csv")"

echo "== U6-T2: check-db =="
CODE=$(curl -s -D "$W/cd1.h" -o "$W/cd1.json" -w '%{http_code}' -X POST $AU "$B/api/check-db")
[[ "$CODE" == "200" ]] && ok "POST check-db -> 200" || bad "check-db 200" "$CODE"
grep -q '"status":"ok"' "$W/cd1.json" && ok "quick_check: status=ok" || bad "status ok" "$(cat "$W/cd1.json")"
CODE=$(curl -s -D "$W/cd2.h" -o /dev/null -w '%{http_code}' -X POST $AU "$B/api/check-db")
[[ "$CODE" == "429" ]] && ok "второй вызов в пределах минуты -> 429" || bad "429" "$CODE"
hdr "429 несёт Retry-After: 60" "$(tr -d '\r' < "$W/cd2.h")" "Retry-After: 60"
[[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$B/api/check-db")" == "401" ]] && ok "POST без auth -> 401" || bad "POST 401"
CODE=$(curl -s -o "$W/cdt.json" -w '%{http_code}' -X POST $AU "$C/api/check-db")
[[ "$CODE" == "503" ]] && ok "таймаут-путь (--check-timeout 0) -> 503" || bad "таймаут 503" "$CODE"
grep -q '"check timed out"' "$W/cdt.json" && ok "тело: check timed out" || bad "timeout body" "$(cat "$W/cdt.json")"
[[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST $AU "$B/api/now")" == "405" ]] && ok "POST /api/now по-прежнему 405" || bad "405"

echo "== U6-T3: /api/settings =="
CODE=$(curl -s -D - -o "$W/set.json" -w '%{http_code}' $AU "$B/api/settings")
hdr "settings -> 200" "$CODE" "200"
jq -e 'has("wmeta") and has("schema_migrations") and has("collector_log") and has("db_health")' "$W/set.json" >/dev/null 2>&1 && ok "конверт {wmeta, schema_migrations, collector_log, db_health}" || bad "конверт settings"
jq -e '.wmeta | has("units") and has("tz_offset_seconds")' "$W/set.json" >/dev/null 2>&1 && ok "whitelist-ключи присутствуют" || bad "whitelist"
if jq -e '.wmeta | has("station_ip") or has("station_mac") or has("migrated_at") or has("units_set_at")' "$W/set.json" >/dev/null 2>&1; then bad "фильтр wmeta: чужие ключи утекли"; else ok "фильтр wmeta: station_ip/station_mac/прочие НЕ отдаются"; fi
jq -e '.db_health.updated_at != null' "$W/set.json" >/dev/null 2>&1 && ok "db_health проходит конверт (фикстура -30 сут)" || bad "db_health"
jq -e '.collector_log | length > 0' "$W/set.json" >/dev/null 2>&1 && ok "collector_log непуст" || bad "collector_log"
# страница /settings + DOM-ID
CODE=$(curl -s -o "$W/set.html" -w '%{http_code}' $AU "$B/settings")
[[ "$CODE" == "200" ]] && ok "/settings -> 200" || bad "/settings 200" "$CODE"
for id in set-export set-export-btn set-wmeta set-mig set-clog set-dbh set-check-btn set-modal; do
  grep -q "id=\"$id\"" "$W/set.html" && ok "DOM id=$id" || bad "DOM id=$id"
done
# empty-DB: export -> 200 header-only
CODE=$(curl -s -D - -o "$W/empty.csv" -w '%{http_code}' $AU "$C/api/export.csv?from=$T0&to=$((T0+3600))")
hdr "empty-DB export -> 200" "$CODE" "200"
[[ $(tr -d '\r' < "$W/empty.csv" | grep -c ';') == "1" ]] && ok "empty-DB: только заголовок" || bad "empty header-only" "$(wc -l < "$W/empty.csv")"

echo "== логи без секретов =="
if grep -rq "u6-pass-123" "$W"/srv?.log; then bad "пароль в логах"; else ok "пароль отсутствует в логах"; fi

kill $(cat "$W/pid8301") $(cat "$W/pid8302") $(cat "$W/pid8303") 2>/dev/null
sleep 0.5
echo "==================================================="
echo "U6 SMOKE: $PASS OK / $FAIL FAIL$( [[ "$SKIP" -gt 0 ]] && printf ' / %d SKIP' "$SKIP" )"
exit $((FAIL > 0))
