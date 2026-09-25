---
date: 2026-09-25
scope: ФИНАЛ U6 — микро-фиксы по вердикту ревьювера (B-1/M-1) + legacy-архив weather_poller + деплой v050 (три режима verify впервые на одной версии)
status: resolved
---
## TL;DR
Вердикт B-1 исполнен ровно в размороженных границах: (1) `limit=`-не-число
теперь 400, а не молчаливый полный экспорт; (2) G15 verify ассертит заголовок
`X-Export-Rows` на 413; (3) реликт `weather_poller.py` заархивирован в
`stage-a/legacy/` (md5-цепочка верифицирована) и удалён из runtime, в
деплой-скрипт введено правило 1b «в runtime нет .py вне манифеста» (WARN).
Деплой v050 выполнен по чеклисту: **Server 0.5.0 / UI 0.5.0, серверный счётчик
статики files=16**, verify unit-test на VM **137/0 ALL PASSED**. Впервые все
три режима verify на одной версии: local **128/0**, unit-test **137/0**,
deploy **111/0**. VM healthy, даунтайм в день деплоя: рестарт <1 c + окно
unit-test 2 c.

## Детали

### 1. fix(u6) `7c67d17` — limit= не-число -> 400 (вердикт B-1)
До фикса: `_safe_int("abc") -> None` сливался с «параметр отсутствует» ->
лимит молча игнорировался -> опечатка клиента давала полный экспорт. Теперь
raw-параметр отделяется от отсутствующего; не-число ИЛИ вне 1..100000 ->
`400 {"error":"invalid limit","max":100000}`. Пограничные случаи:
`limit=` (пустой, keep_blank_values) -> 400; отсутствующий -> без LIMIT
(дефолт); `limit=1.5`, `limit=+1e3` -> 400 (int() не парсит). Клиент не
затронут: page-settings.js шлёт только `&limit=1` на пробник либо вовсе без
limit. Тест в u6_smoke: `limit=abc -> 400` — **57 OK / 0 FAIL** (было 56,
+1 тест). Прогон: `U6_SRC_DB=state/u6_test/test.db`, транскрипт
`state/u6_smoke_final.txt`.

### 2. fix(verify) `e262be8` — G15 ассерт заголовка X-Export-Rows
413-запрос G15 теперь сохраняет заголовки (`-D $WORK/h413.h`, тело по-прежнему
в `$TMPJSON`, повторного запроса нет) и ассертит
`X-Export-Rows: ${NROWS}` (образец — заголовочный ассерт u6_smoke). Локальный
прогон: **V120 «413 несёт X-Export-Rows=726» OK**, verify local **128/0**
(было 127). На VM та же проверка сработала на живых данных:
**V123 «X-Export-Rows=14096»** (14 096 строк в runtime-БД).

### 3. chore(legacy) `286b2bc` — архив реликта + правило 1b
Решение ревьюера после аудита владельцем. Цепочка доказательств:
- Runtime-оригинал ДО удаления: `/home/auditbot/weather-dash/weather_poller.py`,
  6541 байт, 158 строк, mtime 15.09 22:20, md5 `b180ec6d3e88a5dd9dc1ccbb949e700d`.
- Неиспользование зафиксировано: NO_PROCESS (ps), NO_UNIT_FILE/NO_UNIT
  (systemctl), NO_SYSTEMD_REF (/etc/systemd/system), NO_ETC_CRON_REF
  (/etc/cron*, /var/spool/cron), NO_CODE_REF (греп по runtime *.py),
  NO_LSOF_HOLDER. Hit в crontab — ЧУЖОЙ poller (`snmp-dash/poller.py`),
  к weather-dash отношения не имеет.
- Архив: `stage-a/legacy/weather_poller_v1_pre_git.py` = шапка (4 строки:
  «Реликт этапа 1, до-репо, 15.09.2026; схема v1, 23 поля, events не писал;
  заменён weather_collector.py v2. НЕ ИСПОЛНЯТЬ» + метаданные) + тело
  оригинала без изменений. md5 архива целиком
  `9f6fb20360213168e5a351ac72c2d69f` (7271 байт); **срез первых 4 строк даёт
  md5 `b180ec6d…` = runtime-оригинал** (скрипт `scripts/make_poller_archive.py`).
  Режим в git 100644 — исполнимый бит снят.
- `rm` из runtime выполнен ПОСЛЕ коммита архива: md5 до удаления совпал,
  после — POLLER_ABSENT (транскрипт `state/u6_poller_rm.txt`).
- Манифест деплоя v050 дополнительно исключает `stage-a/legacy/`: эмпирически
  проверено, что git-pathspec `stage-a/*.py` матчит вложенные
  (`git ls-files 'docs/*.md'` даёт 22 вложенных) — без исключения реликт
  вернулся бы в runtime при следующем деплое.
