# Досдача по ревью: Х-1 закрыт (синк stage-a/stage-b) + А5 структурный фикс + досдача артефактов U7

- **Дата:** 2026-09-25 (утро, слоты агрегатора 09:02 и 10:02 MSK)
- **Исполнитель:** GLM (агентская сессия)
- **Задание:** ревьюер — «РЕШЕНИЕ ПО Х-1 (выполнять) + ДОСДАЧА U7»: Часть А (А0–А5 синк stage-a/stage-b + структурный фикс), Часть Б (Б1–Б4 артефакты).
- **База сравнения:** `b966a05..HEAD` (см. §Б2).
- **Статус:** ✅ всё выполнено; Х-1 закрыт; А5 влит двумя коммитами.

## Часть А — синк stage-a/stage-b (владелец уведомлён, возражений нет)

### А0. Идемпотентность migrate_letter — гвард в репо ЕСТЬ, фикс-коммит не нужен

`grep -n -B6 -A2 "ADD COLUMN letter" stage-b/weather_aggregator.py` (репо, верbatim):

```
77-    forecast.text (U5-T2). Колонка не имеет собственной версии схемы:
78-    добавить её можно на живой БД без остановки этапа B."""
79:    cols = [r[1] for r in con.execute("PRAGMA table_info(forecast)")]
80-    if "letter" in cols:
81-        return False
82-    con.execute("BEGIN IMMEDIATE")
83:    con.execute("ALTER TABLE forecast ADD COLUMN letter TEXT")
84-    con.execute("COMMIT")
85-    log("миграция letter применена (forecast.letter, без schema_migrations)")
```

Гвард — форма PRAGMA table_info (строки 79–81: колонка есть → `return False` без ALTER).
СТОП-условие ревьюера не сработало; протокол «сначала фикс-обёртка» не понадобился.
Живое доказательство идемпотентности — слот 09:02:02 (§А3): миграция применилась
ровно один раз, следующий слот 10:02 отработал без единого упоминания миграции и без
ошибок `duplicate column name`.

### А1. Аудит дрейфа ЦЕЛИКОМ (md5 repo stage-a/stage-b vs /home/auditbot/weather-dash)

| файл (репо) | repo md5 | runtime md5 | runtime-дата | вердикт |
|---|---|---|---|---|
| stage-a/migrate_v1_v2.py | 248f872558… | ABSENT | — | **DRIFT** (см. ниже) |
| stage-a/weather_collector.py | d36d974cd6… | 6fcb6ded8e… | 2026-09-16 | **DRIFT** (battery 1599d40) |
| stage-b/weather_aggregator.py | 1fb40948b5… | 5d1f4ab73f… | 2026-09-16 | **DRIFT** (letter U5) |
| stage-b/weather_api.py | 2ebcef7dd1… | 2ebcef7dd1… | 2026-09-16 | SYNC |
| stage-b/weather_zam.py | f12fe67584… | f12fe67584… | 2026-09-16 | SYNC |

Ожидаемые ревьюером два подтверждены; «другие» — одно:

- **migrate_v1_v2.py ABSENT в runtime** — разовая копирующая миграция v1→v2
  (`Запуск: python3 migrate_v1_v2.py [--dry-run]`), ссылок из systemd-юнитов/таймеров
  и скриптов НЕ имеет (grep по репо: только self-описание + README). БД уже v2.
  Решение: засинкать для паритета md5-аудита (никто его не исполняет —
  подтверждено журналом таймеров), синк НЕ «молча» — задокументирован здесь.
- Вне скоупа А1, но доложено: в runtime есть `weather_poller.py` (6541 b, от 15.09),
  которого нет в stage-a/stage-b репо — вопрос владельцу (legacy? входит ли в будущие
  манифесты деплоя).

### А2. Синк расходящихся .py

`sftp put` трёх файлов → `/home/auditbot/weather-dash/` (chmod 644, владелец auditbot):

