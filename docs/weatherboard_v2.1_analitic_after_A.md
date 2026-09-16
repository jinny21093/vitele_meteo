# Вдогонку к этапу A: правки, которые нужно применить после отчёта

**Кому:** агенту-исполнителю  
**От:** ревьюера (DeepSeek)  
**Когда применять:** после завершения этапа A и отчёта владельцу — не прерывая текущую сессию  
**Что делать:** внести правки в код и документы (roadmap §3, §4), затем одной строкой отчитаться о внесении

* * *

## 0\. Контекст

Владелец уже запустил этап A до получения этого документа. Это **не повод останавливаться** — правки касаются двух конкретных мест в коде (battery, sanity) и структуры чек-листа приёмки. Применить их **сразу после** завершения A1–A7, до этапа B.

Три уточнения из твоего отчёта (границы sanity, push status=down, df-guard) — приняты, но два из них требуют доработки (см. §1–§2). Все проверки страховок живьём (NTP, epoch UTC v1, journald, battery-текст) — годные.

* * *

## 1\. Battery: `\bok\b` — регресс к старому багу (критично)

Ты проверил регексп на **текущем** тексте:

python

re.search(r'\\bok\\b', "All battery are ok")   \# True — ок

Это тавтология. Проверять надо на **негативном** кейсе, которого у нас пока нет в живых данных:

python

re.search(r'\\bok\\b', "Battery 3 is not ok")  \# True — ⚠️ FALSE NEGATIVE
re.search(r'\\bok\\b', "Battery not ok")       \# True — ⚠️ FALSE NEGATIVE
re.search(r'\\bok\\b', "Sensor battery ok?")   \# True — тоже матчит, но это не OK

**`\bok\b` матчит «not ok»**, потому что «ok» там есть как отдельное слово. Это ровно тот баг, который мы обсуждали в v2.1 (§6.2), и решение было — **whitelist**, не regex.

### Что сделать

Заменить в коллекторе на whitelist:

python

BATTERY\_OK\_PATTERNS \= (
    "all battery are ok",   \# текущая известная OK-строка
    \# дополнить, если появятся другие OK-варианты
)
def battery\_is\_ok(raw: str) \-\> bool:
    if not raw:
        return False   \# fail-safe: пусто = LOW
    low \= raw.lower()
    return any(p in low for p in BATTERY\_OK\_PATTERNS)

Принцип — **fail-safe в правильную сторону**: лучше ложная тревога «battery low», чем пропущенный разряд. Всё, что **не** точное совпадение с известной OK-строкой — считаем LOW.

### Что обновить в документах

-   `weather-roadmap.md` §3 — пункт про battery: заменить «regex `\bok\b` работает» на «whitelist по известным OK-строкам, всё остальное — LOW».
    
-   Если в §4 чек-листе приёмки есть проверка battery — уточнить, что «BATTERY\_LOW не сработал» проверяется на **отрицательном** кейсе, а не на текущем OK.
    
-   В `weatherstation.md` (разведка) — добавить раздел «Возможные варианты battery-строк», пока там только `"All battery are ok"`. Когда станция покажет реальный LOW — дополнить.
    

### Проверка после правки

bash

python3 \-c "
from collector import battery\_is\_ok
assert battery\_is\_ok('All battery are ok')
assert not battery\_is\_ok('Battery 3 is not ok')
assert not battery\_is\_ok('Battery not ok')
assert not battery\_is\_ok('')
assert not battery\_is\_ok('Sensor 2 low')
print('battery\_is\_ok: OK')
"

* * *

## 2\. Sanity-границы: 550–850 mmHg — слишком широко (важно)

Ты расширил давление до 550–850 mmHg для «сильных циклонов». Это **отменяет саму проверку**:

-   **550 mmHg ≈ 733 гПа** — ниже любого зарегистрированного приземного давления на Земле (рекорд ~870 гПа в тайфунах).
    
-   **850 mmHg ≈ 1133 гПа** — выше любого рекорда высокого давления (~1084 гПа).
    

Диапазон покрывает сценарии, которых не существует физически. Sanity-чек перестаёт ловить мусор от датчиков (залипшие нули, отрицательные значения, экстремумы прошивки).

### Что сделать

Границы — **по полю**, не единые для всего:

