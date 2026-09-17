# Метеостанция .101 — roadmap внедрения

**Обновлено:** 2026-09-18 (UI-деплой U0-U4)
**Роль документа:** единственная точка правды для реализации. Канон = ТЗ v2.0
(`network/weatherboard_v2_analitic.md`, исторический артефакт) + дельты из §3
этого файла (свод правок агента и DeepSeek, согласованный владельцем) =
«ТЗ v2.1». При расхождении приоритет у roadmap (DeepSeek, вдогонка §6: канон —
этот roadmap, ТЗ — исторический артефакт). Прочее: `network/weatherstation.md`
— API станции; брифы для DeepSeek и его отзывы — в history.md.

---

## 1. Где мы сейчас

- **Этап 1 (сбор+хранение v1) в строю** с вечера 15.09: `weather_poller.py` (stdlib-only),
  cron 1 мин, `weather.db` (23 REAL + battery, wmeta). На 23:29 — 70 замеров,
  провалов >90 с — 0, journald 15.7M, диск 7.4 ГБ свободно.
- **Сверка ТЗ завершена трижды:** вердикт агента (weather-3: реализуемо, 10 правок) →
  бриф для DeepSeek (weather-4) → отзыв DeepSeek (5 блоков замечаний). Все замечания
  приняты; микро-правки агента — в §3 с пометками.
- Решения владельца (weather-4) зафиксированы в §2. **ЭТАП A ВЫПОЛНЕН (weather-6, ночь 16.09): приёмка ALL_PASS, снепшот — `network/snapshots/2026-09-16-weather6.md`. Этап B открыт.**
- **ЭТАП B ВЫПОЛНЕН (weather-8, 16.09):** агрегаторы hourly/daily, тренды, Zambretti+Sager, REST API :8090, Kuma HTTP-монитор id=75; приёмка `verify_stage_b.sh` → ALL CHECKS PASSED (9/9); снепшот — `network/snapshots/2026-09-16-weather8.md`. Дальше — бэклог C/D (D3, ≥30 дней истории) и UI по `weather-ui-spec.md` (D12).
- **UI U0-U3 = DEPLOYED (v0.2.2, 2026-09-17):** деплой-смоук на vitele — ALL STEPS PASSED (шаги 0–6 + прокси). Запуск `setsid nohup python3 ui/server.py` из `~/weather-dash/ui`, порт **8089**, bind 192.168.8.146 + 10.147.17.101 (LAN+ZeroTier), basic-auth (креды `~/.weather-ui-credentials`, 600), health 200 без auth, статика 13 файлов, /api/history с rain_total_mm. **Запуск вне systemd до U7** (юнит — отдельным заданием; reboot процесс не переживёт). Код = origin/main `2394c8f` (ревью r4-r6 закрыты, смоки 93/0). Для U7 от деплоя: Kuma-монитор для UI — только GET (HEAD даёт 501, do_HEAD нет); 127.0.0.1 вне BIND_HOSTS (спека §2.3 — только явные приватные IP).
- **UI U0-U4 = DEPLOYED (v0.3.0, 2026-09-18):** деплой по чеклисту ревьюера — ALL STEPS PASSED (шаги 0–6 + ZT-бонус; скрипт — `deploy-tools/weather_ui_deploy_v030.py`). U4 «События» (спека v1.2.5 §4.4/§5.5): экран /events — таймлайн по датам, окно 1/7/30/90 дн (дефолт 7, localStorage), чипы 21 тип + severity, бейдж «активно», клик-карточка с context; /api/events — overlap-окно (началось в окне ИЛИ началось раньше и открыто/закрылось после from), фильтр types по скобочной семантике; refreshHeader — окно батареи 1 ч. Лог старта: version=0.3.0, static files=14, dropped=0; /api/events 7д → rows:[] (F-3, гвард n_samples>=720 ещё не открыт). Приёмка: md5-сверка (repo → weather-dash/ui) + чеклист curl + визуал владельцем (браузер, LAN). Код = origin/main `ac4c346`. /api/forecast — по-прежнему 404-заглушка (следующий — U5). Запуск вне systemd до U7.

