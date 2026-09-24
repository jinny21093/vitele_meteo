# Отчёт U7 «systemd + Kuma + verify» + хвосты U5 (Х-1/Х-2)

**Дата:** 2026-09-25 (работы 24.09 23:40 – 25.09 01:00 MSK)
**База:** `b966a05` (отчёт U5 v0.4.0) → голова: `07e038f`
**Версии:** SERVER_VERSION **0.4.1** (бамп — изменение деплоированного config.py); UI_VERSION **0.4.0 без изменений** (app.js не менялся, правило не трогать). Спека — **v1.2.7**.
**Статус:** U7-1…U7-8 — ВЫПОЛНЕНО; Х-1 — НАХОДКА (не чинил, см. §1); Х-2 — находка подтверждена (см. §2).

---

## 0. Х-1 (хвост U5): буква Замбретти в живом конверте — НАХОДКА, не чинил

a) Дословно по чеклисту, после слота агрегатора 00:02 (проверено в 00:33 MSK):

```
$ curl -s -u $CRED "http://192.168.8.146:8089/api/forecast" | grep -o '"letter"[^,]*'
"letter":null
```

b) Диагностика (SELECT по forecast упал — колонки нет):

```
$ sqlite3 weather.db "SELECT issued_at, target_ts, source, letter FROM forecast ORDER BY issued_at DESC LIMIT 5;"
Error: in prepare, no such column: letter

$ sqlite3 weather.db 'PRAGMA table_info(forecast);'   → колонки 0..10: id, issued_at,
  target_ts, source, t_out_c, p_rel_mmhg, rh_out_pct, wind_ms, rain_mm, confidence, text
  (колонки letter НЕТ)

$ md5sum /home/auditbot/weather-dash/weather_aggregator.py
5d1f4ab73f887384bdd66902221ef329  /home/auditbot/weather-dash/weather_aggregator.py
   (= md5 stage-b/weather_aggregator.py @8d362d5 — версия ДО U5-B; migrate_letter в ней
    отсутствует: grep -c 'def migrate_letter' → 0; в клоне репо на VM — 1)
```

journal weather-agg-hourly (слоты идут, код старый — буква печатается в лог, в БД не пишется):

```
Sep 25 00:02:01 debian-vitele python3[221357]: [agg 00:02:01] hourly: окна 3 (…), строк 3
Sep 25 00:02:01 debian-vitele python3[221357]: [agg 00:02:01] forecast: +5 (persistence 3;
  zambretti A; sager: ночь; trend 1.56 мм rapid_rise)
```

**Вывод:** деплой v0.4.0 обновил клон репо на VM, но НЕ синкал `stage-b/weather_aggregator.py` в runtime `/home/auditbot/weather-dash/` (чеклист грепал `migrate_letter` в КЛОНЕ — «код доехал» было ложью для runtime). Колонки `letter` в БД нет, поэтому letter не появится ни после какого числа слотов *:02. По инструкции Х-1b НЕ ЧИНИЛ. Фикс по решению владельца — одна команда + ближайший слот:

```
cp /home/auditbot/vitele_meteo/stage-b/weather_aggregator.py /home/auditbot/weather-dash/weather_aggregator.py
# следующий слот *:02: migrate_letter добавит колонку и напишет буквы
```

Предложение: добавить синк stage-b в чеклист деплоя (v0.4.0 синкал только ui/).

## 1. Х-2 (хвост U5, F-3): живая генерация событий

```
$ sqlite3 /home/auditbot/weather-dash/weather.db "SELECT COUNT(*) FROM events;"
0
$ sqlite3 … "SELECT id, ts_start, event_type, severity FROM events ORDER BY id DESC LIMIT 3;"
(пусто — таблица пуста полностью)
```

Докладываю как есть: таблица events ПУСТАЯ — живой генерации нет ни одной записи за всё время (F-3 с U4). Вопрос этапа B к владельцу, не блокер U7. Контекст погоды за сутки (v_hourly, 23 окна):