| Поле | Min | Max | Обоснование |
| --- | --- | --- | --- |
| `pressure_abs_mmhg` | 500 | 820 | возможная высота над уровнем моря до ~3000 м |
| `pressure_rel_mmhg` | 680 | 820 | всегда приведённое к уровню моря, реальный диапазон |
| `outdoor_temp_c` | −60 | +60 | рекорды Земли с запасом |
| `indoor_temp_c` | −20 | +50 | внутри дома не бывает −60 |
| `rh_out_pct`, `rh_in_pct` | 0 | 100 |  |
| `wind_ms`, `gust_ms` | 0 | 75 | рекордные порывы ~113 м/с, но 75 — уже явная аномалия |
| `wind_max_daily_ms` | 0 | 90 |  |
| `rain_rate_mmh` | 0 | 500 | рекордные ливни |
| `light_wm2` | 0 | 1500 | физический максимум солнечной радиации |
| `uvi` | 0 | 15 |  |

Ключевой принцип: **для каждого поля — свои границы**. Тогда «залипло на 700 mmHg на месяц» заметно по другим полям (например, `light_wm2 = 1200` ночью), даже если формально пройдёт проверку.

### Что обновить в документах

-   `weather-roadmap.md` §4 — в описании sanity-чека заменить единые границы на таблицу выше.
    
-   В коде — функция `sanity_check(row) -> (bool, list_of_failures)`, возвращает список полей, вышедших за границы.
    

### Проверка после правки

python

\# Валидный замер
assert sanity\_ok({"outdoor\_temp\_c": 5.2, "pressure\_rel\_mmhg": 760.1, ...})
\# Мусор
assert not sanity\_ok({"outdoor\_temp\_c": 5.2, "pressure\_rel\_mmhg": 0, ...})     \# ноль
assert not sanity\_ok({"outdoor\_temp\_c": 5.2, "pressure\_rel\_mmhg": 550, ...})   \# ниже физики
assert not sanity\_ok({"outdoor\_temp\_c": 5.2, "rain\_rate\_mmh": 800, ...})       \# нереальный ливень

* * *

## 3\. Push status=down при sanity-fail — принято, но нужна конкретика

Твоё решение — «при провале sanity явный push status=down, а не молчание» — правильное. Уточнение: **в `msg` пуша — конкретное поле-нарушитель**, иначе в Kuma-дашборде не поймёшь, что именно сломалось.

Формат:

text

GET <kuma\_push\_url>?status=down&msg=sanity\_fail:%20pressure\_rel\_mmhg=0&ping=<latency\_ms>

Или при нескольких нарушениях:

text

?status=down&msg=sanity\_fail:%20pressure\_rel\_mmhg=0,t\_out=999&ping=<ms>

Плюс — параллельно писать в `collector_log` со `status='sanity_fail'` и `error='pressure_rel_mmhg=0'`. Так у тебя **два независимых следа**: Kuma для алерта, БД для истории инцидентов.

* * *

## 4\. df-guard — принято, но проверку делать до `VACUUM INTO`

Ты добавил df-guard `<1.5 ГБ свободно → пропуск с алертом`. Убедиться, что проверка стоит **до** начала `VACUUM INTO`:

python

free\_gb \= shutil.disk\_usage(BACKUP\_DIR).free / 1e9
if free\_gb < 1.5:
    push\_to\_kuma(status\="down", msg\=f"backup\_skipped: disk\_full (free={free\_gb:.1f}G)")
    log\_to\_db("backup", status\="skipped", error\=f"disk\_full free={free\_gb:.1f}G")
    sys.exit(0)
\# только теперь — VACUUM INTO

Иначе `VACUUM INTO` начнёт создавать файл, упрётся в переполнение и оставит **битый бэкап + переполненный диск** — худший из вариантов.

* * *

## 5\. Чек-лист приёмки — сделать проверяемым

Из отчёта: «§4 этап A — A0–A7 + чек-лист приёмки + сценарий отката». Это правильно, но чек-лист вида «БД v2 наполняется» невозможно проверить формально. Предлагаю — **автоматизировать** одним bash-скриптом `verify_stage_a.sh`:

bash