---

## 2. Реестр решений (менять только с ведома владельца)

| ID | Решение | Источник |
|----|---------|----------|
| D1 | Давление храним в мм рт. ст. (как выдаёт станция) | владелец, weather-1 |
| D2 | Интервал опроса **60 с** (30 с из ТЗ отклонены: датчики обновляются 16–60 с) | владелец, weather-4; DeepSeek ✅ |
| D3 | LLM-слой (NLP-сводки, /ask, ML, аномалии, инсайты) — **бэклог** («хотелки на будущее»); NLP не требует 30 дней — можно рано, если появится провайдер | владелец; DeepSeek уточнение |
| D4 | Бэкапы: ежедневно `VACUUM INTO` + gzip, ротация 14 дней (~600 МБ) + **месячный «холодный»** с ротацией 12 мес (~600 МБ, ручная чистка); NAS нет, rsync наружу нет; RPO ≤ 1 сутки | владелец + DeepSeek дополнение |
| D5 | Наблюдаемость: `collector_log` + `materializer_log` + **Kuma Push-монитор** (не Prometheus — хост и так нагружен) | агент + DeepSeek детализация |
| D6 | Коллектор — systemd-юнит (не cron); без watchdog | агент + DeepSeek |
| D7 | Часовые пояса: БД — epoch UTC; сутки для агрегатов — 00:00 MSK; `tz=Europe/Moscow, tz_offset_seconds=10800, tz_policy=fixed_offset` (DST нет с 2014) | агент + DeepSeek упрощение |
| D8 | Sanity-чек каждой записи в коллекторе; провал → `collector_log status='sanity_fail'` + явный push `status=down` в Kuma | DeepSeek; границы уточнены агентом |
| D9 | Ветер: в hourly — суммы `sum_sin/sum_cos/n` (аддитивны) + mode; mode суток — напрямую из raw; wind_run — через dt | DeepSeek |
| D10 | Материализаторы: указатели `last_agg_*` в wmeta; пересчёт хвоста 3 ч на каждом прогоне; только завершившиеся окна | DeepSeek |
| D11 | DHCP-резерв .101 на Keenetic — владелец сам (веб-панель) | владелец, weather-4 |
| D12 | UI/дашборд — после этапа B, по отдельному ТЗ владельца `weather-ui-spec.md` (сначала данные и хранение) | владелец |

---

## 3. Дельты к ТЗ v2.0 → v2.1 (свод правок; всё согласовано)

### 3.1 Схема
- Убрать `idx_weather_id` (AUTOINCREMENT PK уже даёт индекс) *(агент)*.
- Новая таблица **`materializer_log(ts, kind, hours_processed, rows_written, latency_ms, error)`** — по аналогии с `collector_log`, иначе «почему agg пустая» — детектив *(DeepSeek)*.
- `wmeta` ключи: `last_agg_hourly_epoch`, `last_agg_daily_epoch`, `tz`, `tz_offset_seconds`, `tz_policy`, `station_mac`, `station_ip`, `schema_version`.
- Событие **`RAIN_COUNTER_RESET`**: отрицательная дельта `rain_total_mm` = сброс счётчика станции — rate не считать, событие с контекстом *(DeepSeek, обязательно)*.

### 3.2 PRAGMA / SQLite
- `journal_mode=WAL` — один раз (персистентен на файле); **`synchronous=NORMAL`, `busy_timeout=5000` — ставить на КАЖДОМ соединении** (коллектор, материализатор, бэкап, API) *(DeepSeek)*.
- `wal_autocheckpoint=1000` (дефолт) + `journal_size_limit=67108864` *(DeepSeek)*.
- Бэкап — **только `VACUUM INTO` / `.backup`** (cp битый из-за `-wal/-shm`); после — `PRAGMA integrity_check` на копии + лог результата *(DeepSeek)*.

