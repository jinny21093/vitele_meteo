# Снепшот: этап B — агрегаты, прогнозы, REST API (weather-8)

**Дата:** 2026-09-16, 10:47–10:55 MSK
**Объект:** vitele (10.147.17.101 / 192.168.8.146), `~/weather-dash/`
**Предыстория:** этап A (weather-6) + «вдогонка» (weather-7); план — roadmap §5;
канон — roadmap (ТЗ v2.0 — исторический артефакт).

## Что сделано
- **B0 снепшот БД:** `backups-archive/weather-v2-2026-09-16-B/wb-snap.db.gz`
  (VACUUM INTO + integrity_check ok + gzip).
- **B1 материализатор** `weather_aggregator.py` (stdlib-only): таймер **:02** каждый час
  (hourly: хвост 3 ч — D10; первый прогон — вся история), таймер **00:05 MSK** (daily:
  2 завершившихся MSK-суток — D7 fixed +10800); `Persistent=true`; каждая запись — в
  `materializer_log`; указатели `wmeta.last_agg_hourly_epoch/last_agg_daily_epoch`.
- **Rolling-события дня** (в материализаторе, идемпотентно DELETE+INSERT по
  event_type+ts_start, гвард n_samples≥720): HEATWAVE (tmax>35, high), CALM
  (wind_max<1, low), DRY_SPELL (rain 7 дней == 0; solar-условие ТЗ упрощено, гвард ≥5
  суток данных в окне).
- **B2 тренды:** 1h/3h/6h по **временным окнам** (avg 10-минутных концов, не LAG-строки),
  классификация ±0.5/±1.5 мм рт.ст. (ТЗ §4) — в API `/now`.
- **B3 прогнозы → `forecast`:** persistence (+1h/+3h/+6h); **zambretti** (Beteljuice,
  en+ru текст, letter) +6h/+12h; **sager_day** упрощённый (облачность = solar / SOLAR_POT
  ~61°N), только 10..15 MSK; TTL 30 дней. Миграция **v3**: `ALTER TABLE forecast ADD
  COLUMN text TEXT` (+ schema_migrations v3).
- **B4 REST API** `weather_api.py` (stdlib http.server): порт **:8090**, bind 0.0.0.0
  (NAT наружу закрыт; доступ LAN + ZeroTier), **basic auth** — `api_auth.conf`
  (user:pass, chmod 600; креды выданы владельцу в чате, в доках НЕ публикуются);
  `/health` без auth (живость, метеоданных не выдаёт); `/now` (последний замер + тренды
  + zambretti/sager сейчас), `/history`, `/hourly`, `/daily`, `/events`, `/forecast`,
  `/csv` (raw|hourly|daily); БД read-only (`mode=ro` + `PRAGMA query_only`), busy_timeout.
- **B5:** Kuma **HTTP-монитор id=75** «Метеостанция .101 — API (http)» → /health, 60 с;
  **`verify_stage_b.sh`** — 9 проверок (в т.ч. сверка агрегатов с raw за сутки).

## Артефакты на vitele
- `~/weather-dash/`: `weather_zam.py`, `weather_aggregator.py`, `weather_api.py`,
  `verify_stage_b.sh` (755), `api_auth.conf` (600).
- systemd: `weather-agg-hourly.service/.timer`, `weather-agg-daily.service/.timer`,
  `weather-api.service` — активны.
- Kuma: id=73 push «сбор», id=74 push «бэкап», id=75 http «API».
- БД: schema_migrations v3; `v_hourly` — вся история (12 окон на первый прогон),
  `v_daily` — сутки 15.09 (частичные, 100 замеров; события не флагуются — гвард),
  `forecast` — persistence/zambretti/sager_day.

## Приёмка
`verify_stage_b.sh` → **ALL CHECKS PASSED (9/9)**: коллектор жив (5 ok/5 мин);
materializer_log без ошибок; v_hourly/v_daily свежие; forecast пишется; API —
401 без auth / 200 с auth; **агрегаты == raw** (15.09 MSK: n 100==100, t_avg
2.586==2.586 ±0.05, rain 0.0==0.0 ±0.3); quick_check ok.

## Откат
`sudo systemctl disable --now weather-agg-hourly.timer weather-agg-daily.timer
weather-api` → удалить юниты из /etc/systemd/system → daemon-reload; скрипты
`~/weather-dash/` (zam/aggregator/api/verify_b) удалить. Таблицы v_hourly/v_daily/
forecast и колонка forecast.text коллектору не мешают (агрегаты — производные).
Точка восстановления: снепшот B0 + снепшот v1 (weather-6).
