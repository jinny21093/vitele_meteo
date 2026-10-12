#!/bin/bash
# Remote meteo backup via ZeroTier (closes risk #1): свежайший
# локальный снапшот + конфиги/скрипты + reports → 10.147.17.42.
# Слот 06:55: после снапшота 06:40, до отчёта 07:20.
# Молчание Kuma = вывоз не случился.
set -euo pipefail
DEST=jin@10.147.17.42:/home/jin/dacha-meteo
LOG=/home/auditbot/weather-dash/backup_remote.log
SRC_DB=/home/auditbot/backups-archive/weather-db/daily/
LATEST=$(ls -t "$SRC_DB"weather-*.db.gz 2>/dev/null | head -1)
[ -n "$LATEST" ] || { echo "$(date '+%F %T') FAIL no local snapshot" >> "$LOG"; exit 1; }
# вывоз БД: только свежайший файл (история уже там, инкрементально)
rsync -a --timeout=120 "$LATEST" "$DEST/db/"
# вывоз конфигов/скриптов (файлы кода и конфигов, БЕЗ бэкап-мусора)
rsync -a --timeout=120 \
  --include='*/' --include='*.py' --include='*.sh' \
  --include='*.service' --include='*.timer' --include='*.env' \
  --include='*.conf' --include='*.sql' --exclude='*' \
  /home/auditbot/weather-dash/ "$DEST/configs/"
# report.env содержит секреты — вывозится осознанно (суть бэкапа
# восстановления), rsync -a сохраняет права 600; НИЧЕГО из env
# в лог не попадает, в отчёт не цитировать (только факт+md5).
# ok-push в Kuma (механизм M6)
URL=$(grep -m1 '^backup_url=' /home/auditbot/weather-dash/kuma_push.conf | cut -d= -f2-)
curl -s -G --max-time 5 "$URL" \
  --data-urlencode "status=up" \
  --data-urlencode "msg=remote backup ok $(basename "$LATEST")" \
  >/dev/null || echo "$(date '+%F %T') WARN kuma push failed" >> "$LOG"
echo "$(date '+%F %T') OK $(basename "$LATEST")" >> "$LOG"