### 3.3 Коллектор v2
- `urlopen(..., timeout=...)` обязателен — в v1 уже есть (8 с + 1 retry); сохранить.
- **Sanity-чек** каждой записи — границы **по каждому полю** (weather-7, «вдогонка» §2; прежние единые 550–850 мм рт.ст. = 733–1133 гПа шире физики Земли — отменяли саму проверку): `pressure_abs_mmhg` 500–820 (высота до ~3000 м), `pressure_rel_mmhg` 680–820, `outdoor_temp_c` −60…+60, `indoor_temp_c` −45…+50 (решение владельца 16.09: дача в Карелии, с. Видлица — редкие морозы до ~−40; у DeepSeek −20, первая оценка агента −30), влажность 0–100, `wind_ms`/`gust_ms`/`wind_avg2_ms`/`wind_avg10_ms` 0–75, `wind_max_daily_ms` 0–90, `rain_rate_mmh` 0–500, `light_wm2` 0–1500, `uvi` 0–15; `ts` монотонен (допуск −5 с). Реализация — `sanity_check(row) -> [нарушения]`. Провал → `collector_log status='sanity_fail'` (error=`поле=знач;...`) + **явный push `status=down` в Kuma, в msg поле-нарушитель** `sanity_fail: поле=знач,...` (до 6 шт, полный список — в БД) *(DeepSeek + weather-7)*.
- `BATTERY_LOW`: raw-текст хранить целиком (`battery_raw`); оценка — **whitelist известных OK-строк** (`BATTERY_OK_PATTERNS`, сейчас только «all battery are ok»): всё остальное — LOW, fail-safe пусто/неизвестно = LOW (weather-7; прежний regex `\bok\b` матчи́л «not ok» → ложный OK). Новые OK-варианты — дополнять в `BATTERY_OK_PATTERNS` и в `weatherstation.md` *(DeepSeek, вдогонка §1)*.
- **Миграция cron→systemd: cron-строку удалить ДО `systemctl enable --now`** — иначе двойной опрос (данные не разъедутся из-за `INSERT OR REPLACE`, но `collector_log` замусорится) *(DeepSeek)*.

### 3.4 systemd-юнит (`weather-collector.service`)
```ini
[Unit]
Description=Weather station collector (.101 -> weather.db v2)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=auditbot
WorkingDirectory=/home/auditbot/weather-dash
ExecStart=/usr/bin/python3 /home/auditbot/weather-dash/weather_collector.py
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5
[Install]
WantedBy=multi-user.target
```
(Wants+After — DeepSeek; StartLimit* — DeepSeek; User/WorkingDirectory — агент; watchdog не ставим — agreed.)

### 3.5 Агрегаты / материализаторы
- `v_hourly`: хранить **суммы** `wind_dir_sum_sin`, `wind_dir_sum_cos`, `wind_n` — аддитивны, суточный вектор = Σ + `atan2`; хранить и mode (16 румбов), и векторное среднее *(DeepSeek)*.
- Mode направления **суток — считать напрямую из raw** (1440 строк, копейки); mode от mode ≠ mode *(DeepSeek; гистограмма 16 бинов признана избыточной)*.
- `wind_run`: `SUM(wind_avg2_ms × dt_sec)`, `dt = ts − prev_ts` (dt>120 с — пропуск; gap>300 с — NULL) *(DeepSeek)*.
- Указатели прогресса в wmeta (D10); **хвост 3 часа пересчитывать** каждый прогон (DELETE+INSERT — late-data-safe); агрегировать только завершившиеся окна (`hour_epoch+3600 <= now`) *(DeepSeek)*.
- Гонки: WAL — читатели/писатели не блокируют друг друга; санкционировано один писатель на таблицу *(DeepSeek)*.
- journald: `SystemMaxUse=500M` в `/etc/systemd/journald.conf` (сейчас занято 15.7M — профилактика на будущее). ⚠️ Системная правка, входит в этап A с ведома владельца *(DeepSeek)*.
- NTP: `timedatectl` → **NTPSynchronized=yes подтверждено 15.09**; контроль в префлайте каждого этапа *(DeepSeek)*.