```
MIN(t_out_min)=12.4   MAX(t_out_max)=19.3   (°C)
MIN(p_rel_min)=764.0  MAX(p_rel_max)=773.6  (мм рт.ст., рост)
макс. часовое падение p_rel_avg: 0.77 мм/ч
```

За сутки были и рост давления на ~9.6 мм, и перепад T на ~7 °C — «событийные» условия были, записей нет.

## 2. U7-1: systemd-юнит (ui/weather-ui.service, коммит 3367a4f)

Файл целиком:

```ini
; weather-ui.service — systemd-юнит UI-сервера (U7-1, спека weather-ui-spec.md
; v1.2.7 §2.2). Ставится рядом с этапами A/B: cp ui/weather-ui.service
; /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now
; weather-ui. Заменяет setsid-nohup-запуск: переживает ребут VM (инцидент
; 20.09 — UI мёртв 4.5 суток незаметно).
;
; ZeroTier — Wants, НЕ Requires: ZT не обязателен для LAN-бинда (UI обязан
; подниматься и без него). Бинды — только из config.py (LAN + ZT + loopback
; с v0.4.1); никаких --bind 0.0.0.0. TimeoutStopSec=30 — под graceful
; shutdown server.py (SIGTERM -> shutdown() в отдельном потоке -> server_close,
; daemon_threads=False — дожидаемся обработчиков, §2.2).

[Unit]
Description=Weather UI dashboard (:8089, basic auth, stdlib-only, weather-ui)
After=network-online.target zerotier-one.service
Wants=network-online.target zerotier-one.service

[Service]
Type=simple
User=auditbot
WorkingDirectory=/home/auditbot/weather-dash/ui
ExecStart=/usr/bin/python3 server.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

## 3. U7-2: loopback + версии (коммит 181d4b2)

```diff
--- a/ui/config.py
+++ b/ui/config.py
@@ … Сеть (§2.3) @@
-# честно падает с ERROR в лог).
-BIND_HOSTS = ("192.168.8.146", "10.147.17.101")
+# …; 127.0.0.1 — loopback для Uptime Kuma на той же VM (v1.2.7 §2.3, U7-2):
+# монитор целится в http://127.0.0.1:8089/api/health и не зависит от
+# LAN/ZT-интерфейса. …
+BIND_HOSTS = ("127.0.0.1", "192.168.8.146", "10.147.17.101")
```

ui/server.py: `SERVER_VERSION = "0.4.1"` + docstring v0.4.1. UI_VERSION/app.js — не тронуты (проверено verify: `UI_VERSION = "0.4.0"` в отданном app.js, `Server: weather-ui/0.4.1`).

## 4. U7-3: установка на VM и верификация

Порядок: git pull (33ea9b1) → синк ui/ → md5 → `sudo cp` юнита + `daemon-reload` → kill setsid-процесса → `enable --now`. Строка старта в journal (важно: **version=0.4.1**, а не 0.4.0 — применён бамп из правил задания; в тексте задания фигурировал 0.4.0, правила старше):

```
Sep 24 23:51:24 debian-vitele python3[219166]: INFO static loaded files=15 bytes=302865
Sep 24 23:51:24 debian-vitele python3[219166]: INFO start version=0.4.1
  bind=127.0.0.1,192.168.8.146,10.147.17.101 port=8089 db=…/weather.db auth=yes whitelist=37cols
```

`systemctl status`: **active (running)**; `systemctl is-enabled`: **enabled**.
Слушатели (три адреса, никаких 0.0.0.0):

```
$ ss -tln | grep ':8089 '
LISTEN 0 5 127.0.0.1:8089        ← новый loopback (U7-2)
LISTEN 0 5 10.147.17.101:8089
LISTEN 0 5 192.168.8.146:8089
```

health: loopback/LAN/ZT — все `200 {"status":"ok","db":"ok",…}`; Server-заголовок `weather-ui/0.4.1`.

**Тест Restart=on-failure** (kill -9 MainPID):

```
MAINPID_BEFORE=219166 → sudo kill -9 → 7 c → MAINPID_AFTER=219200, active
journal: Main process exited, code=killed, status=9/KILL
        Scheduled restart job, restart counter is at 1.