#!/bin/bash
\# verify\_stage\_a.sh — проверка приёмки этапа A
set \-e
DB\=/home/auditbot/weather-dash/weather.db
NOW\=$(date +%s)
echo "1. Свежие замеры (≥5 за 5 мин):"
COUNT\=$(sqlite3 "$DB" "SELECT COUNT(\*) FROM weather WHERE ts > $NOW - 300")
\[ "$COUNT" \-ge 5 \] && echo "   OK ($COUNT)" || { echo "   FAIL ($COUNT)"; exit 1; }
echo "2. Коллектор логирует успех (≥55 за час):"
COUNT\=$(sqlite3 "$DB" "SELECT COUNT(\*) FROM collector\_log WHERE status='ok' AND ts > $NOW - 3600")
\[ "$COUNT" \-ge 55 \] && echo "   OK ($COUNT)" || { echo "   FAIL ($COUNT)"; exit 1; }
echo "3. Событий за сутки > 0 (FROST/SENSOR\_\* и т.п.):"
COUNT\=$(sqlite3 "$DB" "SELECT COUNT(\*) FROM events WHERE ts\_start > $NOW - 86400")
\[ "$COUNT" \-gt 0 \] && echo "   OK ($COUNT)" || echo "   WARN ($COUNT) — может быть штиль"
echo "4. Служба активна, без рестартов:"
systemctl is-active weather-collector \>/dev/null && echo "   OK" || { echo "   FAIL"; exit 1; }
NRESTARTS\=$(systemctl show weather-collector \-p NRestarts \--value)
\[ "$NRESTARTS" \-eq 0 \] && echo "   OK (0 restarts)" || echo "   WARN ($NRESTARTS restarts)"
echo "5. Sanity: нет NULL в ключевых полях за час:"
COUNT\=$(sqlite3 "$DB" "SELECT COUNT(\*) FROM weather WHERE ts > $NOW - 3600 AND (outdoor\_temp\_c IS NULL OR pressure\_rel\_mmhg IS NULL)")
\[ "$COUNT" \-eq 0 \] && echo "   OK" || { echo "   FAIL ($COUNT NULL-строк)"; exit 1; }
echo "6. WAL жив, БД не заблокирована:"
sqlite3 "$DB" "PRAGMA quick\_check" | grep \-q "ok" && echo "   OK" || { echo "   FAIL"; exit 1; }
echo "ALL CHECKS PASSED"

Приёмка = запуск скрипта, вывод `ALL CHECKS PASSED`. Это снимает вопрос «а как проверяли» на будущее.

### Что обновить в документах

-   `weather-roadmap.md` §4 — добавить файл `verify_stage_a.sh` в артефакты этапа A и указать, что приёмка = успешный прогон этого скрипта.
    
-   Аналогичные скрипты для этапа B (`verify_stage_b.sh`) — по мере готовности.
    

* * *

## 6\. Оценка roadmap — принято, одно пожелание

Структура `weather-roadmap.md` — хорошая:

-   **§2 реестр решений D1–D12** — ценная вещь. Спасает от «а помнишь, мы говорили…» через месяц.
    
-   **§3 свод правок v2.0→v2.1** — правильный инкрементальный подход.
    
-   **§4 этап A (A0–A7)** — то, что нужно.
    
-   **§7 процесс** («этап = сессия, между этапами — неделя-две наблюдения») — золотая дисциплина.
    

**Пожелание:** в §3 явно указать, что **канон документа — roadmap**, а `weatherboard_v2_analitic.md` — исторический артефакт. Если эта позиция уже есть — ок, пропустить.

* * *

## 7\. Про `weather-ui-spec.md`

Собираю отдельным документом, отдам владельцу после этапа B. **Не блокер этапа A.** Внутри будет:

-   стек (FastAPI + Jinja2 + Chart.js, без SPA);
    
-   polling 30 с, опционально SSE;
    
-   экраны (Сейчас / Сутки / Месяц / События / Прогноз);
    
-   basic auth через nginx, LAN-only;
    
-   systemd-юнит uvicorn.
    

Как только соберу — передам.

* * *

## 8\. Что делать сейчас

1.  **Не прерывать этап A.** Продолжай A1–A7 как начал.
    
2.  **После отчёта по A** — применить правки из §1 (battery whitelist) и §2 (sanity по полям), обновить `weather-roadmap.md` §3–§4.
    
3.  **Добавить** `verify_stage_a.sh` и прогнать приёмку через него.
    
4.  **Отчитаться одной строкой** вида: «Правки из вдогонку применены: battery→whitelist, sanity→по полям, verify\_stage\_a.sh создан и пройден (ALL CHECKS PASSED), roadmap §3–§4 обновлён».
    
5.  **Владельцу** — забрать `weather-ui-spec.md` (у меня), сохранить, сверить с roadmap §5.
    

Ничего архитектурного, всё — правки на полстраницы. Этап B можно начинать сразу после внесения.

* * *

**Резюме:** этап A продолжать без остановки. После отчёта — два фикса в коде (battery, sanity), один скрипт приёмки, обновление roadmap §3–§4. Всё остальное — принято и работает.