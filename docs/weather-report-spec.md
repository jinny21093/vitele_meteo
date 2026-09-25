ТЗ: Юнит U8 «Суточный ИИ-отчёт» (weather-report)
Объект: ежедневный отчёт о погоде на даче: числа считает SQL, текст пишет LLM.Хост: Debian 12 VM (Hyper-V), x86_64, 1 vCPU, ~2.6 ГБ RAM — ресурсы дефицитны, все процессы U8 governed.Артефакт: docs/weather-report-spec.md, версия 1.1 (канон)Автор ТЗ: DeepSeekРевью: GLM (r1 — 6 MAJOR + 18 MINOR; r2 — верификация интеграции провайдера, A17 подтверждён владельцем по Z.ai Pricing + Models.dev)Акцепт: владелец vitele, 2026-09-25 (провайдер — Z.ai, только бесплатные модели)Связанные документы: weather-roadmap.md, weatherstation.md, weather-ui-spec.md, weatherboard_v2.1_analitic.mdУсловие внедрения: после успешного этапа B (живые v_daily, events, weather-api :8090) и после закрытия U7 (ребут-устойчивость приоритетнее).Нумерация: независима от weather-ui-spec.md. Версии 1.x.x этого документа — не версии UI-спеки.

Примечание по версии. Документ — интегрированная v1.1, прошедшая ревью r1 (все правки внесены) и верификацию r2. В него интегрировано решение владельца от 2026-09-25 о провайдере Z.ai и бесплатных моделях (A17–A20, тесты 21–25), включая уточнение r2: основная модель — glm-4.7-flash, резервная — glm-4.5-flash. Документ принят владельцем и является каноном; отдельного v1.2-документа не будет.

Changelog
v1.0
#	Источник	Что вошло
1	Задание на ТЗ U8	Базовая структура §1–§7, принцип «числа SQL / текст LLM», решения владельца, блок допущений, запреты
2	D-дополнение	Закрытый список полей, day_partial, NULL ≠ 0, OpenAI-совместимый интерфейс, дефолты LLM, enum fallback, 4 статуса доставки, DDL reports, whitelist промпта, тесты
3	D2-правки	fail-fast по PRAGMA, max_tokens=800 + finish_reason, wrong_language, раздельные переменные шаблона, pending_retry с TTL 3 прогонов, TimeoutStartSec + flock, миграция 00N_reports.sql, полный алгоритм нормализации, «N<3 — поля нет, не null»
v1.1
#	Источник	Что изменилось
1	REVIEW-U8-SPEC-r1 §2 MAJ-1	Убраны p_min_time/p_max_time из JSON-схемы; A5 удалено
2	§2 MAJ-2	Кап событий 15, поля events_total/events_truncated, норма из 3 полей prev-дней, деградация вместо ERROR при превышении 8 КБ
3	§2 MAJ-3	Двухуровневый тест чисел (строгий с единицами / мягкий без) + тест «5 м/с при факте 8 → invalid_numbers»
4	§2 MAJ-4	Markdown-политика: SQL-шапка с Markdown, LLM-текст плейном (без parse_mode)
5	§2 MAJ-5	Колонка llm_error TEXT в DDL + правило rerun-LLM при NULL/NULL
6	§2 MAJ-6	OnCalendar с зоной Europe/Moscow + формула вчерашнего дня MSK + инвариант day_epoch ≡ 75600 (mod 86400) + примеры 2026
7	§3 m-1…m-18	Правки по списку ревью (все приняты)
8	§1 M1, M2, M4	MISSING закрыты вербатим-данными из ревью (22 поля, 21 тип, N=3)
9	Owner decision 2026-09-25 + Z.ai docs	Провайдер — Z.ai; только бесплатные модели glm-4.7-flash, glm-4.5-flash; валидация whitelist на старте; M3 закрыт
10	REVIEW-U8-SPEC-r2 + проверка владельца (Models.dev)	A17 уточнён: glm-4.7-flash — основная, glm-4.5-flash — резервная (риск снятия после 2026-01-30 по данным BigModel.cn); A19 — правило сравнения model в ответе уточняется по факту P1; мелочи: wind_dir_mode — текстовое (§1.2), тест 15 различает «нет строки» vs «n_samples=0», precedence только в §6.2
§0. Принципы
Числа считает SQL. Текст пишет LLM. LLM запрещено вычислять, сравнивать, конвертировать единицы, «вспоминать» погоду. Всё, что попадает в промпт, — заранее подготовленные SQL-слоем факты. Нарушение = BLOCKER ревью.

Стандарты проекта. Python stdlib-only, creds только env/файл 600, никогда в git; имена таблиц/колонок по PRAGMA-whitelist; никаких долгоживущих коннектов к БД; логи без секретов; graceful degradation («нет данных» ≠ «0»); идемпотентность; идентификация версий.