health после автоперезапуска: HTTP=200   (простой ~5-7 с)
```

## 5. U7-4: Kuma-монитор + тест Down/Up

Монитор добавлен через socket.io (идемпотентно), запись в kuma.db:

```
76|weather-ui :8089 health (loopback)|http://127.0.0.1:8089/api/health|GET|60|http
```

Метод **GET** (НЕ HEAD — тот даёт 501, U7-6), без auth, интервал 60 с, retries 2 × 60 с. Нюанс настройки: Kuma 2.5.3 на этой VM не принимает форсированный websocket-транспорт сокета — работоспособен дефолтный polling→ws (первый add с `transports:["websocket"]` упёрся в глобальный таймаут, без форса — ОК).

Тест падения (два окна остановки UI — **предупреждение владельцу: суммарно ~8 мин контролируемого простоя UI в 00:14–00:26 MSK**; плюс короче — прогон verify unit-test останавливает юнит ~1.5 мин):

```
heartbeat (UTC; 1=Up, 2=Pending, 0=Down):
1|4|21:21:27   ← Up, ping 4 ms
2| |21:22:27   ← юнит остановлен, retry
2| |21:23:27   ← retry
0| |21:24:27   ← Down (после исчерпания retries)
0| |21:25:27
1|9|21:26:27   ← systemctl start → Up (пинг 9 ms)
```

Up → Down → Up подтверждены сердцебиениями БД Kuma; старт обратно — заодно упражнение restart-логики юнита. Автоперезапуск при падении — отдельно доказан kill -9 (§4).

## 6. U7-5: verify_stage_ui.sh (deploy-tools/, 414 строк)

Три режима (параметр): `local` (клон репо + копия БД, полный прогон), `deploy` (живой сервер, только read-only GET), `unit-test` (остановка юнита → полный прогон на копии → старт юнита). Guards jq/curl из §11; сквозная нумерация V001+; фикстуры ТОЛЬКО на копию; exit по финальному счётчику.

Результаты на VM (после доводки — история фиксов ниже):

```
VERIFY local:     ALL PASSED — 85 OK / 0 FAIL
VERIFY deploy:    ALL PASSED — 75 OK / 0 FAIL
VERIFY unit-test: ALL PASSED — 94 OK / 0 FAIL
```

Группы проверок: G0 окружение/guards · G3 health+auth (401/WWW-Authenticate/charset) · G4 страницы 6×200 + версии · G5 заголовки (CSP на ВСЕХ 6 страницах, no-cache, nosniff, Server) · G6 DOM-ID-грепы 22 шт (урок events.html: timeline, type-chips, sev-chips, fc-zam, fc-sager, fc-table, fc-data, fc-init, fc-fresh, chart-t/p/rain/sun/wind, stat-body, hm-grid, hm-legend, cal-box, chart-trend, cards, card-outdoor, spark-t) · G7 API-конверты (hourly/daily/events/history-400 + forecast: available/issued_values/letter/persistence×3/calc_ts + meta.db_health + export.csv 404-заглушка) · G8 вербы (POST 405+close, POST 401, HEAD→501, path-traversal ×2) · G9 слушатели ss (deploy: loopback+LAN+ZT, нет 0.0.0.0) · G10 фикстуры на копии (letter→конверт, issued_values 10.0/770.0 — база на issued_at, stale, события) · G11 empty-DB→503 initMode («current is empty») + available:false · G13 юнит обратно + слушатели.

История доводки (коммиты d6574c1, 078aabf, 5541a83, 0f73785, 28434ea, 07e038f) — реальные находки, каждая фикс-коммитом: jq-фильтры по факту конвертов (.current.ts, .zambretti.letter); дамп server-лога при провале; **сирота-сервер** от прошлого прогона (pidfile ловил PID субшелла — cleanup-kill промахивался, мёртвая БД отвечала 503 и отравляла следующие прогоны → exec-паттерн + пре-килл порта + детект «ERROR bind failed»); stale-фикстура сдвигом на 30 сут (без UNIQUE-коллизий issued_at); **grep -q под pipefail давал SIGPIPE systemctl** (ложный «юнит не найден»); systemctl stop/start юнита — только под sudo (polkit). На VM установлен jq (apt, 1.6) — требование guard'а §11.

## 7. U7-6/U7-7: спека v1.2.7 (docs-коммит 33ea9b1)

- §2.2 — [Service]-блок синхронизирован с фактическим юнитом (Restart=on-failure, RestartSec=5, WorkingDirectory + ExecStart относительным, StandardOutput/StandardError=journal); добавлен буллет «юнит-файл — в репо: ui/weather-ui.service (U7-1)».
- §2.3 — loopback 127.0.0.1 в BIND_HOSTS (v0.4.1, Kuma на той же VM).
- §3 — «**Не-GET/POST методы не поддерживаются** — 501 базового BaseHTTPRequestHandler (do_HEAD не переопределяется). Uptime Kuma настроен на GET /api/health — 501 не влияет на мониторинг (решение согласовано — U7-6)».
- Changelog v1.2.6 → v1.2.7 (3 строки). Попутный фикс: дублированная строка №3 в changelog v1.2.6 (сырой `|` ломал markdown-таблицу) — убрана.

## 8. U7-8: почва U6 (без реализации)

Подтверждено в работающем коде (verify V063-V065): `/api/export.csv` отвечает **404** с телом `{"error":"endpoint planned for U6, absent in U0-U5"}` (server.py, метод _route) — заглушка на месте, JSON_MAX_BYTES уже оговорён в config как исключение §5.9; `/api/meta` отдаёт **db_health** (парсинг wmeta→JSON с деградацией до null и WARN в лог) плюс schema_migrations/collector_log — U6 остаётся добавить экран настроек и CSV-выдачу, серверной земли подготовлено достаточно.

## 9. Проверки кода

`python3 -m py_compile ui/server.py ui/config.py` — OK; `bash -n deploy-tools/verify_stage_ui.sh` — OK. JS не менялся (node --check не применим). `wc -l`: verify_stage_ui.sh **414**, weather-ui.service **30**.

## 10. Что не сделано и почему

1. **Х-1: letter не появился — НЕ ЧИНИЛ** (прямая инструкция Х-1b). Причина локализована: runtime-агрегатор на VM = pre-U5 (md5 совпадает с 8d362d5); деплой v0.4.0 не включал синк stage-b в runtime. Готовое решение — §0; жду подтверждения владельца/ревьюера (или разрешение — выполню одной командой).
2. **Х-2: events пусто — вне юнита U7** (вопрос этапа B владельцу; агрегатор событий в runtime-stage-B либо не пишет, либо генерация не реализована — требует решения владельца).
3. **Не-GET/HEAD** — не реализуем сознательно (согласовано, U7-6; зафиксировано в спеке §3).
4. Простой UI при тестах — суммарно ~12 мин (два окна остановки ~3.5-4 мин для Kuma-теста + короткие окна прогонов unit-test); все старты/остановки — через штатный systemctl, юнит каждый раз возвращался в active.

## 11. Ссылки для самопроверки владельца (репо публичный)

- Юнит: https://github.com/jinny21093/vitele_meteo/blob/main/ui/weather-ui.service
- Verify: https://github.com/jinny21093/vitele_meteo/blob/main/deploy-tools/verify_stage_ui.sh
- Спека v1.2.7: https://github.com/jinny21093/vitele_meteo/blob/main/docs/weather-ui-spec.md
- Diff U7 целиком: https://github.com/jinny21093/vitele_meteo/compare/b966a05...07e038f
- Отчёты: https://github.com/jinny21093/vitele_meteo/tree/main/docs/reviews
- Живые проверки: `curl -u <cred> http://192.168.8.146:8089/api/forecast | grep -o '"letter"[^,]*'` (сейчас null — §0); `systemctl status weather-ui`; `ss -tln | grep 8089`; Kuma → монитор «weather-ui :8089 health (loopback)» (id=76).
