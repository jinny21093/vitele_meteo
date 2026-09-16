#!/bin/bash
# verify_stage_b.sh v1 — автоматизированная приёмка этапа B (weather-8, roadmap §5).
# Приёмка = прогон этого скрипта, вывод ALL CHECKS PASSED. FAIL — exit 1, WARN — не валид.
# Проверки: коллектор жив; таймеры agg + api active; materializer_log свежий и без ошибок;
# v_hourly/v_daily наполнены и свежи; forecast (persistence+zambretti) пишется;
# API: /health 200, /now 401 без auth и 200 с auth; сверка агрегатов с raw за сутки;
# quick_check БД. Стиль verify_stage_a.sh (set -u безопасен: ok() без аргументов).
set -u
DB=/home/auditbot/weather-dash/weather.db
AUTH=/home/auditbot/weather-dash/api_auth.conf
API=http://127.0.0.1:8090
FAIL=0
NOW=$(date +%s)
ok()   { echo "   OK ${1:-}"; }
bad()  { echo "   FAIL $1"; FAIL=1; }
warn() { echo "   WARN $1"; }
q()    { sqlite3 -readonly "$DB" "$1"; }

echo "1. Коллектор жив (>=4 ok/5мин, служба active):"
C=$(q "SELECT COUNT(*) FROM collector_log WHERE status='ok' AND ts > $NOW-300")
A=$(systemctl is-active weather-collector)
if [ "$A" = "active" ] && [ "${C:-0}" -ge 4 ]; then ok "($C ok/5мин, active)"; else bad "($C ok/5мин, svc=$A)"; fi

echo "2. Этап B запущен (agg-hourly.timer, agg-daily.timer, weather-api):"
T1=$(systemctl is-active weather-agg-hourly.timer)
T2=$(systemctl is-active weather-agg-daily.timer)
T3=$(systemctl is-active weather-api)
if [ "$T1" = "active" ] && [ "$T2" = "active" ] && [ "$T3" = "active" ]; then ok "(все active)"; else bad "(timer1=$T1 timer2=$T2 api=$T3)"; fi

echo "3. materializer_log: hourly за 90 мин и daily за сутки, без ошибок:"
H=$(q "SELECT COUNT(*) FROM materializer_log WHERE kind='hourly' AND ts > $NOW-5400 AND COALESCE(error,'')=''")
D=$(q "SELECT COUNT(*) FROM materializer_log WHERE kind='daily' AND ts > $NOW-86400 AND COALESCE(error,'')=''")
if [ "${H:-0}" -ge 1 ] && [ "${D:-0}" -ge 1 ]; then ok "(hourly=$H, daily=$D)"; else bad "(hourly=$H, daily=$D)"; fi

echo "4. v_hourly: >=10 строк, свежий час (хвост 3ч):"
N=$(q "SELECT COUNT(*) FROM v_hourly")
MX=$(q "SELECT COALESCE(MAX(hour_epoch),0) FROM v_hourly")
if [ "${N:-0}" -ge 10 ] && [ "$MX" -ge $((NOW - 7200)) ]; then ok "($N строк, последний час $(date -u -d @$MX +%H:00))"; else bad "($N строк, max_hour=$MX)"; fi

echo "5. v_daily: >=1 строка, last_agg_daily_epoch записан:"
ND=$(q "SELECT COUNT(*) FROM v_daily")
LA=$(q "SELECT value FROM wmeta WHERE key='last_agg_daily_epoch'")
if [ "${ND:-0}" -ge 1 ] && [ -n "$LA" ]; then ok "($ND строк, last_agg_daily=$LA)"; else bad "($ND строк, last=$LA)"; fi

echo "6. forecast: persistence >=3 и zambretti с текстом за час:"
P=$(q "SELECT COUNT(*) FROM forecast WHERE source='persistence' AND issued_at > $NOW-3600")
Z=$(q "SELECT COUNT(*) FROM forecast WHERE source='zambretti' AND issued_at > $NOW-3600 AND COALESCE(text,'')!=''")
if [ "${P:-0}" -ge 3 ] && [ "${Z:-0}" -ge 1 ]; then ok "(persistence=$P, zambretti=$Z)"; else bad "(persistence=$P, zambretti=$Z)"; fi

echo "7. API: /health 200; /now без auth 401; /now с auth 200+trends:"
CRED=$(cat "$AUTH" 2>/dev/null || true)
H1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API/health")
H2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API/now")
H3=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -u "$CRED" "$API/now")
BODY=$(curl -s --max-time 5 -u "$CRED" "$API/now")
if [ "$H1" = "200" ] && [ "$H2" = "401" ] && [ "$H3" = "200" ] && echo "$BODY" | grep -q '"trends"'; then
    ok "(health=$H1, noauth=$H2, auth=$H3)"
else
    bad "(health=$H1, noauth=$H2, auth=$H3)"
fi

echo "8. Сверка агрегатов с raw (последние сутки в v_daily):"
D0=$(q "SELECT MAX(day_epoch) FROM v_daily")
RAWN=$(q "SELECT COUNT(*) FROM weather WHERE ts>=$D0 AND ts<$D0+86400")
AGGN=$(q "SELECT n_samples FROM v_daily WHERE day_epoch=$D0")
RAWT=$(q "SELECT ROUND(AVG(outdoor_temp_c),3) FROM weather WHERE ts>=$D0 AND ts<$D0+86400")
AGGT=$(q "SELECT ROUND(t_out_avg,3) FROM v_daily WHERE day_epoch=$D0")
RAWR=$(q "SELECT ROUND(SUM(d),2) FROM (SELECT rain_total_mm - LAG(rain_total_mm) OVER (ORDER BY ts) AS d FROM weather WHERE ts>=$D0-600 AND ts<$D0+86400 AND rain_total_mm IS NOT NULL) WHERE d>=0.0999 AND d<5")
AGGR=$(q "SELECT ROUND(rain_mm,2) FROM v_daily WHERE day_epoch=$D0")
DT=$(python3 -c "print(abs(float('${RAWT:-0}')-float('${AGGT:-0}')))" 2>/dev/null || echo 99)
DR=$(python3 -c "print(abs(float('${RAWR:-0}')-float('${AGGR:-0}')))" 2>/dev/null || echo 99)
if [ "$RAWN" = "$AGGN" ] && python3 -c "exit(0 if $DT <= 0.05 else 1)" && python3 -c "exit(0 if $DR <= 0.3 else 1)"; then
    ok "(n=$RAWN=$AGGN, dt=$DT, dr=$DR мм)"
else
    bad "(raw n=$RAWN t=$RAWT r=$RAWR vs agg n=$AGGN t=$AGGT r=$AGGR)"
fi

echo "9. Целостность БД (quick_check):"
QQ=$(q "PRAGMA quick_check" | head -1)
if [ "$QQ" = "ok" ]; then ok; else bad "(quick_check=$QQ)"; fi

if [ "$FAIL" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
else
    echo "CHECKS FAILED"
    exit 1
fi
