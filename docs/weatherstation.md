# Weather Station — Техническое расследование

## 1\. Общая информация

| Параметр | Значение |
| --- | --- |
| IP станции | `192.168.8.101` |
| MAC-адрес | `D8:BC:38:A6:E6:14` |
| Веб-морда | Встроенный HTTP-сервер |
| Назначение | Погодная станция с Wi-Fi, штатно умеет отправлять данные только на Wunderground и Weathercloud |
| Цель расследования | Получить сырые данные локально, без облачных сервисов |

* * *

## 2\. Структура веб-интерфейса

Станция отдаёт следующие страницы:

| URL | Назначение | Обнаружено |
| --- | --- | --- |
| `/setting.html` | Настройки Wi-Fi, часового пояса, NTP, аккаунтов погодных сервисов | В инструкции |
| `/record.html` | **Live Data** — живые данные с датчиков | **Не задокументировано**, найдено через анализ `common.js` |
| `/unit.html` | Единицы измерения | Упоминается в меню |
| `/calibrate.html` | Калибровка | Упоминается в меню |
| `/upgrade.html` | Прошивка | Упоминается в меню |
| `/about.html` | О устройстве | Упоминается в меню |

### Подключаемые скрипты

-   `setting.js` — логика страницы настроек
    
-   `record.js` — логика страницы Live Data
    
-   `common.js` — общие функции, меню, инициализация XMLHttpRequest
    

* * *

## 3\. Внутренние API станции

Все запросы идут на тот же хост (`192.168.8.101`). Формат — JSON, кодировка UTF-8 (но без `charset` в заголовке, из-за чего браузер может показывать `В°C` вместо `°C`).

### 3.1. Настройки

| Метод | URL | Назначение |
| --- | --- | --- |
| `GET` | `/config?command=setup` | Получить текущие настройки |
| `POST` | `/config?command=setup` | Сохранить настройки |
| `GET` | `/config?command=connect_status` | Статус подключения к Wi-Fi |
| `GET` | `/config?command=scan_ap` | Сканировать Wi-Fi сети |
| `POST` | `/config?command=close_apmode` | Закрыть режим точки доступа |
| `POST` | `/config?command=calibrate` | Калибровка датчиков |
| `POST` | `/config?command=register` | Работа с регистрами |

### 3.2. **Живые данные (ключевой эндпоинт)**

| Метод | URL | Назначение |
| --- | --- | --- |
| `GET` | `/client?command=record` | **Полный слепок живых данных (JSON)** |
| `GET` | `/client?command=rec_refresh` | Обновление данных |

**Это основной способ получения сырых данных локально.** Никаких MITM, DNS-подмен или перехвата трафика не требуется.

* * *

## 4\. Формат ответа `client?command=record`

### 4.1. Пример реального ответа

json

{
  "sensor": \[
    {
      "title": "Indoor",
      "list": \[
        \["Temperature", "18.2", "°C"\],
        \["Humidity", "64", "%"\]
      \]
    },
    {
      "title": "Outdoor",
      "list": \[
        \["Temperature", "3.8", "°C"\],
        \["Humidity", "95", "%"\]
      \]
    },
    {
      "title": "Pressure",
      "list": \[
        \["Absolute", "760.3", "mmhg"\],
        \["Relative", "777.5", "mmhg"\]
      \]
    },
    {
      "title": "Wind Speed",
      "list": \[
        \["Max Daily Gust", "3.6", "m/s"\],
        \["Wind", "0.0", "m/s"\],
        \["Gust", "0.0", "m/s"\],
        \["Direction", "110", "°"\],
        \["Wind Average 2 Minute", "0.0", "m/s"\],
        \["Direction Average 2 Minute", "102", "°"\],
        \["Wind Average 10 Minute", "0.0", "m/s"\],
        \["Direction Average 10 Minute", "102", "°"\]
      \]
    },
    {
      "title": "Rainfall",
      "list": \[
        \["Rate", "0.0", "mm/hr"\],
        \["Hour", "0.0", "mm", "43"\],
        \["Day", "0.0", "mm", "44"\],
        \["Week", "4.5", "mm", "45"\],
        \["Month", "111.3", "mm", "46"\],
        \["Year", "737.7", "mm", "47"\],
        \["Total", "737.7", "mm", "48"\]
      \],
      "range": "Range: 0mm to 9999.9mm."
    },
    {
      "title": "Solar",
      "list": \[
        \["Light", "0.0", "w/m²"\],
        \["UVI", "0.0", ""\]
      \]
    }
  \],
  "battery": {
    "title": "Battery",
    "list": \["All battery are ok"\]
  }
}