- Правило 1b деплой-скрипта: find по DASH+UIDIR (maxdepth 1) минус
  basename-манифест; strays -> WARN (как дрейф G12), не блокирует. Прогон на
  живом: «OK runtime .py полностью покрыт манифестом (7 .py)».

### 4. ДЕПЛОЙ v050 (по чеклисту скрипта, EXPECT 0.5.0/0.5.0)
- **Прогон 1** (транскрипт `state/v050_run2.log`): шаги 0–4 PASSED —
  клон на VM 5c60f58->286b2bc; манифест **24 файла (+ юнит)**; SYNCED 6
  (ui/config.py, ui/server.py, ui/static/app.js, page-settings.js [был ABSENT],
  settings.html, style.css); 1b OK; py_compile OK; рестарт weather-ui;
  **journalctl 17:29:29 `static loaded files=16 bytes=322744`**; смоук 0.5.0 +
  U6 (settings DOM/фильтр, export chunked) OK. Шаг 5 упал: V008 «копирование
  ui/» — verify заливается в `/tmp`, его дефолт `REPO_DIR=SCRIPT_DIR/..=/`.
  Юнит НЕ пострадал (V008 `exit 1` срабатывает ДО systemctl stop).
- **fix(u6-deploy) `6c41ec1`**: `REPO_DIR=/home/auditbot/vitele_meteo` в env
  вызова verify (одна строка).
- **Прогон 2 — финальный** (`state/weather_ui_deploy_v050.txt`,
  `state/v050_run3.log`): **DEPLOY v0.5.0: ALL PASSED**. Синкнуто 0
  (идемпотентность А5-механики), смоук `Server: weather-ui/0.5.0`, шаг 5:
  **VERIFY unit-test: ALL PASSED — 137 OK / 0 FAIL** (V132–137: юнит обратно,
  health 200 loopback, слушатели 127.0.0.1 / 192.168.8.146 / 10.147.17.101).
- **files=16 — сверка факта**: серверная строка при старте
  (`static loaded files=16 bytes=322744`) — v030=14, v040=15
  (+settings.html-заглушка m-14), v050=16 (+page-settings.js). Манифест
  статики = 16 файлов. Ревьюерская формула «+settings.html +page-settings.js»
  сходится от базы v030.
- Даунтайм (journald, MSK): рестарт деплоя 17:29:29 (<1 c); окно unit-test
  17:33:16 -> 17:33:18 (**2 c**). Kuma id=76 (интервал 60 c) — короче одного
  интервала опроса, инцидент не зафиксирован.

### 5. Три режима verify — впервые на одной версии (0.5.0/0.5.0)
| Режим | Где | Счёт | SKIP-состав |
|---|---|---|---|
| local | песочница, копия фикстуры | **128 OK / 0 FAIL** | G9, G12 — вне deploy-контекста (2) + 1 неактуальный |
| unit-test | VM, копии, stop/start юнита | **137 OK / 0 FAIL** | G9, G12 (2) |
| deploy | VM, живой сервер :8089, read-only | **111 OK / 0 FAIL** | POST check-db — только GET (1) |
- Первое предъявление deploy-режима с полным G12 (без WARN «не задеплоен»
  ui/*: U6-код синкнут).
- Попутный фикс `eb3cb6c` fix(verify): G12-манифест md5 ui/server.py
  f3e59efe95 -> 95acbc0405 (после 7c67d17; первый deploy-прогон честно WARN'нул
  «манифест устарел» — штатное поведение, манифест пересобран).

### 6. Коммиты
`7c67d17` fix(u6) limit=400 · `e262be8` fix(verify) G15-заголовок ·
`286b2bc` chore(legacy) архив+1b · `6c41ec1` fix(u6-deploy) REPO_DIR ·
`eb3cb6c` fix(verify) G12-md5 · (этот отчёт — docs-коммит).
Диапазон финала: c10e0cc..HEAD. Продуктовая поверхность фикса — 11 строк
в `ui/server.py` (парсер limit + docstring) и по одной проверке в
u6_smoke/G15; VM-состояние: runtime == HEAD по манифесту (G12 111-й прогона).

## Хвосты и решения
- Х-2 (events пуста, этап B) — не в этом задании, очередь.
- Пара замечаний для истории: (а) verify в unit-test 137 проверок прогоняет
  stop->start за секунды — юнит-секция лёгкая; (б) правило 1b пока только в
  v050-скрипте (verify-G12 продолжает ловить дрейф по захардкоженному
  манифесту — его пересборка остаётся ручной дисциплиной при правках
  продуктовых файлов).
- Открытое у владельца: слоты *:02 агрегатора работают; камера .45 мертва;
  .81 -> NTP .146 — вне U6.
