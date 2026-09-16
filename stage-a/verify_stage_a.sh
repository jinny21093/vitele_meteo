#!/bin/bash
# verify_stage_a.sh v1 — автоматизированная приёмка этапа A (weather-7,
# «вдогонка» DeepSeek §5). Приёмка = прогон этого скрипта, вывод
# ALL CHECKS PASSED. FAIL — exit 1, WARN — не валид.
# Отличия от эскиза DeepSeek: set -u + счётчик FAIL (вместо set -e — grep/
# systemctl в if-ах непредсказуемы), добавлены п.7 cron-чистота, п.8 schema,
# п.9 BATTERY_LOW (whitelist). Семантика проверок сохранена.
set -u
DB=/home/auditbot/weather-dash/weather.db
FAIL=0
NOW=$(date +%s)
ok()   { echo "   OK ${1:-}"; }
bad()  { echo "   FAIL $1"; FAIL=1; }
warn() { echo "   WARN $1"; }

echo "1. Свежие замеры (>=5 за 5 мин):"
C=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM weather WHERE ts > $NOW-300")
if [ "${C:-0}" -ge 5 ]; then ok "($C)"; else bad "($C <5)"; fi

echo "2. Коллектор логирует успех (>=55 ok/час):"
C=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM collector_log WHERE status='ok' AND ts > $NOW-3600")
if [ "${C:-0}" -ge 55 ]; then ok "($C)"; else bad "($C <55)"; fi

echo "3. Событий за сутки >0 (FROST/SENSOR_* и т.п.; штиль возможен):"
C=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM events WHERE ts_start > $NOW-86400")
if [ "${C:-0}" -gt 0 ]; then ok "($C)"; else warn "(0 — может быть штиль)"; fi

echo "4. Служба активна, без рестартов:"
if systemctl is-active -q weather-collector; then ok; else bad "не active"; fi
N=$(systemctl show weather-collector -p NRestarts --value)
if [ "${N:-1}" -eq 0 ]; then ok "(0 restarts)"; else warn "($N restarts)"; fi

echo "5. Нет NULL в ключевых полях за час:"
C=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM weather WHERE ts > $NOW-3600 AND (outdoor_temp_c IS NULL OR pressure_rel_mmhg IS NULL)")
if [ "${C:-1}" -eq 0 ]; then ok; else bad "($C NULL-строк)"; fi

echo "6. Целостность БД (quick_check):"
Q=$(sqlite3 -readonly "$DB" "PRAGMA quick_check" 2>/dev/null | head -1)
if [ "$Q" = "ok" ]; then ok; else bad "(quick_check=$Q)"; fi

echo "7. cron v1 удалён (нет weather-строк):"
if crontab -l 2>/dev/null | grep -q weather; then bad "найдена weather-строка в cron"; else ok; fi

echo "8. Схема v2 (wmeta.schema_version):"
V=$(sqlite3 -readonly "$DB" "SELECT value FROM wmeta WHERE key='schema_version'")
if [ "$V" = "2" ]; then ok "(schema_version=2)"; else bad "(schema_version=$V)"; fi

echo "9. BATTERY_LOW не открыт (battery whitelist, fail-safe):"
C=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM events WHERE event_type='BATTERY_LOW' AND ts_end IS NULL")
if [ "${C:-1}" -eq 0 ]; then ok; else warn "($C открыт(ы) — проверь battery_raw в weather/events)"; fi

echo "10. Kuma: последний push не старше 5 мин (wmeta.last_ok):"
L=$(sqlite3 -readonly "$DB" "SELECT value FROM wmeta WHERE key='last_ok'")
if [ -n "${L:-}" ] && [ $((NOW - L)) -le 300 ]; then ok "(last_ok $((NOW - L))s назад)"; else bad "(last_ok=${L:-нет} / $((NOW - ${L:-0}))s назад)"; fi

if [ "$FAIL" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
else
    echo "CHECKS FAILED"
    exit 1
fi
