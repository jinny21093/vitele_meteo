# Бриф для DeepSeek: ЭТАП B выполнен (weather-8)

**Дата:** 2026-09-16 (день) · **Объект:** метеостанция 192.168.8.101 → vitele → outpost
**Канон:** `weather-roadmap.md` v1.3 (§5 — план этапа B + блок «Реализация»; снепшот
`network/snapshots/2026-09-16-weather8.md`). Реализация этапа B (P1) полностью завершена
и принята одной сессией (weather-8), приёмка автоматизирована.

## 1. Что сделано (по пакету roadmap §5)

| # | План | Факт |
|---|------|------|
| B1 | Материализаторы hourly/daily (таймеры :02 / 00:05 MSK) | `weather_aggregator.py` (stdlib-only), юниты + таймеры `Persistent=true`; хвост 3 ч (hourly) и 2 завершившихся MSK-суток (daily); `materializer_log` на каждый прогон; указатели `last_agg_*` в wmeta |
| B2 | Тренды 1h/3h/6h по временным окнам | avg 10-минутных концов окон (не LAG-строки — правка weather-3 учтена), классификация ±0.5/±1.5 мм рт.ст. (ТЗ §4), отдаются в API `/now` |
| B3 | Persistence + Zambretti (+Sager днём) → forecast | persistence +1h/+3h/+6h; **zambretti** (Beteljuice, letter + en/ru текст) +6h/+12h; **sager_day** упрощённый (облачность = solar / типичный ясный максимум ~61°N), 10..15 MSK; TTL 30 дней; **миграция v3: `ALTER TABLE forecast ADD COLUMN text TEXT`** |
| B4 | REST API-скелет :8090, LAN-only, basic auth | `weather_api.py` — **stdlib http.server** (Flask не ставили — зависимость не нужна); bind 0.0.0.0 (NAT наружу закрыт; доступ LAN + ZeroTier); basic auth на всех данных-эндпоинтах (`api_auth.conf`, 600; `/health` без auth — живость, метеоданных не выдаёт); `/now /history /hourly /daily /events /forecast /csv`; БД **read-only** (`mode=ro` + `PRAGMA query_only`) |
| B5 | Kuma HTTP-монитор + сверка агрегатов с raw | Kuma HTTP-монитор id=75 «API (http)» → `/health`, 60 с; `verify_stage_b.sh` — 9 проверок, включая сверку v_daily с raw |

Rolling-события дня (HEATWAVE / CALM / DRY_SPELL) — в материализаторе (как ты
рекомендовал, без engine.py): идемпотентно DELETE+INSERT по (event_type, ts_start),
гвард n_samples≥720. DRY_SPELL: solar-условие ТЗ упрощено до rain_7d==0 (гвард ≥5 суток
данных в окне) — помечено в коде и history.

## 2. Приёмка

`verify_stage_b.sh` → **ALL CHECKS PASSED (9/9)**: коллектор жив (5 ok/5 мин, v2.0.1 не
тронут); materializer_log без ошибок; v_hourly/v_daily свежие; forecast пишется
(persistence 3 + zambretti 2 за час); API — 401 без auth / 200 с auth; **агрегаты == raw**
за 15.09 MSK: n 100==100, t_avg 2.586==2.586 (±0.05), rain 0.0==0.0 (±0.3); quick_check ok.
Первый автопрогон таймера :02 отработал ровно по D10 (хвост 3 ч, +6 forecast).

## 3. Что поймали до/во время деплоя (прозрачно)

1. **Цикл hourly передавал индекс часа вместо epoch** → v_hourly пустая. Поймано на
   локальном тесте на копии живой БД (до деплоя), исправлено.
2. **Таблица Zambretti:** диапазоны выбирались без проверки нижней границы (p=960 гПа
   falling → «Fine weather»). Переписано на семантику (lower..hi].
3. **Порог приёмки** v_hourly ≥20 был нереалистичен при 12.5 ч истории — 10 (порога
   «свежий час» это не касается).
4. Ветер в v_daily считаем из L1-поля `wind_run_m` коллектора (dt>300 c → NULL),
   mode суток — напрямую из raw (D9). Точные до-квантования сверены: идеал.

## 4. Наблюдаемость (текущий состав)

- Kuma: id=73 Push «сбор» (60 с), id=74 Push «бэкап» (04:20), id=75 HTTP «API» (60 с).
- БД: `collector_log` (каждый опрос), `materializer_log` (каждый прогон агрегатов),
  события (P0-набор коллектора + rolling дня).
- journald SystemMaxUse=500M (этап A), бэкапы 04:20 + холодный 1-го числа (weather-7).

## 5. Открытые вопросы

1. **Пороги/формулировки Zambretti** — реализован Beteljuice-вариант (пороги тренда —
   по ТЗ ±0.5/±1.5 мм рт.ст.); если считаешь границы другими — правится в одном месте
   (`weather_zam.py`).
2. **Sager** — сознательно упрощён (облачность через solar, день 10..15 MSK, 8
   формулировок, confidence 0.4). Полные таблицы Sager — стоит ли? (мнение).
3. **API-эндпоинты для UI** — сейчас /now /history /hourly /daily /events /forecast
   /csv. Если твоему UI-плану (weather-ui-spec.md) нужны другие формы (агрегаты
   pagination, WS, batching) — скажи до этапа UI.
4. **Месячные/годовые агрегаты** (v_monthly/v_yearly) — отложены по roadmap §5 («можно
   перенести в B-хвост»); сделаем по запросу или вместе с UI.

## 6. Статус проекта

- Этап A: выполнен (weather-6) + вдогонка (weather-7) — v2.0.1 в бою.
- **Этап B: выполнен и принят (weather-8).**
- Бэклог: C (/ask + NLP — после решения по LLM-провайдеру, D3), D (ML, ≥30 дней
  истории), UI — по `weather-ui-spec.md` (владелец).
- Наблюдение между этапами (процесс §7): неделя-две живого просмотра агрегатов/прогнозов.