### 3.6 Эксплуатация
- Бэкап-джоб — **systemd timer** (daily 04:20 MSK; «холодный» — 1-го числа 05:10) + **df-guard: свободно <1.5 ГБ → пропуск с алертом; проверка строго ДО `VACUUM INTO`** (иначе битый бэкап + переполненный диск); при пропуске — три независимых следа: `collector_log` (статусы `backup_ok|backup_skipped|backup_failed`), journald, Kuma push down *(DeepSeek + weather-7)*.
- Kuma **Push-монитор**: коллектор после успешной записи шлёт `?status=up&msg=<замеров за час>|last_ts&ping=<latency>`; grace = 2× интервал. Ловит «процесс жив и реально пишет», а не просто «запущен» *(DeepSeek)*.
- Kuma **второй Push-монитор «Метеостанция .101 — бэкап (push)»** (weather-7): после успешного бэкапа — `up backup_ok <размер>`; при пропуске/ошибке — `down` с причиной. На монитор «сбор» слать нельзя — коллектор затрёт down своим up через 60 с. URL — `kuma_push.conf`, строка `backup_url=` *(агент, вдогонка §4)*.
- Rolling-события (HEATWAVE, DRY_SPELL, CALM) — в материализаторе, без отдельного engine.py *(DeepSeek)*.
- CSV-экспорт — эндпоинт этапа B; `set_authorizer` для /ask — этап C *(DeepSeek)*.

---

## 4. Этап A — пакет работ (P0) «фундамент»

| # | Работа |
|---|--------|
| A0 | Префлайт (read-only): NTP, ts=epoch, df, cron-строка — **выполнен 15.09, всё зелёное** |
| A1 | Снепшот v1: `VACUUM INTO` + `integrity_check` + gzip → `~/backups-archive/weather-v1-<дата>/` |
| A2 | Схема v2: таблицы по дельтам §3.1–3.2, миграция данных v1 (копирующая, с пересчётом L1), `schema_migrations` v2 |
| A3 | Коллектор v2 `weather_collector.py`: L1 (точка росы, wind chill, heat index, 16 румбов, ΔP, wind run, rain rate через типпер + RAIN_COUNTER_RESET, rain_event, GDD Tbase=5), collector_log, sanity (§3.3), PRAGMA per-connection, retry-safe INSERT |
| A4 | Юнит weather-collector.service (§3.4) → **сначала удалить cron-строку** → enable --now |
| A5 | Бэкап-джоб: weather-backup.service+timer (§3.6) |
| A6 | Kuma Push-монитор: Push://...weather-collector, grace 120 с |
| A7 | journald SystemMaxUse=500M |

**Чек-лист приёмки (всё — ✅ или этап не закрыт):**
- [ ] `crontab -l | grep weather` → пусто; юнит active ≥ 15 мин
- [ ] `collector_log`: ровно 1 ok/мин, sanity_fail=0
- [ ] `UNIQUE(ts)` — 0 конфликтов, ts продолжают ряд v1 без разрыва
- [ ] L1-поля в разумных пределах (Td ≤ T_out; румбы из 16; ΔP мал)
- [ ] Ручной запуск бэкапа: файл создан, integrity_check ok, ротация работает
- [ ] Kuma: монитор зелёный, в msg видны last_ts и счётчик
- [ ] `systemctl restart` юнита → без дублей и потери минуты
- [ ] После часа работы: покрытие 100 %, БД растёт ~1 строка/мин
- [ ] Снепшот + history + зеркало обновлены

**Откат:** стоп юнита → вернуть cron-строку v1 → v1-поллер продолжает (данные v1 не затёрты, миграция копирующая).

