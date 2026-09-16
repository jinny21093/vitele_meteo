# Метеостанция дачного дома (vitele_meteo)

Домашний проект автоматической метеостанции на даче в Карелии (с. Видлица):
сбор телеметрии с физической станции, хранение в SQLite, материализация
агрегатов, расчёт прогноза (Замбретти + Sager), REST API и веб-дэшборд.
Всё крутится на одноплатном хосте `vitele` (Debian 12, Python 3.11, SQLite 3.40,
1 vCPU / 4 ГБ RAM) внутри доверенной сети; наружу закрыто NAT.

## Архитектура

```
Метеостанция 192.168.8.101
   │  GET /client?command=record  (~921 Б JSON, 23 значения строками)
   ▼
weather_collector.py  (systemd-демон, цикл 60 с)          ── этап A
   │  sanity 14 полей, battery-whitelist, события FROST/
   │  HARD_FREEZE/BATTERY_LOW/SENSOR_MISSING/RAPID_TEMP_*/
   │  RAIN_COUNTER_RESET (dedup 3 ч), Kuma push
   ▼
weather.db  (SQLite v2: WAL, synchronous=NORMAL, busy_timeout,
   │  schema_migrations, surrogate id + UNIQUE(ts),
   │  L1-производные: точка росы (Магнус), heat index
   │  (JAG-TI/Rothfusz), румбы16, ΔP 3 ч, wind_run, rain rate)
   │
   ├─► weather_aggregator.py  hourly :02 / daily 00:05 MSK ── этап B
   │      v_hourly / v_daily (materializer_log, last_agg_*),
   │      тренды (регрессия t_out_slope), rolling-события
   │      HEATWAVE/CALM/DRY_SPELL
   │
   ├─► weather_zam.py  Замбретти (Beteljuice) + Sager (день) → forecast
   │
   └─► weather_api.py  REST :8090, basic auth (api_auth.conf, 600)
          /health (без auth) /now /history /hourly /daily
          /events /forecast /csv  — read-only + query_only

server.py :8089 (fallback 8091)  ── UI-дэшборд (U0–U3)
   index «Сейчас», «Сутки», «Месяц» (heatmap), Chart.js 4.4.4
   локально (без CDN), тёмная тема, CSP, ETag/304, rate-limit

weather_backup.sh  ── 04:20 daily (14 шт) / 1-го числа cold (12 шт)
   df-guard → VACUUM INTO → integrity_check → gzip

Uptime Kuma (на vitele :3001)
   push id=73 «сбор» / push id=74 «бэкап» / HTTP id=75 «API /health»
```

## Структура репозитория

```
stage-a/        сбор + хранение (этап A, в бою с 16.09.2026)
  weather_collector.py    коллектор v2.0.1 (демон 60 с)
  weather_backup.sh       бэкап v2 (VACUUM INTO + gzip + df-guard)
  migrate_v1_v2.py        копирующая миграция v1 → v2 (одна транзакция)
  verify_stage_a.sh       автоприёмка этапа A (10 проверок)
  systemd/                weather-collector.service, weather-backup.service/.timer

stage-b/        материализаторы + прогноз + API (этап B, принят 9/9)
  weather_aggregator.py   hourly/daily (инкрементально по last_agg_*)
  weather_zam.py          Замбретти + Sager → forecast
  weather_api.py          REST API :8090 (stdlib http.server, basic auth)
  verify_stage_b.sh       автоприёмка этапа B (9 проверок, сверка raw-vs-agg)
  weather_api_monitor.cjs создание Kuma HTTP-монитора id=75
  systemd/                weather-agg-hourly/.timer, weather-agg-daily/.timer,
                          weather-api.service

ui/             веб-дэшборд (стадии U0–U3, smoke 89/89)
  server.py               HTTP-сервер (Threading + семафор, shutdown-поток,
                          rw + query_only на запрос, 401 + rate-limit, ETag)
  config.py               BIND_HOSTS LAN+ZeroTier, порт 8089/8091, лимиты
  static/                 index/day/month/forecast/events/settings +
                          page-*.js, app.js, style.css, vendor/chart.min.js

deploy-tools/   создание Kuma Push-мониторов (id=73 «сбор», id=74 «бэкап»);
                креды только через env KUMA_PASS, не хранятся в репо

docs/           roadmap v1.3 (канон), ТЗ станции, аналитика DeepSeek v2/v2.1,
                ui-spec v1.2.2, снепшоты этапов, ревью (GLM + чек-листы U0U1/U2U3)
```

## Этапы (roadmap: docs/weather-roadmap.md)

| Этап | Состав | Статус |
|------|--------|--------|
| A (weather-6/7) | сбор+хранение v2, миграция, бэкапы, sanity, Kuma push | выполнен и принят, в бою |
| B (weather-8) | агрегаторы hourly/daily, тренды, Замбретти/Sager, REST API :8090, Kuma HTTP | выполнен, приёмка 9/9 |
| UI (U0–U7) | дэшборд по weather-ui-spec: U0+U1 приняты ревью, U2+U3 готовы (smoke 89/89) | U4–U7 в работе |
| C | /ask + NLP-запросы | бэклог |
| D | ML-прогноз (нужно ≥30 дней истории) | бэклог |

## Запуск (vitele)

```bash
# коллектор
sudo install -m 644 stage-a/systemd/weather-collector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now weather-collector

# агрегаторы + API
sudo install -m 644 stage-b/systemd/*.service stage-b/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now weather-agg-hourly.timer weather-agg-daily.timer weather-api

# креды API (сгенерировать один раз)
python3 -c "import secrets; print('dacha:' + secrets.token_urlsafe(24))" \
  | sudo tee /home/auditbot/weather-dash/api_auth.conf && sudo chmod 600 ...
```

Kuma-скрипты запускаются с кредами в окружении: `KUMA_PASS=... node weather_push.cjs`.

## Приёмка

- `verify_stage_a.sh` — 10 проверок (юнит, циклы, схема v2, WAL, таймер бэкапа,
  Kuma push, события) → ALL CHECKS PASSED.
- `verify_stage_b.sh` — 9 проверок (v_hourly/v_daily свежесть, forecast, API
  200/401, сверка агрегатов с raw) → ALL CHECKS PASSED.

## Особенности

- Единицы — как на станции: давление в мм рт. ст., температура °C.
- Sanity-граница indoor: **−45…+50 °C** — дом неотапливаемый, зимой в Карелии
  бывают морозы до −45 (решение владельца, roadmap §3.3).
- Дождь считается по приращениям накопительного счётчика типпера со сбросом
  (RAIN_COUNTER_RESET), wind_run — через SUM(wind_avg×dt).
- Все соединения с БД: WAL + synchronous=NORMAL + busy_timeout=5000;
  API и UI открывают БД в режиме `rw` + `query_only` одновременно.