### 4.2. Схема структуры

text

{
  "sensor": \[
    {
      "title": <string>,          // Название группы датчиков
      "list": \[
        \[<name>, <value>, <unit>\]         // 3 элемента
        \[<name>, <value>, <unit>, <id>\]   // 4 элемента (регистр для калибровки)
      \],
      "range": <string>           // опционально (например, для Rainfall)
    }
  \],
  "battery": {
    "title": <string>,
    "list": \[<string>\]
  }
}

### 4.3. Все доступные поля

| Группа | Параметр | Единица | Примечание |
| --- | --- | --- | --- |
| **Indoor** | Temperature | °C |  |
|  | Humidity | % |  |
| **Outdoor** | Temperature | °C |  |
|  | Humidity | % |  |
| **Pressure** | Absolute | mmhg |  |
|  | Relative | mmhg |  |
| **Wind Speed** | Max Daily Gust | m/s |  |
|  | Wind | m/s |  |
|  | Gust | m/s |  |
|  | Direction | ° | 0–360, угол |
|  | Wind Average 2 Minute | m/s |  |
|  | Direction Average 2 Minute | ° |  |
|  | Wind Average 10 Minute | m/s |  |
|  | Direction Average 10 Minute | ° |  |
| **Rainfall** | Rate | mm/hr |  |
|  | Hour | mm | регистр 43 |
|  | Day | mm | регистр 44 |
|  | Week | mm | регистр 45 |
|  | Month | mm | регистр 46 |
|  | Year | mm | регистр 47 |
|  | Total | mm | регистр 48 |
| **Solar** | Light | w/m² |  |
|  | UVI | (пусто) | UV-индекс |
| **battery** | — | — | Текст: "All battery are ok" |

### 4.4. Особенности данных

-   **Все значения — строки**, требуется `float()` для чисел.
    
-   **Кодировка**: ответ в UTF-8, но без заголовка `charset`. При парсинге явно указывать `r.encoding = 'utf-8'` или `r.content.decode('utf-8')`.
    
-   **Direction** — угол в градусах, а не сторона света.
    
-   **id (4-й элемент)** — внутренний регистр для калибровки, для сбора данных не нужен.
    

* * *

## 5\. Готовый сборщик данных (Python)

python

#!/usr/bin/env python3
import requests, json, sqlite3, csv, time, os
from datetime import datetime
IP \= "192.168.8.101"
URL \= f"http://{IP}/client?command=record"
INTERVAL \= 30  \# секунд
DB \= "weather.db"
CSV \= "weather.csv"
FIELDS \= \[
    "indoor\_temp\_c", "indoor\_hum\_pct",
    "outdoor\_temp\_c", "outdoor\_hum\_pct",
    "pressure\_abs\_mmhg", "pressure\_rel\_mmhg",
    "wind\_max\_daily\_ms", "wind\_ms", "gust\_ms", "wind\_dir\_deg",
    "wind\_avg2\_ms", "wind\_dir\_avg2\_deg",
    "wind\_avg10\_ms", "wind\_dir\_avg10\_deg",
    "rain\_rate\_mmh", "rain\_hour\_mm", "rain\_day\_mm",
    "rain\_week\_mm", "rain\_month\_mm", "rain\_year\_mm", "rain\_total\_mm",
    "light\_wm2", "uvi",
    "battery",
\]
def fetch():
    r \= requests.get(URL, timeout\=10)
    r.encoding \= "utf-8"
    return json.loads(r.text)
