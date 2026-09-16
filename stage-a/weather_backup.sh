#!/bin/bash
# weather_backup.sh v2 — этап A5 (weather-6) + правки «вдогонку» DeepSeek (weather-7):
#   * df-guard ПЕРЕД VACUUM INTO (иначе битый бэкап + переполненный диск);
#     при пропуске — три независимых следа: collector_log, journald, Kuma (push down)
#   * каждый исход пишется в collector_log (статусы backup_ok|backup_skipped|
#     backup_failed; ts выравнивается на MM+45с, чтобы не пересечься с рядами
#     коллектора MM+2с — PK общий)
#   * Kuma: ОТДЕЛЬНЫЙ Push-монитор «Метеостанция .101 — бэкап (push)» (URL из
#     kuma_push.conf, строка backup_url=...; нет строки — push-и тихо пропускаются).
#     Нельзя слать down на монитор «сбор»: коллектор затрёт его up-ом через 60 с.
# Запуск: systemd timer weather-backup.timer (04:20 MSK) или вручную:
# systemctl start weather-backup.service
set -u
DB=/home/auditbot/weather-dash/weather.db
BASE=/home/auditbot/backups-archive/weather-db
DAILY="$BASE/daily"
COLD="$BASE/cold"
LOG=/home/auditbot/weather-dash/weather-backup.log
KUMA_CONF=/home/auditbot/weather-dash/kuma_push.conf
mkdir -p "$DAILY" "$COLD"
ts=$(date +%F_%H%M)
stamp() { date '+%F %T'; }

BK_URL=$(grep -m1 '^backup_url=' "$KUMA_CONF" 2>/dev/null | cut -d= -f2-)
kuma_push() { # $1=up|down $2=msg
    [ -n "$BK_URL" ] || return 0
    curl -s -G --max-time 5 "$BK_URL" \
        --data-urlencode "status=$1" --data-urlencode "msg=$2" >/dev/null 2>&1 || true
}
db_log() { # $1=status $2=bytes $3=error(может быть пусто)
    local lts=$(( $(date +%s) / 60 * 60 + 45 ))
    sqlite3 -cmd '.timeout 5000' "$DB" \
        "INSERT OR REPLACE INTO collector_log (ts,status,latency_ms,bytes,error) \
         VALUES ($lts,'$1',NULL,${2:-NULL},$( [ -n "${3:-}" ] && printf "'%s'" "${3//\'/}" || echo NULL ))" \
        2>>"$LOG" || echo "$(stamp) WARN collector_log insert failed" >> "$LOG"
}

free_mb=$(df -Pm / | awk 'NR==2{print $4}')
if [ "${free_mb:-0}" -lt 1500 ]; then
    echo "$(stamp) ALERT free=${free_mb}MB < 1500MB — бэкап ПРОПУЩЕН (df-guard)" >> "$LOG"
    logger -t weather-backup "ALERT free=${free_mb}MB — backup skipped (df-guard)"
    db_log "backup_skipped" "NULL" "disk_full free=${free_mb}MB"
    kuma_push "down" "backup_skipped: disk_full (free=${free_mb}MB)"
    exit 0
fi

tmp="$DAILY/.weather-$ts.db"
rm -f "$tmp"
if ! sqlite3 "$DB" "VACUUM INTO '$tmp'"; then
    echo "$(stamp) ERROR VACUUM INTO failed" >> "$LOG"
    logger -t weather-backup "ERROR VACUUM INTO failed"
    db_log "backup_failed" "NULL" "VACUUM INTO failed"
    kuma_push "down" "backup_failed: VACUUM INTO"
    exit 1
fi

ich=$(sqlite3 "$tmp" "PRAGMA integrity_check;" 2>&1 | head -1)
if [ "$ich" != "ok" ]; then
    echo "$(stamp) ERROR integrity_check=$ich — копия удалена" >> "$LOG"
    logger -t weather-backup "ERROR integrity_check=$ich"
    db_log "backup_failed" "NULL" "integrity_check=$ich"
    kuma_push "down" "backup_failed: integrity=$ich"
    rm -f "$tmp"
    exit 1
fi

gzip -f "$tmp" || { echo "$(stamp) ERROR gzip" >> "$LOG"; logger -t weather-backup "ERROR gzip"; db_log "backup_failed" "NULL" "gzip"; kuma_push "down" "backup_failed: gzip"; exit 1; }
final="$DAILY/weather-$ts.db.gz"
mv "$tmp.gz" "$final"
size=$(du -h "$final" | cut -f1)
bsize=$(stat -c%s "$final" 2>/dev/null || echo 0)

if [ "$(date +%d)" = "01" ]; then
    cp "$final" "$COLD/weather-cold-$(date +%Y-%m).db.gz"
    ls -1t "$COLD"/weather-cold-*.db.gz 2>/dev/null | tail -n +13 | xargs -r rm -f
    echo "$(stamp) COLD monthly: $COLD/weather-cold-$(date +%Y-%m).db.gz" >> "$LOG"
fi

# ротация daily: хранить 14
ls -1t "$DAILY"/weather-*.db.gz 2>/dev/null | tail -n +15 | xargs -r rm -f

echo "$(stamp) OK $final ($size) integrity=ok free=${free_mb}MB" >> "$LOG"
logger -t weather-backup "OK $final ($size) free=${free_mb}MB"
db_log "backup_ok" "$bsize" ""
kuma_push "up" "backup_ok $size"