Read-only по отношению к принятой схеме. U8 не пишет в weather, v_hourly, v_daily, events, forecast, wmeta. Единственная новая таблица — reports (см. §5). Append-only логи допустимы.

Один прогон в сутки. Никаких циклов, авто-retry-loop, фоновых демонов. Таймер — systemd, один запуск.

Graceful degradation. Падение LLM-провайдера или Telegram не отменяет доставку чисел: fallback-шаблон уходит всегда (§3). Превышение размера фактов — не ERROR, а деградация (MAJ-2).

Ресурсная дисциплина. Nice=19, TimeoutStartSec=300, flock с таймаутом, размер промпта и ответа — ограничены.

Финансовая дисциплина (owner decision). Только бесплатные LLM-модели. Платные запрещены — при нарушении ERROR + exit. Никакого «тихого» апгрейда.

§1. Сбор фактов (SQL-слой)
1.1. Окно «сутки дачи»
Канон дня — day_epoch из v_daily: локальная полночь MSK (tz_offset_seconds="10800", tz_policy=fixed_offset).

Отчёт строится только за закрытые сутки: D < сегодня_по_MSK. Запуск в 06:50 MSK 25 сентября формирует отчёт за day_epoch = 24 сентября 00:00 MSK.

Текущий незакрытый день в отчёт не попадает ни частично, ни справочно.

Формула D_epoch (канон, MAJ-6):

today_msk_idx     = (now + 10800) // 86400yesterday_msk_idx = today_msk_idx − 1D_epoch           = yesterday_msk_idx * 86400 − 10800
Одной строкой:

