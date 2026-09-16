# Снапшот: этап A — схема v2.1 + коллектор v2 + systemd (weather-6)

**Дата:** 2026-09-16, ~00:00–00:10 MSK. **Статус: ЭТАП A ВЫПОЛНЕН, приёмка ALL_PASS.**
Канон реализации: `network/weather-roadmap.md` (дельты v2.1 в §3, чек-лист в §4).

## Что сделано

- **A0 префлайт:** NTPSynchronized=yes; ts v1 = epoch UTC; диск 7.4 ГБ свободно.
- **A1 снепшот v1:** `backups-archive/weather-v1-2026-09-15/weather-v1-2026-09-15_2357.db.gz`
  (VACUUM INTO + integrity=ok).
- **A2 миграция (копирующая, одна транзакция):** 98 строк raw скопированы 1:1,
  L1 пересчитан (98/98 заполнено), `journal_mode=WAL` + `journal_size_limit=64M`,
  15 таблиц v2.1 (weather, wmeta, collector_log, events, forecast, summaries, insights,
  v_hourly/v_daily/v_monthly/v_yearly, materializer_log, schema_migrations),
  `schema_migrations` v2 записана. Старое сохранено: `weather_v1_backup`,
  `wmeta_v1_backup`. wmeta: ключи v1 перенесены + новые (schema_version=2,
  tz=Europe/Moscow, tz_offset_seconds=10800, tz_policy=fixed_offset, gdd_tbase_c=5,
  station_mac/ip, err_total, sanity_fail_total, migrated_at).
- **A3 коллектор v2.0** `~/weather-dash/weather_collector.py` (stdlib-only, демон,
  цикл 60 с на :02, SIGTERM-плавный): MAP 23 поля (как v1) + battery_raw;
  L1 при ингесте — точка росы (Magnus), wind chill (JAG/TI), heat index (Rothfusz),
  румбы 16, ΔP, wind run (dt≤300 c), rain_rate_calc (окно 10 мин, квант типпера
  0.1 мм), rain_event (≥0.1), RAIN_COUNTER_RESET (отрицательная дельта);
  PRAGMA per-connection (busy_timeout=5000, synchronous=NORMAL,
  journal_size_limit=64M); sanity (P 550–850, T_out −60…+60, ветер 0–75, ts≥last−5)
  → статус sanity_fail + Kuma push down; события этапа A: FROST, HARD_FREEZE,
  BATTERY_LOW (regex `\bok\b`), SENSOR_MISSING (gap>600 c), RAPID_TEMP_DROP/RISE
  (±5 °C/ч, dedup 3 ч), RAIN_COUNTER_RESET.
- **A4 systemd:** `weather-collector.service` (User=auditbot, WorkingDirectory,
  Wants+After=network-online, Restart=always RestartSec=10,
  StartLimitIntervalSec=300/Burst=5, без watchdog, PYTHONUNBUFFERED=1).
  **cron-строка v1 удалена ДО старта юнита** (23:57 MSK); enable --now 23:58:06.
- **A5 бэкап:** `weather_backup.sh` (VACUUM INTO → integrity_check → gzip;
  df-guard 1.5 ГБ; ротация daily 14; «холодный» месячный 12, 1-го числа) +
  `weather-backup.timer` (04:20 MSK, Persistent=true).
- **A6 Kuma:** Push-монитор «Метеостанция .101 — сбор (push)», id=73, interval 60,
  maxretries 2; токен в `~/weather-dash/kuma_push.conf` (chmod 600, не публикуется);
  push `?status=up|down&msg=&ping=` каждый цикл (тест `{"ok":true}`).
- **A7 journald:** drop-in `/etc/systemd/journald.conf.d/weather-size.conf` →
  SystemMaxUse=500M (фактическое использование 15.7M — профилактика).

## Приёмка (weather6_acceptance + recheck) — ALL PASS

| Пункт | Результат |
|---|---|
| cron v1 удалён | PASS (crontab без weather) |
| юнит active | PASS (NRestarts=0) |
| collector_log | PASS (12 записей/10 мин, все ok, sanity_fail=0, latency 40–200 мс) |
| замер свежий | PASS (age ≤ 120 с) |
| провалы >90 с за час | PASS (0) |
| дубли ts | PASS (0, UNIQUE(ts) работает) |
| L1 | PASS (Td≤T_out везде; p_delta=rel−abs везде; румбы из 16) |
| events | живая (пуста — триггеров нет, норма) |
| бэкап вручную | PASS (weather-2026-09-16_0005.db.gz, 8 КБ, integrity=ok) |
| Kuma push | PASS (тест ok, WARN=0) |
| restart-тест | PASS (жив, дублей нет, замер в ту же минуту) |
| journald 500M | PASS |

## Откат (запасной, не требуется)

1. `sudo systemctl disable --now weather-collector`
2. Вернуть cron: `echo '*/1 * * * * /usr/bin/python3 /home/auditbot/weather-dash/weather_poller.py >> /home/auditbot/weather-dash/weather.log 2>&1' | crontab -`
3. Данные v1 нетронуты (`weather_v1_backup` + gz-снепшот); `weather_poller.py` на месте.

## Хвосты (не блокируют)

- Чистка остатков v1 (`weather_poller.py`, `weather.log`, backup-таблицы) — после
  приёмки этапа B.
- Push-монитор вне групп дашборда Kuma — сгруппировать опционально.
- Этап B (roadmap §5): материализаторы + тренды + Замбретти/Sager + API-скелет + Kuma.