def flat(data):
    out \= {}
    for s in data.get("sensor", \[\]):
        title \= s\["title"\]
        for item in s\["list"\]:
            name, value, unit \= item\[0\], item\[1\], item\[2\]
            try:
                val \= float(value)
            except ValueError:
                val \= None
            if title in ("Indoor", "Outdoor") and name \== "Temperature":
                out\[f"{title.lower()}\_temp\_c"\] \= val
            elif title in ("Indoor", "Outdoor") and name \== "Humidity":
                out\[f"{title.lower()}\_hum\_pct"\] \= val
            elif title \== "Pressure":
                out\["pressure\_abs\_mmhg" if name \== "Absolute" else "pressure\_rel\_mmhg"\] \= val
            elif title \== "Wind Speed":
                m \= {
                    "Max Daily Gust": "wind\_max\_daily\_ms",
                    "Wind": "wind\_ms", "Gust": "gust\_ms",
                    "Direction": "wind\_dir\_deg",
                    "Wind Average 2 Minute": "wind\_avg2\_ms",
                    "Direction Average 2 Minute": "wind\_dir\_avg2\_deg",
                    "Wind Average 10 Minute": "wind\_avg10\_ms",
                    "Direction Average 10 Minute": "wind\_dir\_avg10\_deg",
                }
                if name in m: out\[m\[name\]\] \= val
            elif title \== "Rainfall":
                m \= {
                    "Rate": "rain\_rate\_mmh", "Hour": "rain\_hour\_mm",
                    "Day": "rain\_day\_mm", "Week": "rain\_week\_mm",
                    "Month": "rain\_month\_mm", "Year": "rain\_year\_mm",
                    "Total": "rain\_total\_mm",
                }
                if name in m: out\[m\[name\]\] \= val
            elif title \== "Solar":
                if name \== "Light": out\["light\_wm2"\] \= val
                elif name \== "UVI": out\["uvi"\] \= val
    b \= data.get("battery", {})
    if "list" in b:
        out\["battery"\] \= "; ".join(b\["list"\])
    return out
def deg\_to\_cardinal(d):
    dirs \= \["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"\]
    return dirs\[int((d + 11.25) / 22.5) % 16\]
def init\_db():
    con \= sqlite3.connect(DB)
    cols \= ", ".join(f"{f} REAL" for f in FIELDS if f != "battery")
    con.execute(f"CREATE TABLE IF NOT EXISTS weather (ts TEXT, {cols}, battery TEXT)")
    con.commit()
    return con
def save\_db(con, ts, row):
    placeholders \= ", ".join("?" for \_ in FIELDS)
    cols \= ", ".join(FIELDS)
    vals \= \[row.get(f) for f in FIELDS\]
    con.execute(f"INSERT INTO weather (ts, {cols}) VALUES (?, {placeholders})",
                \[ts\] + vals)
    con.commit()
def init\_csv():
    if not os.path.exists(CSV):
        with open(CSV, "w", newline\="", encoding\="utf-8") as f:
            csv.writer(f).writerow(\["ts"\] + FIELDS)
def save\_csv(ts, row):
    with open(CSV, "a", newline\="", encoding\="utf-8") as f:
        csv.writer(f).writerow(\[ts\] + \[row.get(k) for k in FIELDS\])
def main():
    con \= init\_db()
    init\_csv()
    while True:
        try:
            raw \= fetch()
            row \= flat(raw)
            ts \= datetime.now().isoformat(timespec\="seconds")
            save\_db(con, ts, row)
            save\_csv(ts, row)
            print(f"\[{ts}\] T\_out={row.get('outdoor\_temp\_c')}°C "
                  f"RH={row.get('outdoor\_hum\_pct')}% "
                  f"P={row.get('pressure\_rel\_mmhg')}mmHg "
                  f"Wind={row.get('wind\_ms')}m/s "
                  f"Rain\_day={row.get('rain\_day\_mm')}mm")
        except Exception as e:
            print("Error:", e)
        time.sleep(INTERVAL)
if \_\_name\_\_ \== "\_\_main\_\_":
    main()

**Зависимости:** `pip install requests`

**Проверка одной командой:**