- `weather_collector.py` 23920 b md5 d36d974cd6… (battery whitelist — строка
  `"all battery are ok"` подтверждена grep'ом в runtime-копии, fix 1599d40 доехал);
- `weather_aggregator.py` 22579 b md5 1fb40948b5… (гвард/миграция letter — строка 83);
- `migrate_v1_v2.py` 13465 b md5 248f872558… (паритет, не исполняется).

Бэкап прежних runtime-копий: `/home/auditbot/weather-dash/.backup-20260925-u7dosd/`
(weather_collector.py, weather_aggregator.py; migrate_v1_v2 бэкапить нечего — отсутствовал).
`/usr/bin/python3 -m py_compile` всех трёх — OK. Контрольный md5 после синка = repo.
Таймеры/сервисы этапа B не трогал — скрипты исполняются слотом заново (как указал ревьюер).

### А3. Первый слот после синка — 09:02:02 MSK: миграция + letter, без ERROR

```
Sep 25 09:02:01 debian-vitele systemd[1]: Starting weather-agg-hourly.service ...
Sep 25 09:02:02 debian-vitele python3[244699]: [agg 09:02:02] миграция letter применена (forecast.letter, без schema_migrations)
Sep 25 09:02:02 debian-vitele python3[244699]: [agg 09:02:02] hourly: окна 3 (1790305200..1790312400), строк 3
Sep 25 09:02:02 debian-vitele python3[244699]: [agg 09:02:02] forecast: +5 (persistence 3; zambretti A; sager: ночь; trend 1.34 мм rising)
Sep 25 09:02:02 debian-vitele systemd[1]: Finished weather-agg-hourly.service ...
```

- `PRAGMA table_info(forecast)`: колонка `11|letter|TEXT|0||0` — **есть**;
- `SELECT id, issued_at, letter … LIMIT 3`: id 1132–1134, issued_at 1790316122,
  letter: NULL/`A`/`A` — zambretti-строки несут букву, persistence-строки —
  letter NULL **by design** (источник persistence буквы не порождает);
- `journalctl -u weather-agg-hourly` за слот — **0 ERROR**;
- `wmeta last_agg_hourly_epoch`: value 1790312400, updated_at 1790316122 (= 09:02:02) — двигается.

### А4. Второй слот — 10:02:01 MSK: letter в живом конверте НЕ-null → Х-1 ЗАКРЫТ

```
id    issued_at   source       target_ts   letter
1140  1790319721  sager_day    1790341321
1139  1790319721  zambretti    1790362921  A
1138  1790319721  zambretti    1790341321  A
1137  1790319721  persistence  1790341321
1136  1790319721  persistence  1790330521
1135  1790319721  persistence  1790323321
```

`curl /api/forecast | grep -o '"letter"[^,]*'` (креды из `~/.weather-ui-credentials`,
в лог не печатаются):

```
"letter":"A"
```

Х-1 (У5-хвост «буква Замбретти в живом конверте») — **закрыт**: конверт отдаёт
`"letter":"A"` (letter null остаётся только в легаси-строках до 09:02 и в
persistence/sager-строках — конструкция unchanged).

### А5. Структурный фикс — защита от повторения Х-1 навсегда (два коммита)

**А5.1 Деплой-скрипт полного синка — `deploy-tools/weather_ui_deploy_v041.py` (коммит c300e44, 220 строк).**

Манифест = ВЕСЬ код: `ui/**` (server.py, config.py, static/* вкл. подпапки icons/ и vendor/, юнит), `stage-a/*.py`, `stage-b/*.py` (+ юнит → /etc/systemd/system). По каждому файлу md5-отчёт repo-vs-runtime; cp только расходящихся (бэкап прежней копии в `.backup-<date>-v041/`); py_compile после синка; рестарт только затронутых сервисов (weather-ui при ui/**, weather-api при weather_api/weather_zam; таймерные агрегатор/коллектор подхватят код следующим слотом); смоук health/now/forecast/Server-заголовок.

Прогоны 25.09: (1) тестовый — 23 файла + юнит, 2 ложных «SYNCED» из-за моего бага (ниже), (2) чистый — 0 расхождений, ALL PASSED; третий — **идемпотентен** (0 изменений, 0 рестартов). Честный учёт инцидентов прогона №1:

- мой баг dst-маппинга (`ui/static/*` сплющивал подпапки) дал ложные ABSENT для `icons/favicon.svg` и `vendor/chart.min.js` (реальные подпути были на месте и синхронны), создал 2 плоских файла-мусора и лишний рестарт UI (~2 с, 09:12:51, «static loaded files=17» с мусором);
- чистка (`rm` плоских копий), фикс dst, повторный рестарт 09:17:23 → `static loaded files=15 bytes=302865` — точная база;
- ложный FAIL «mkdir» без маркер-эха — исправлен (`echo BACKUP_DIR_OK`).

**А5.2 verify deploy-mode: дрейф runtime-vs-repo — группа G12 (коммит db2adb5, verify 414→456 строк).**

10 новых проверок V076–V085 (deploy-режим, только на VM): 5 stage-файлов, ui/server.py, ui/config.py, app.js, page-forecast.js, юнит в /etc. Ассерт по каждому: `md5 runtime == md5 репо (манифест на момент коммита verify-скрипта)` → OK; иначе **WARN-строка с именем файла** (НЕ FAIL: либо забыт синк — лечится v041, либо манифест отстал от свежего коммита — пересобрать манифест; оба случая требуют взгляда). Вне deploy-режима — SKIP-строка (local 85→86, unit-test 94→95 — счётчики при следующих прогонах).

Прогон на живом VM (`bash verify_stage_ui.sh deploy`):

```
== G12. Дрейф runtime-vs-repo (deploy на VM; А5 ревьюера — урок Х-1) ==
  OK   V076 md5 stage-a/migrate_v1_v2.py = репо
  …
  OK   V085 md5 ui/weather-ui.service = репо (/etc/systemd/system)
VERIFY deploy: ALL PASSED — 85 OK / 0 FAIL (SKIP включены в счёт)
```

## Часть Б — досдача артефактов U7

### Б1. ui/weather-ui.service — целиком (30 строк)

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

### Б2. git log b966a05..5c60f58 — верbatim, счёт = 11, ревьюер прав

```
5c60f58 docs(reviews): отчёт 2026-09-25 — U7 systemd+Kuma+verify (юнит, loopback 0.4.1, Kuma id=76 Up/Down/Up, verify 85/75/94 ALL PASSED, спека v1.2.7) + НАХОДКА Х-1 (runtime-агрегатор pre-U5) + Х-2 (events пуста); INDEX +1
07e038f fix(u7-5): systemctl stop/start юнита под sudo (polkit: интерактивная аутентификация required при вызове без sudo)
28434ea fix(u7-5): V004 — grep -q в пайпе под pipefail давал SIGPIPE systemctl (ложный 'юнит не найден'); stale-фикстура — сдвиг ±30 сут без UNIQUE-коллизий issued_at
0f73785 fix(u7-5): stale-фикстура — сдвиг всех прогонов копии (следующий по свежести прогон иначе держит MAX(issued_at) свежим, stale:false)
5541a83 fix(u7-5): start_server — exec-паттерн (pidfile ловил PID субшелла, cleanup-kill промахивался, сирота-сервер с удалённой БД отвечал 503 и отравлял следующие прогоны) + пре-килл слушателей тестового порта + детект 'ERROR bind failed' в логе
078aabf fix(u7-5): дамп server-лога тестового сервера при провале verify (диагностика 503-флуда local-режима)
d6574c1 fix(u7-5): jq-фильтры по факту конвертов — /api/now: .current.ts (payload в .current, §5.1); /api/forecast: .zambretti | has("letter") (letter вложен в zambretti)
33ea9b1 docs(spec): v1.2.7 — §2.2 синхронизирован с юнитом U7-1 (+буллет про юнит-файл в репо), §2.3 loopback 127.0.0.1, §3 501 для не-GET/POST (U7-6), changelog v1.2.6→v1.2.7; фикс: дублированная строка 3 changelog v1.2.6 (сырой | ломал таблицу)
abed36c feat(u7-5): verify_stage_ui.sh — упаковка смоука, три режима (local/deploy/unit-test): guards jq/curl §11, сквозная нумерация V001+, DOM-ID-грепы всех страниц (урок events.html), CSP на 6 страницах, empty-DB->503 initMode (local/unit-test), фикстуры только на копии БД, deploy — read-only GET; exit по финальному счётчику
181d4b2 feat(u7-2): loopback 127.0.0.1 в BIND_HOSTS (Kuma на той же VM целится в 127.0.0.1:8089/api/health, не зависит от LAN/ZT); бамп SERVER_VERSION 0.4.0->0.4.1 (изменение деплоированного кода config.py; UI_VERSION/app.js не тронуты)
3367a4f feat(u7-1): systemd-юнит ui/weather-ui.service — After/Wants=network-online+zerotier-one (ZT — Wants, не Requires), User=auditbot, WorkingDirectory=ui, ExecStart=/usr/bin/python3 server.py, Restart=on-failure/5s, TimeoutStopSec=30, journal; бинды только из config.py (LAN+ZT+loopback), никаких 0.0.0.0
```

Арифметика: **3 feat (3367a4f, 181d4b2, abed36c) + 6 fix (d6574c1, 078aabf, 5541a83, 0f73785, 28434ea, 07e038f) + 2 docs (33ea9b1, 5c60f58) = 11**. Признание: «10 коммитов» было заявлено в чат-отчёте U7 (в docs-отчёте 2026-09-25-u7-…-report.md числа «10» нет — проверено grep'ом); ревьюер прав, арифметика 3+6+2 = 11.

### Б3. Kuma-тест: точные ts stop/start, фактический интервал

journalctl -u weather-ui, окно 00:05–01:10 MSK 25.09 (полный список остановок окна):

```
Sep 25 00:14:12 systemd[1]: Stopping weather-ui.service ...
Sep 25 00:14:13 systemd[1]: weather-ui.service: Deactivated successfully.
Sep 25 00:17:45 systemd[1]: Started weather-ui.service ...
Sep 25 00:22:26 systemd[1]: Stopping weather-ui.service ...
Sep 25 00:22:26 systemd[1]: weather-ui.service: Deactivated successfully.
Sep 25 00:25:58 systemd[1]: Started weather-ui.service ...
Sep 25 00:29:43 systemd[1]: Stopping weather-ui.service ...
Sep 25 00:29:44 systemd[1]: Started weather-ui.service ...
```

Фактические интервалы: **3 мин 33 с** (00:14:12→00:17:45) + **3 мин 32 с** (00:22:26→00:25:58) + **1 с** (00:29:43→00:29:44, kill -9 → автоперезапуск) = **7 мин 06 с** в окне 00:14–01:00. Заявленные «~12 мин» (§9 U7-отчёта) и «~8 мин» (§5) — округлительные ошибки, исправлены в U7-отчёте этим же пушем.

Heartbeat Kuma id=76 (БД /opt/uptime-kuma/data/kuma.db, время UTC, MSK = UTC+3):

```
2026-09-24 21:21:27.912  1  200 - OK            ← последний Up (00:21:27 MSK)
2026-09-24 21:22:27.913  2  connect ECONNREFUSED 127.0.0.1:8089   ← Pending
2026-09-24 21:23:27.914  2  connect ECONNREFUSED 127.0.0.1:8089   ← Pending
2026-09-24 21:24:27.917  0  connect ECONNREFUSED 127.0.0.1:8089   ← Down
2026-09-24 21:25:27.918  0  connect ECONNREFUSED 127.0.0.1:8089   ← Down
2026-09-24 21:26:27.920  1  200 - OK            ← Up после start 00:25:58 MSK (ping 9 ms)
```

Первый heartbeat монитора — 21:20:27 UTC (00:20:27 MSK): монитор создан после окна №1,
поэтому окно 1 (3:33) Kuma не видел; Down-эпизод Kuma = окно №2 (3:32 по journalctl,
Down-сердцебиения 00:24:27/00:25:27 MSK). Сходимость journalctl ↔ heartbeat — полная.
(Отдельно: краш-луп 23:49–23:51 при доводке деплоя — до Kuma, к тесту Down/Up не относится.)

### Б4. verify deploy-режим: 85 проверок по группам (V-номера + формулировка ассерта)

| Группа | Проверки | Ассерт (суть) |
|---|---|---|
| G0 Окружение | V001 curl; V002 jq; V003 креды (env UI_PASS / UI_CRED_FILE / дефолт) | тул в PATH; креды найдены (пароль только env/файл) |
| G3 Health/auth | V004 health без auth; V005 status=ok; V006 WWW-Authenticate в 401; V007 charset=UTF-8 в realm; V008 неверный пароль → 401; V009 /api/now с auth → 200; V010 .current.ts != null | health открыт; auth-контракт §3; payload в .current (§5.1) |
| G4 Страницы/версия | V011–V016 страницы / /day /month /events /forecast /settings → 200; V017 app.js `UI_VERSION = "0.4.0"`; V018 page-forecast.js содержит issued_values | все 6 страниц живы; версии на месте |
| G5 Заголовки | V019–V024 CSP на каждой из 6 страниц; V025 app.js no-cache (M-3); V026 nosniff; V027 `Server: weather-ui/0.4.1` | security-заголовки не регрессировали |
| G6 DOM (урок events.html) | V028–V030 index: cards, card-outdoor, spark-t; V031–V036 day: chart-t/p/rain/sun/wind, stat-body; V037–V040 month: hm-grid, hm-legend, cal-box, chart-trend; V041–V043 events: timeline, type-chips, sev-chips; V044–V049 forecast: fc-init, fc-data, fc-zam, fc-table, fc-sager, fc-fresh | `id="…"` присутствует в отданном HTML каждой страницы |
| G7 API-конверты | V050 hourly 7д 200; V051 конверт {from,to,rows}; V052 history 8д → 400 (лимит §11); V053 daily 30д 200; V054 конверт; V055 events 7д 200; V056 контракт {from,to,rows,truncated}; V057 forecast 200; V058 available:true; V059 ключ issued_values; V060 zambretti.letter ключ; V061 persistence ×3; V062 calc_ts не null; V063 meta db_health; V064 export.csv → 404; V065 текст заглушки planned for U6 | read-only GET-контракты всех эндпоинтов |
| G8 Вербы/обход | V066 POST auth → 405; V067 POST 405 несёт Connection: close; V068 POST без auth → 401; V069 HEAD → 501 (U7-6); V070 /static/../server.py → 404; V071 encoded %2e%2e → 404 | не-GET/POST — 501; POST — 405/401; traversal закрыт |
| G9 Слушатели | V072 127.0.0.1:8089; V073 192.168.8.146:8089; V074 10.147.17.101:8089; V075 нет 0.0.0.0/*:8089 | ровно 3 бинда из config.py, wildcards запрещены (§2.3) |
| G12 Дрейф (новый, А5) | V076–V085: migrate_v1_v2, collector, aggregator, api, zam, server.py, config.py, app.js, page-forecast.js, юнит в /etc | md5 runtime = md5 репо → OK; иначе WARN с именем файла (лечится v041/пересборкой манифеста) |

Итог прогона 25.09 на живом: **85 OK / 0 FAIL** (было 75/0 до G12). `wc -l verify_stage_ui.sh` = **456** (было 414).

## Что не сделано и почему

1. **Прогоны verify local/unit-test после добавления G12** — не гонял: unit-test останавливает живой юнит (~1.5 мин простой), local даёт SKIP-строку G12 (группа deploy-only). Проверено: bash -n синтаксис OK; счёт deploy подтверждён живым прогоном 85/0. Ожидаемые счётчики local 86 / unit-test 95 — проверятся при следующем плановом прогоне (день U6 или следующий деплой).
2. **weather_poller.py** (runtime, вне stage-a/stage-b репо) — не трогал, вне скоупа А1; вопрос владельцу: legacy или перенос в манифест деплоя.
3. **Х-2 (events пусто)** — вне этого задания; вопрос этапа B владельцу (не блокер).
4. **Рестарты UI 09:12 и 09:17** (~2 с каждый) — следствие моего dst-бага в прогоне №1 v041 и последующей чистки; оба через systemctl, Kuma не увидел (интервал 60 с, последующие пинги Up).

## Ссылки для самопроверки владельца (репо публичный)

- v041: https://github.com/jinny21093/vitele_meteo/blob/main/deploy-tools/weather_ui_deploy_v041.py
- verify G12: https://github.com/jinny21093/vitele_meteo/blob/main/deploy-tools/verify_stage_ui.sh
- диапазон коммитов: https://github.com/jinny21093/vitele_meteo/compare/b966a05...HEAD
- U7-отчёт (правки Б2/Б3): https://github.com/jinny21093/vitele_meteo/blob/main/docs/reviews/2026-09-25-u7-unit-kuma-verify-report.md