**Правки «вдогонку» DeepSeek (weather-7, 16.09) — применены после приёмки:**
- battery → whitelist (вдогонка §1: regex `\bok\b` матчи́л «not ok»), fail-safe пусто=LOW;
- sanity → границы по полям (вдогонка §2; таблица — §3.3), функция `sanity_check(row)`;
- Kuma push down при sanity_fail — msg содержит поле-нарушитель (вдогонка §3);
- df-guard — проверка до `VACUUM INTO`; пропуск = collector_log + journald + Kuma-монитор «бэкап» (вдогонка §4);
- **приёмка этапа A автоматизирована: `~/weather-dash/verify_stage_a.sh`** (10 проверок; приёмка = вывод `ALL CHECKS PASSED`) — вдогонка §5;
- battery-проверка приёмки — на ОТРИЦАТЕЛЬНОМ кейсе (юнит-тесты `battery_is_ok`: «All battery are ok» → OK; «not ok»/пусто → LOW), не только на текущем OK.

---

## 5. Этап B — пакет работ (P1) «агрегаты, прогнозы, API»

| # | Работа |
|---|--------|
| B1 | Материализатор hourly/daily (systemd timer'ы :02 / 00:05 MSK; инкрементально, хвост 3 ч; materializer_log) |
| B2 | Тренды 1h/3h/6h по временным окнам (не LAG-строкам) + классификация барической тенденции |
| B3 | Прогнозы: persistence + Zambretti (+Sager днём) → `forecast` |
| B4 | REST API-скелет: /now, /history, /daily, /events, /forecast (+CSV-эндпоинт); LAN-only bind, порт свободен (:8090), basic auth; стек — stdlib `http.server` или Flask, решается на этапе |
| B5 | Kuma HTTP-монитор API + контрольное сравнение агрегатов с raw за сутки |

Месячные/годовые агрегаты — можно перенести в этап B2+ или B-хвост: дёшево, не блокирует.
Приёмка этапа B — аналогично этапу A, скриптом `verify_stage_b.sh` (сделать в рамках этапа; DeepSeek, вдогонка §5).

**Реализация (weather-8, 16.09) — всё выполнено:**
- B1: `weather_aggregator.py` (stdlib) — таймеры **:02** (hourly, хвост 3 ч) и
  **00:05 MSK** (daily, 2 завершившихся MSK-суток), `Persistent=true`;
  `materializer_log` на каждый прогон; указатели `last_agg_*` в wmeta;
  rolling-события дня — идемпотентно (DELETE+INSERT по event_type+ts_start),
  гвард n_samples≥720; DRY_SPELL: solar-условие ТЗ упрощено до rain_7d==0;
- B2: тренды 1h/3h/6h — **временные окна** (avg 10-мин концов, не LAG-строки),
  классификация ±0.5/±1.5 мм рт.ст. (ТЗ §4) — отдаются в API `/now`;
- B3: forecast: persistence +1h/+3h/+6h; **zambretti** (Beteljuice, en+ru) +6h/+12h
  (миграция v3: `forecast.text`); **sager_day** упрощённый (облачность через
  solar/SOLAR_POT ~61°N), 10..15 MSK; TTL 30 дней;
- B4: `weather_api.py` (stdlib http.server): **:8090**, bind 0.0.0.0 (NAT наружу
  закрыт; доступ LAN + ZeroTier), **basic auth** (`api_auth.conf`, 600 — в доках
  НЕ публикуются), `/health` без auth; /now /history /hourly /daily /events
  /forecast /csv; БД read-only (mode=ro + query_only); юнит `weather-api.service`;
- B5: Kuma **HTTP-монитор id=75** «API (http)» → /health, 60 с; `verify_stage_b.sh`
  — 9 проверок, включая сверку агрегатов с raw (n, avg ±0.05 °C, rain ±0.3 мм).
- Приёмка: **ALL CHECKS PASSED (9/9)**; первые прогоны: v_hourly 12 окон (вся
  история), v_daily 1 сутки, сверка raw-vs-agg: n=100=100, dt=0.0, dr=0.0.

---

## 6. Бэклог (вне текущих этапов)

- **Этап C (отложен, D3):** /ask (read-only валидатор, set_authorizer, ask_log) + NLP-сводки — при появлении LLM-провайдера/ключа (решение владельца).
- **Этап D (бэклог):** ML-прогноз (LightGBM/Prophet — оценка на 1 vCPU), Isolation Forest, корреляции-инсайты; условие ≥30–90 дней истории.
- MQTT-публикация в уже работающий mosquitto (vitele) — дёшево, если пригодится UI/HA.
- UI/дашборд — после B, по `weather-ui-spec.md` (владелец, документ в работе).

---

## 7. Как работаем (процесс)

- **Этап = задача = сессия.** До: бэкап/снепшот. После: приёмка по чек-листу этапа. Между этапами — неделя-две живого наблюдения (аналог «этапа 1: смотрим вживую»).
- **Ченжлог:** общий журнал работ — `docs/history.md` (записи weather-N по task ID — это и есть ченжлог проекта, отдельный CHANGELOG не заводим, чтобы не удваивать). Версии документов — в §8 здесь; снепшоты — `network/snapshots/` (неизменяемые).
- **Связка с DeepSeek:** после каждого этапа — короткий бриф владельцу (как weather-4) для показа DeepSeek; его отзыв → дельты в §3 → следующий этап.
- Права и границы: изменения только на vitele в рамках этапа; системные правки (journald) — отдельной строкой с ведома владельца; станция .101 — read-only, как и прежде.

## 8. История документа

- **v1 (weather-5, 15.09 вечер):** создан. Вобрал: 10 правок агента (weather-3) + 5 блоков
  замечаний DeepSeek (отзыв на бриф weather-4) + микро-правки агента к ним (границы sanity,
  явный push down, df-guard, systemd timer'ы) + решения владельца weather-4 (D1–D12).
  Префлайт A0 выполнен: NTP=yes, ts=epoch UTC, cron-строка зафиксирована.
- **v1.1 (weather-6, 16.09 ночь):** ЭТАП A выполнен и принят (ALL_PASS); §1 обновлён; этап B открыт.
- **v1.2 (weather-7, 16.09):** «вдогонка» DeepSeek после приёмки этапа A: battery→whitelist, sanity→по полям (§3.3), Kuma down-msg с полем-нарушителем, df-guard до VACUUM INTO (+ статусы backup_* в collector_log, монитор «бэкап»), приёмка автоматизирована (`verify_stage_a.sh`, ALL CHECKS PASSED). Прямо указано: канон — этот roadmap, ТЗ v2.0 — исторический артефакт.
- **v1.3 (weather-8, 16.09):** ЭТАП B выполнен и принят (verify_stage_b.sh 9/9):
  материализаторы hourly/daily (+rolling-события), тренды по окнам, forecast
  (persistence/zambretti/sager_day, миграция v3 forecast.text), REST API :8090
  (basic auth, /health), Kuma HTTP id=75. Детали — снепшот 2026-09-16-weather8.
- **v1.4 (UI-деплой, 18.09):** §1 — U0-U3 = DEPLOYED v0.2.2 (2026-09-17): смоук ALL STEPS PASSED, порт 8089 (LAN+ZT), запуск вне systemd до U7. Код — origin/main 2394c8f.
- **v1.5 (UI U4 + deploy-tools, 18.09):** §1 — U0-U4 = DEPLOYED v0.3.0 (деплой 17.09 20:45, приёмка 18.09): экран «События» §4.4 + overlap §5.5, спека v1.2.5, смоук 105/0. В deploy-tools/ добавлены деплой-скрипты агента (`weather_ui_deploy.py` v0.2.2, `weather_ui_deploy_v030.py` v0.3.0 — ранее жили вне репо; половина будущей deploy-процедуры U7 рядом с verify_stage_ui.sh). Код — origin/main ac4c346.