D_epoch = ((now + 10800) // 86400 − 1) * 86400 − 10800
Инвариант: D_epoch ≡ 75600 (mod 86400) — MSK-полночь. Проверка на живой БД: 1789506000 + 10800 = 20712 × 86400.

1.2. Читаемые поля
Из v_daily (за день D) — закрытый перечень 22 полей (M1, verbatim из server.py DAILY_FIELDS, сверено с PRAGMA на старте; деплой-лог dropped=0):

day_epoch, t_out_min, t_out_max, t_out_avg, t_out_min_time, t_out_max_time,p_min, p_max, wind_avg, wind_max, gust_max, wind_run_km, wind_dir_mode,rain_mm, rain_hours, solar_sum_wh_m2, uvi_max, gdd_day, frost_flag,hard_freeze_flag, fog_flag, n_samples
Валидация по PRAGMA table_info(v_daily) на старте; расхождение → ERROR + exit (D2-§1). Поля p_min_time/p_max_time не существуют (MAJ-1) — не читать, не использовать.

wind_dir_mode — текстовое поле (16-румбовый кардинал); читается и уходит в промпт (§6.1), но в счётчик числовых полей (§1.4, ≤40) не входит (r2).

Из v_daily (за D-1 и до 7 предыдущих дней) — только 3 поля: t_out_avg, rain_mm, n_samples.

Правила нормы (MAJ-2, m-5):

N = число доступных полных предыдущих дней (не более 7).
«Полный день»: n_samples ≥ 720. Дни с меньшим покрытием в норму не входят.
N < 3 → блок нормы в JSON отсутствует целиком (поля нет, не null). LLM не упоминает норму или «типичную погоду» вовсе.
3 ≤ N < 7 → блок нормы присутствует, поле norm_n = N, в промпт уходит пометка «норма по N дням»; LLM обязан упомянуть N в тексте.
N ≥ 7 → полная норма, norm_n = 7.
Из events — за день D (overlap-семантика):

События, чей интервал [ts_start, ts_end] пересекается с окном дня D (overlap-семантика §5.5 UI-спеки). Для каждого: event_type, severity, value, ts_start, ts_end, duration_s (для закрытых — ts_end − ts_start, для открытых — null).

Открытые события на начало дня D (FROST начался в 23:00 D-1, закрылся в 03:00 D) — включаются в отчёт за D (см. §7.2, тест 10).

Каталог типов событий — 21 тип (M2, verbatim из server.py EVENT_TYPES, решение A-9; он же в UI-спеке §5.5):

FROST, HARD_FREEZE, FOG, STORM_APPROACH, THUNDER_RISK, HEAVY_RAIN, DOWNPOUR,STRONG_WIND, HURRICANE_GUST, HEATWAVE, DRY_SPELL, CALM, RAPID_TEMP_DROP,RAPID_TEMP_RISE, PRESSURE_CRASH, RAIN_COUNTER_RESET, SENSOR_MISSING,SENSOR_STUCK, SENSOR_DRIFT, SENSOR_ANOMALY, BATTERY_LOW
Обработка неизвестного типа (m-13): новый тип в БД, которого нет в каталоге → WARN в лог + включить в факты как есть. Отчёт не падает.

События — правила (MAJ-2):

Сортировка: severity DESC, ts_start ASC (high → mid → low; внутри — по времени).
Кап 15 событий. При превышении — в факты включаются первые 15, выставляется events_truncated: true, events_total = полное число.
Контексты событий (context из events) не включаются ни в каком виде (m-16).
1.3. Производные факты (считает SQL, не LLM)
t_delta = t_out_max − t_out_min (суточная амплитуда).
p_delta = p_max − p_min (суточная дельта давления).
t_vs_prev = t_out_avg − prev_day.t_out_avg (сравнение с предыдущим днём).
rain_vs_prev = rain_mm − prev_day.rain_mm.
norm_t_avg = среднее t_out_avg по доступным полным дням (N из §1.2).
norm_rain_mm = среднее rain_mm по доступным полным дням.
D-1 отсутствует (m-6): блока comparison в JSON нет целиком (как при N<3 для norm).
1.4. Формат JSON-фактов
Один объект, ≤ 40 числовых полей (закрытый список из §1.2, §1.3; wind_dir_mode — текстовое, в счётчик не входит). Поля с отсутствующими данными — null (не 0). Блок нормы при N<3 — отсутствует. Блок сравнения при отсутствии D-1 — отсутствует.

Схема (verbatim, v1.1):

{  "day_epoch": 1758662400,  "day_label": "24.09.2026",  "day_partial": false,  "n_samples": 1440,  "temperature": {    "t_out_min": 3.8, "t_out_min_time": 1758690720,    "t_out_max": 12.1, "t_out_max_time": 1758718800,    "t_out_avg": 7.9, "t_delta": 8.3  },  "pressure": {    "p_min": 758.1, "p_max": 762.4, "p_delta": 4.3  },  "wind": {    "wind_avg": 1.4, "wind_max": 3.6, "gust_max": 5.8,    "wind_dir_mode": "WSW", "wind_run_km": 121.0  },  "rain": {    "rain_mm": 0.0, "rain_hours": 0  },  "solar": {    "solar_sum_wh_m2": 1240.0, "uvi_max": 2.1  },  "flags": {    "frost_flag": 0, "hard_freeze_flag": 0, "fog_flag": 0,    "gdd_day": 2.4  },  "events": [    {"event_type": "FROST", "severity": "high",     "ts_start": 1758672000, "ts_end": 1758675600,     "duration_s": 3600, "value": -0.3}  ],  "events_total": 1,  "events_truncated": false,  "comparison": {    "prev_day_epoch": 1758576000,    "t_vs_prev": 1.2,    "rain_vs_prev": -3.4  },  "norm": {    "norm_n": 7,    "norm_t_avg": 6.9,    "norm_rain_mm": 1.8  }}
Лимит размера JSON-фактов: ≤ 8 КБ.

Правило деградации при превышении (MAJ-2): если факты всё же > 8 КБ → обрезать events до 0, установить events_truncated: true, events_total сохранить. Никогда не ERROR. Отчёт отправляется с числами и пометкой «события не включены (превышен лимит)».

Правило NULL ≠ 0:

NULL в факте → в JSON null, не 0.
LLM не должен утверждать про NULL-поле ничего: «дождя не было» при rain_mm = null — ошибка; допустимо «данные о дожде недоступны».
Правило прописывается в системном промпте (см. §2.4).
Правило частичного дня:

Если n_samples < 720 (половина от 1440 при опросе 60 с) → day_partial = true в JSON.
day_partial = true обязывает LLM упомянуть в тексте, что данные неполные, с указанием покрытия {n_samples}/1440.
Флаг day_partial не отменяет отчёт: числа, которые есть, отдаются, с пометкой.
§2. LLM-слой
2.1. Провайдер (v1.1, r2 — канон)
Провайдер: Z.ai, OpenAI-совместимый endpoint.Базовый URL: https://api.z.ai/api/paas/v4Аутентификация: Authorization: Bearer <LLM_API_KEY>
Жёсткое ограничение (owner decision): ТОЛЬКО бесплатные модели. Платные модели запрещены — финансовый BLOCKER.

Whitelist разрешённых моделей (verbatim из Z.ai Pricing + Models.dev, проверено владельцем 2026-09-25):

  - glm-4.7-flash    (ОСНОВНАЯ;  Input: Free | Cached: Free | Output: Free)  - glm-4.5-flash    (РЕЗЕРВНАЯ; Input: Free | Cached: Free | Output: Free)
Не входят в whitelist (бесплатные, но не для U8): glm-4.6v-flash (vision-модель; текстовый нарратив — не её задача; возможное будущее применение — анализ снимков, отдельным решением).

Запрещены любые другие, включая (но не ограничиваясь): glm-4.7, glm-4.5, glm-4.5-air, glm-5.x — любые, не входящие в whitelist.

LLM_MODEL по умолчанию — glm-4.7-flash (r2): она новее и дольше проживёт. glm-4.5-flash — резервная на случай недоступности основной; по данным BigModel.cn, может быть снята с поддержки после 2026-01-30 — при снятии whitelist уменьшается до одной модели без правок кода, обновляется только env и ТЗ.

Валидация на старте процесса (до первого API-запроса):

ALLOWED_FREE_MODELS = {"glm-4.7-flash", "glm-4.5-flash"}if LLM_MODEL not in ALLOWED_FREE_MODELS:    ERROR "model not in free whitelist" + exit 1
Никакого «тихого» переключения на платную модель. При недоступности бесплатной модели → fallback без нарратива (§3.4), а не апгрейд до платной.

Ollama — резервный вариант этапа 2, вне U8. Смена провайдера возможна через env, но только при условии сохранения whitelist-валидации. При появлении нового провайдера — обновление ТЗ и whitelist.

Базовый URL и API-ключ — из env (LLM_BASE_URL, LLM_API_KEY) или файла 600 по пути из env (LLM_SECRETS_FILE). Precedence — см. §6.2 (m-10). Модель — из env (LLM_MODEL). Обязательна ∈ whitelist.

2.2. Параметры генерации
Параметр	Значение	Обоснование
temperature	0.3	нарратив, но не креатив
max_tokens	800	обрезка хуже отсутствия; 500 было тесно на 150–200 слов
timeout	60 с	на HTTP-запрос к провайдеру
retries	2	backoff 2 с, 8 с
finish_reason	проверяется	"length" → текст принимается + WARN в лог, fallback не триггерится. finish_reason ∉ {stop, length} (например, content_filter) → отказ (m-12)
Формат ответа	только текст	без JSON-обёртки, без структур
2.3. Язык
Ответ — только на русском.

Детект: доля кириллицы в ответе < 70% → wrong_language → fallback без нарратива.

Правило прописывается в системном промпте.

2.4. Промпт-дисциплина
Системный промпт (смысл, дословная формулировка — на этапе реализации):

«Используй только числа из блока фактов. Не выдумывай, не вычисляй, не округляй, не конвертируй единицы.»
«Числа в тексте — копия из фактов, в тех же единицах, что в БД (°C, мм, м/с, мм рт. ст.). Конвертация единиц запрещена.»
«Никаких "половина градуса", "около", "примерно", пересчётов.»
«Если поле факта null — не утверждай про него ничего. "Дождя не было" при rain_mm = null — ошибка. Пиши "данные недоступны".»
«Если day_partial = true — упомяни, что данные неполные, с покрытием {n_samples}/1440.»
«Если norm_n присутствует и norm_n < 7 — упомяни "норма по N дням". Если блока norm нет — не упоминай норму и "типичную погоду" вообще.»
«Если events_truncated = true — упомяни, что события показаны не все.»
«Ответ — только на русском. Только текст, без markdown-обёрток, без JSON.»
Стиль: спокойный, повествовательный, 2–3 абзаца, для человека.

2.5. Отказ-поведение LLM
Считается отказом (→ fallback без нарратива):

HTTP timeout.
HTTP 5xx от провайдера.
HTTP 4xx от провайдера (401, 403 и т.п.) — без retry, сразу отказ (m-9).
HTTP 402 Payment Required — прямое нарушение финансового ограничения, без retry, provider_unreachable.
Ответ с model ≠ запрошенной LLM_MODEL (провайдер подменил модель) — отказ, provider_unreachable. Ни при каких обстоятельствах не принимать ответ от другой модели. Правило сравнения — не побуквенное (r2, A19): провайдер может вернуть каноническое имя/алиас; точная форма сравнения фиксируется по факту pre-flight P1 (живое значение поля model от Z.ai, verbatim в отчёте P1) и документируется в коде.
Ответ с признаком платного тарифа (если API возвращает поле tier/plan ≠ free) — отказ, provider_unreachable.
Пустой ответ.
Ответ без чисел.
Ответ с числами, не совпадающими с фактами (см. §7.1).
Ответ не на русском (кириллица < 70%).
Ответ — не текст (JSON, markdown-код, объект).
finish_reason ∉ {stop, length} (m-12).
При отказе:

llm_text = NULL в reports.
llm_error = строка с причиной из enum (§3.5).
delivery_status идёт штатным путём (fallback-шаблон, см. §3).
§3. Доставка
3.1. Целевой канал
Telegram-бот. TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — из env, не в git.

Markdown-политика (MAJ-4):

Шапка сообщения — SQL-сформированная, с Telegram Markdown (жирным — ключевые числа шаблона).
LLM-текст — плейном, без parse_mode. Отправляется вторым сообщением или после \n\n в шапке.
Markdown-спецсимволы в LLM-тексте не экранируются (мы их не парсим — plain).
Время отправки — см. §4.

3.2. Статусы доставки
delivery_status ∈ ('pending', 'pending_retry', 'sent', 'failed'):

pending — отчёт сформирован, доставка не начата.
pending_retry — все in-run попытки исчерпаны, отложено на следующий прогон.
sent — успешно доставлено.
failed — 3 следующих прогона не смогли доставить, отчёт снят с очереди, Kuma push один раз на переход.
3.3. Retry-политика
In-run (в рамках одного прогона):

3 попытки отправки в Telegram: backoff 2 с, 8 с, 30 с.
Таймаут на попытку — 15 с (m-7).
Все 3 провалились → delivery_status = 'pending_retry'.
Next-run (следующий прогон, 06:50 следующего дня):

СНАЧАЛА — досылка всех pending_retry (по одному кругу in-run retry на каждый).
ПОТОМ — генерация нового отчёта.
pending_retry живёт ≤ 3 следующих прогонов. На 4-й неудаче → failed + один Kuma push на переход (weather_backup.sh уже умеет).
Запрет авто-retry-loop. Никаких бесконечных попыток в фоне, никаких демонов.

3.4. Fallback-шаблон (v1.1, канон)
Если LLM отказал (§2.5), отправляется шаблонный текст. Числа подставляет SQL-слой, не LLM. {X} = gust_max (m-1). Условный блок {если fallback} убран — шаблон и есть «сообщение без нарратива» (m-2).

Погода за {DD.MM}: Tmin {t} °C ({HH:MM}), Tmax {t} °C ({HH:MM}), осадки {mm} мм, ветер до {gust_max} м/с.{если day_partial} Данные неполные: покрытие {n}/1440. Нарратив недоступен: {reason}
{t}, {HH:MM}, {mm}, {gust_max}, {n} — из фактов, форматирование SQL-слоя. {reason} — из enum §3.5. Условный блок {если day_partial} вставляется, только если флаг истинен.

3.5. Enum причин fallback
{reason} ∈ фиксированный список:

timeoutempty_responseinvalid_numberswrong_languageprovider_unreachableprovider_5xx
HTTP 4xx от провайдера (401, 403 и т.п.) → provider_unreachable (m-9).
HTTP 402 → provider_unreachable.
Подмена модели (model != LLM_MODEL по правилу §2.5) → provider_unreachable.
finish_reason ∉ {stop, length} (например, content_filter) → отказ (m-12).
Расширение enum — через правку ТЗ и миграцию, не «на лету».

§4. Расписание и надёжность
4.1. systemd timer (v1.1)
OnCalendar=*-*-* 06:50:00 Europe/MoscowNice=19TimeoutStartSec=300OnFailure=weather-report-alert.service
OnCalendar с явной зоной Europe/Moscow (MAJ-6). VM TZ может быть UTC (Hyper-V Timesync off) — на внедрении проверить timedatectl.
Nice=19.
TimeoutStartSec=300 (с досылкой pending_retry + генерацией 180 было тесно, m-8).
OnFailure= + существующий Kuma-push через weather_backup.sh (m-15).
При таймауте — сразу fallback без LLM (числа+шаблон отправляются, если успели собраться; иначе отчёт за день помечается failed с причиной timeout).
Имя юнита: weather-report.service / weather-report.timer.
4.2. Lock
flock /var/lock/weather-report.lock (снятие в finally).
Блокирующий flock с таймаутом 150 с. Истёк → WARN «previous run still active» + выход 0 (не ошибка, не дубль).
Если процесс упал по SIGKILL — ядро отпускает lock автоматически.
4.3. Порядок действий в одном прогоне
Старт, flock, nice.
Валидация LLM_MODEL ∈ whitelist (до первого API-запроса).
Валидация схемы v_daily по PRAGMA table_info (fail-fast при расхождении, D2-§1).
Досылка pending_retry (если есть).
Сбор фактов за D (§1).
Проверка размера фактов; при > 8 КБ — деградация (обрезка events, §1.4).
Запись в reports со status = 'pending', facts_json.
Запрос к LLM (§2).
Обновление reports.llm_text, llm_model, llm_tokens_*, llm_error (при отказе).
Отправка в Telegram (§3).
Обновление delivery_status (sent / pending_retry / failed).
Снятие flock.
4.4. Идемпотентность и rerun
Повторный запуск за тот же день (ручной или после падения таймера) не создаёт новую строку в reports — idempotency_key (§5.1) обеспечивает UNIQUE.

Если строка есть и delivery_status = 'sent' — прогон завершается без действий (выход 0).
Если строка есть и delivery_status ∈ ('pending', 'pending_retry') — переиспользуется, повтор доставки.
Если delivery_status = 'failed' (m-11): WARN + exit 0. Восстановление — только через --resend.
Правило rerun LLM (MAJ-5):

llm_text IS NULL AND llm_error IS NULL → повторить LLM (крах между INSERT и LLM-запросом; нарратив не потерян).
llm_error IS NOT NULL → сразу fallback-шаблон, LLM не пробуется (известно, что провайдер/модель отказывает).
llm_text IS NOT NULL → использовать сохранённый текст.
4.5. Таймзоны
Все времена в фактах и тексте — MSK (tz_offset_seconds="10800", tz_policy=fixed_offset).
Формула D_epoch — см. §1.1.
Инвариант day_epoch ≡ 75600 (mod 86400).
OnCalendar — с явной зоной Europe/Moscow.
§5. Хранение и аудит
5.1. DDL таблицы reports (v1.1)
Вносится миграцией docs/migrations/00N_reports.sql, N = MAX(version)+1 на момент внедрения. В живой БД schema_migrations.version = 2 (M4) → ожидаемо N = 3, сверить на месте.

CREATE TABLE reports (  id INTEGER PRIMARY KEY AUTOINCREMENT,  day_epoch INTEGER NOT NULL,  generated_at INTEGER NOT NULL,  facts_json TEXT NOT NULL,  llm_text TEXT,  llm_model TEXT,  llm_tokens_in INTEGER,  llm_tokens_out INTEGER,  llm_error TEXT,  delivery_status TEXT NOT NULL    CHECK (delivery_status IN ('pending','pending_retry','sent','failed')),  delivery_attempts INTEGER DEFAULT 0,  delivery_error TEXT,  idempotency_key TEXT UNIQUE NOT NULL,  schema_version INTEGER DEFAULT 1);CREATE INDEX idx_reports_day ON reports(day_epoch);CREATE INDEX idx_reports_status ON reports(delivery_status, generated_at);
idempotency_key = f"{day_epoch}:{chat_id}". Формируется SQL-слоем до INSERT. UNIQUE гарантирует отсутствие дублей при retry/рестарте.
llm_error — строка с причиной из enum §3.5 при отказе LLM; NULL при успехе (MAJ-5).
llm_model — фиксировать фактическую модель (должна совпадать с LLM_MODEL из whitelist, по правилу §2.5).
schema_version = 1 — версия схемы reports (не путать с schema_migrations БД).
Запись в schema_migrations — (version=N, description='U8 reports').
5.2. Retention
reports — вечно, с пометкой «пересмотр через 1 год».
facts_json (≤ 8 КБ) + llm_text (≤ 5 КБ) → ~5 МБ/год. Незначительно.
5.3. CLI
weather-report --resend-last — переотправить последнюю строку по day_epoch (любой статус). Нет строк → exit 1 (m-3).
weather-report --resend — переотправить конкретный день.
Оба флага — через argparse, без секретов в аргументах.
Повторная отправка не создаёт новую строку в reports, обновляет существующую (delivery_status, delivery_attempts).
Идемпотентность сохраняется: если Telegram уже принял — повторная отправка может создать дубль в чате (осознанное поведение --resend).
--dry-run (m-4) — без LLM и Telegram; печатает facts JSON + заполненный шаблон; exit 0.
§6. Безопасность и лимиты
6.1. Whitelist промпта (v1.1)
Уходят в промпт:

числа v_daily за D (T, осадки, ветер, давление);
времена событий в HH:MM MSK;
типы событий из каталога 21;
v_daily за D-1 и до 7 дней назад (норма) с пометкой фактического N;
метка дня DD.MM.YYYY;
флаги day_partial / events_truncated / NULL-поля.
НЕ уходят в промпт:

пути к БД, IP-адреса, MAC;
имена хостов (vitele, outpost);
имена файлов;
bot_token, chat_id;
API-ключи;
содержимое wmeta (кроме tz_offset_seconds, если нужно);
версии софта;
event_id, внутренние id;
контексты событий (context из events) — не включаются ни в каком виде (m-16).
6.2. Секреты
LLM_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — только env или файл 600. Никогда в git, никогда в логи.
Precedence (m-10): env приоритетен; файл LLM_SECRETS_FILE — только если env пуст.
Тест на приёмке: git log -p | grep -iE 'api[-]?key|bot[-]?token' → пусто (§7.2).
Тест на приёмке: journalctl -u weather-report | grep -iE 'api[-]?key|bot[-]?token' → пусто (§7.2).
6.3. Лимиты
Лимит	Значение
Разрешённые модели	только glm-4.7-flash (основная), glm-4.5-flash (резервная)
Запрет платных моделей	hard, при нарушении — ERROR + exit 1
Подмена модели провайдером	hard, при model != LLM_MODEL (по правилу §2.5) — отказ
Месячный бюджет	0 (ноль)
Промпт (факты + системный + user)	≤ 16 КБ
Ответ LLM	≤ 8 КБ
Запросов к LLM в день	1–2 (штатно 1)
Запросов к Telegram в день	≤ 4 (штатно 1)
Авто-retry-loop	запрещён
Размер facts_json	≤ 8 КБ (при превышении — деградация, не ERROR)
Кап событий в фактах	15 (events_truncated: true при превышении)
Нарушение лимита (кроме размера фактов — там деградация) → ERROR + fallback.

§7. Приёмка
7.1. Тест «числа в тексте == числа в фактах» (v1.1)
Двухуровневый алгоритм (MAJ-3):

Уровень A (строгий). Числа, сопровождаемые единицей в том же токене или рядом: °C, °, мм, м/с, %, ч, мм рт. ст., Вт/м².→ Каждое обязано ∈ множества чисел из facts_json. Без исключений.

Уровень B (мягкий). Голые числа без единиц.→ Исключения: годы 1900–2100, HH:MM, DD.MM, счётчики со словами «раз / событий / дней / часов».→ Остальные — ∈ множества чисел из facts_json.

Нормализация (до сравнения):

Минусы − (U+2212), – (en-dash), - → -.
Запятая → точка: 18,2 → 18.2.
Trailing zeros: 18.20 → 18.2.
«Около N» — N считается совпавшим + WARN (нарушение «копия из фактов», не отказ).
Провал любого уровня → invalid_numbers → fallback.

7.2. Обязательные тесты (v1.1)
#	Сценарий	Ожидание
1	Нормальный день	llm_text != NULL, delivery_status = 'sent'
2	Частичный день (n_samples = 400)	day_partial = true, в тексте «данные неполные»
3	rain_mm = NULL	в тексте нет «дождя не было»
4	LLM timeout	llm_text = NULL, llm_error = 'timeout', delivery sent, шаблон доставлен
5	LLM вернул не на русском	llm_error = 'wrong_language', fallback
6	LLM вернул числа не из фактов	llm_error = 'invalid_numbers', fallback
7	Telegram недоступен	3 in-run попытки → pending_retry
8	Telegram недоступен 4 прогона	failed, один Kuma push на переход
9	Дубль-рестарт за один день	одна строка reports, одно сообщение в Telegram
10	FROST открыт 23:00 (D-1), закрыт 03:00 D	есть в отчёте за D
11	norm_n < 3	блока norm в JSON нет, LLM не упоминает норму
12	norm_n = 5	в тексте «норма по 5 дням»
13	Секреты не в git	git log -p | grep -iE 'api[-]?|key|bot[-]?|token' → пусто
14	Секреты не в логах	journalctl -u weather-report | grep -iE 'api[-]?|key|bot[-]?|token' → пусто
15	Пустой день	строка v_daily отсутствует → «Отчёт за DD.MM: данных нет»; строка есть, n_samples = 0 → «данных нет (0 замеров)» — случаи различаются формулировкой причины (r2, m-14)
16	Схема v_daily не совпала с PRAGMA	ERROR + exit 1
17	finish_reason = "length"	текст принимается, WARN в лог, fallback не триггерится
18	--resend без новой строки	количество строк в reports не растёт, обновляется существующая
19	events_truncated = true	events_total > 15, массив events содержит 15
20	LLM написал «до 5 м/с», факт gust_max = 8	invalid_numbers, fallback
21	LLM_MODEL=glm-4.5 (платная)	ERROR + exit 1 до API-запроса
22	LLM_MODEL=glm-4.7-flash	штатный прогон
23	API вернул model != LLM_MODEL (по правилу §2.5)	llm_error='provider_unreachable', fallback, без retry
24	API вернул HTTP 402 Payment Required	llm_error='provider_unreachable', fallback, без retry
25	Бесплатная модель вернула 5xx, потом восстановилась	in-run retry (2 попытки), штатный прогон
7.3. Smoke-test
verify_stage_u8.sh (по аналогии с verify_stage_ui.sh):

weather-report --dry-run собирает факты без LLM и Telegram.
sqlite3 weather.db "SELECT COUNT(*) FROM reports WHERE day_epoch = <вчерашний>" = 1.
weather-report --resend-last → exit 0 при успехе, exit 1 при отсутствии строк (m-18).
journalctl -u weather-report --since "10 min ago" | grep -c ERROR = 0.
Проверка бюджета Z.ai (вручную, после первого прогона): usage за месяц = $0.00.
§8. Допущения
#	Допущение	Обоснование	Статус
A1	Имена env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_SECRETS_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID	стандарт проекта — env	✅
A2	Имя таблицы reports (единственная новая)	D-§5/D2-§5	✅
A3	Имя systemd-юнита weather-report.service / .timer	совпадает с CLI; стандарт проекта	✅
A4	Путь lock /var/lock/weather-report.lock	D-§4; стандарт Debian	✅
A5	p_min_time / p_max_time	полей нет в v_daily	❌ удалено (MAJ-1)
A6	wind_dir_mode — 16-румбовый кардинал	стандарт проекта (UI-спека)	✅
A7	day_label — DD.MM.YYYY в TZ дачи	не число, не участвует в тесте чисел	✅
A8	--dry-run в CLI	для приёмки §7.3	✅
A9	Retention reports — вечно	~5 МБ/год	✅
A10	JSON-факты — UTF-8 без BOM	JSON-стандарт	✅
A11	Каталог 21 типа — UI-спека §5.5 / server.py EVENT_TYPES, не roadmap	источник правды — код	✅
A12	Порог кириллицы 70% — эвристика	простая, документируемая	✅
A13	finish_reason="length" → WARN, текст принимается; ∉ {stop,length} → отказ	m-12	✅
A14	SQL-слой форматирует все переменные шаблона	принцип §0	✅
A15	LLM-текст — плейн; Markdown только в SQL-шапке	иначе 400-фейл-луп Telegram	✅
A16	Провайдер — облачный freetier	изменено решением владельца → Z.ai (A17)	✅ изменено
A17	Whitelist: glm-4.7-flash (основная), glm-4.5-flash (резервная; риск снятия после 2026-01-30 по данным BigModel.cn — при снятии whitelist уменьшается без правок кода). glm-4.6v-flash (vision) — бесплатна, но НЕ в whitelist	verbatim из Z.ai Pricing + Models.dev, проверено владельцем 2026-09-25	✅ (r2)
A18	Валидация LLM_MODEL на старте, fail-fast	финансовый BLOCKER	✅
A19	Подмена модели → отказ; правило сравнения — не побуквенное, уточняется по факту P1	защита от платного запроса + реалии API-алиасов	✅ (r2 уточнено)
A20	Ollama — вне U8; при недоступности Z.ai → fallback-шаблон	этап 2	✅
§9. MISSING
#	MISSING	Статус
M1	Точный список колонок v_daily	✅ закрыт (22 поля, §1.2)
M2	Точный список 21 типа event_type	✅ закрыт (§1.2)
M3	Провайдер, endpoint, ID моделей	✅ закрыт (Z.ai, https://api.z.ai/api/paas/v4, glm-4.7-flash основная / glm-4.5-flash резервная)
M4	Номер миграции 00N_reports.sql	✅ подтверждён: version=2 → N=3 (сверить на месте)
M5	Формат ответа при HTTP 4xx	✅ закрыт (без retry, provider_unreachable; уточнение verbatim — в P1)
M6	Kuma-push endpoint	⚠️ владелец; существующий механизм weather_backup.sh (kuma_push.conf, backup_url)
§10. Не входит в U8
Прогнозная часть (только факты прошедших суток).
Управление станцией (калибровка, register).
Модификация weather, v_*, events, forecast, wmeta.
/ask (этап C).
NLP-сводки по неделям/месяцам (возможно — отдельный юнит).
Мобильное приложение.
HTTPS, reverse proxy.
Изменение принятых механик U0–U5, UI-спеки v1.2.x.
Ollama и любые локальные LLM (этап 2, вне U8).
Vision-модели (glm-4.6v-flash) — вне U8; возможное будущее применение (анализ снимков) — отдельным решением.
§11. Резюме
Ежедневный отчёт о погоде: SQL собирает факты за закрытые сутки дачи в JSON ≤ 8 КБ, LLM (Z.ai, бесплатные модели: основная glm-4.7-flash / резервная glm-4.5-flash) наррирует по строгому промпту (только русский, только числа из фактов), Telegram доставляет. Все сбои (LLM, Telegram, таймаут, частичный день, переполнение событий, HTTP 402, подмена модели) покрыты graceful degradation: числа уходят всегда, нарратив — по возможности. Идемпотентность — через idempotency_key. Retry — гибрид in-run + next-run, TTL 3 прогона. Секреты — только env, никогда в git/логи. Ресурсы — nice 19, timeout 300, flock.

Ключевые механики:

reports — единственная новая таблица, миграция 003_reports.sql (N=3 ожидаемо).
CLI weather-report --resend-last / --resend / --dry-run.
Timer 06:50 MSK (OnCalendar=--* 06:50:00 Europe/Moscow), после снапшота 06:40.
Fallback-шаблон с раздельным day_partial-блоком и reason из enum (6 значений).
Двухуровневый тест чисел (строгий с единицами / мягкий без).
Markdown-политика: SQL-шапка Markdown + LLM-текст плейном.
Формула вчерашнего дня MSK + инвариант day_epoch ≡ 75600 (mod 86400).
Провайдер Z.ai, только бесплатные модели; whitelist валидируется на старте; защита от платного запроса (402 / подмена модели / fail-fast по LLM_MODEL).
Тесты §7.2 — 25 сценариев.