bash

curl "http://192.168.8.101/client?command=record"

* * *

## 6\. Что уже можно делать с этими данными

1.  **Локальное хранение** — SQLite / CSV / InfluxDB (код выше уже пишет в первые два).
    
2.  **Home Assistant** — через MQTT-мост или REST-сенсоры.
    
3.  **Grafana** — из SQLite или InfluxDB, готовые дашборды.
    
4.  **Свой дашборд** — Flask/FastAPI + графики (Chart.js, Plotly).
    
5.  **Любой погодный сервис** — отправка в OpenWeatherMap, Windy, Weather Underground и т.п. по их HTTP API.
    
6.  **Telegram-бот** с алертами (например, «ветер > 10 м/с» или «дождь начался»).
    
7.  **Prometheus exporter** — для мониторинга через Grafana.
    

* * *

## 7\. Ограничения и нюансы

-   **Интервал опроса**: не чаще 10 секунд. Внутри станции датчики обновляются реже (обычно 16–60 с).
    
-   **Кодировка**: ответ UTF-8, но без `charset` в заголовке — задавать явно.
    
-   **Числа в строках**: все значения приходят как строки, нужен `float()`.
    
-   **Регистры калибровки** (4-й элемент в `list` для Rainfall) — внутренние ID, для сбора данных не требуются.
    
-   **Единицы измерения** зашиты в ответе: `°C`, `%`, `mmhg`, `m/s`, `mm/hr`, `mm`, `w/m²`, `°`.
    
-   **`rec_refresh` vs `record`**: `record` даёт полный слепок, `rec_refresh` — обновление. Для сбора проще всегда звать `record`.
    

* * *

## 8\. Что осталось за кадром (не критично)

-   `POST /config?command=calibrate` — формат: `{"Calibrate":{"id43":"0", ...}}`, ответ: `{"msg": "...", "result": true/false, "id": "..."}`
    
-   `POST /config?command=register` — формат: `{"Register":"43"}`, управление регистрами (старший бит = Stop/Register).
    

Для сбора живых данных это не нужно.

* * *

## 9\. Итог

**Станция отдаёт полный JSON с живыми данными локально по адресу:**

text

GET http://192.168.8.101/client?command=record

**Облачные сервисы (Wunderground, Weathercloud) для сбора данных не нужны.** Всё, что станция туда шлёт, доступно напрямую.

**Дальнейшие шаги для следующего агента:**

1.  Развернуть сборщик (Python-скрипт выше) на любом устройстве в той же сети (Raspberry Pi, NAS, роутер с OpenWrt).
    
2.  Выбрать хранилище: SQLite (просто) / InfluxDB (для графиков) / PostgreSQL (для продакшена).
    
3.  Настроить визуализацию: Grafana / Home Assistant / собственный веб-дашборд.
    
4.  По желанию — настроить экспорт в другие сервисы и алерты.
    

**Ключевой файл для дальнейшего анализа (если понадобится):**

-   `record.js` — содержит всю логику парсинга JSON и работы с эндпоинтами.
    
-   `common.js` — общие функции, меню, `initXML`.
    
-   `settings.js` — конфигурация и работа с `config?command=*`.
### Возможные варианты battery-строк (weather-7, 16.09)

Станция возвращает `battery.list` — список текстовых статусов. Единственный известный
OK-вариант на сегодня: **"All battery are ok"** (проверено 15.09 на живой станции).

Политика оценки — whitelist в коллекторе (`BATTERY_OK_PATTERNS` в
weather_collector.py): OK только если raw содержит известную OK-строку целиком;
всё остальное — LOW → событие `BATTERY_LOW`. Это fail-safe: ложная тревога лучше
пропущенного разряда. Regex `\bok\b` не используется — он матчит и «not ok»
(DeepSeek, «вдогонка» §1).

Когда станция покажет реальный LOW/заряд — записать ТОЧНУЮ строку здесь и добавить
в `BATTERY_OK_PATTERNS` (если это OK-вариант). Пустой/отсутствующий battery-текст
тоже считается LOW (проверять коллектор при смене прошивки).
