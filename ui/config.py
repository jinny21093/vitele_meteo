#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.py — конфиг weather-ui server.py (ТЗ weather-ui-spec.md v1.2.8).

Правится на хосте при установке (U7). Пароль НЕ хранится здесь — только путь
к ~/.weather-ui-credentials (600, вне репо, §3). Все пути абсолютные.
"""

# --- БД (та же, что у коллектора/материализатора, этап A/B) ---
DB_PATH = "/home/auditbot/weather-dash/weather.db"

# --- Сеть (§2.3): биндинг на явные приватные IP, НЕ 0.0.0.0. ---
# 192.168.8.146 — домашний LAN vitele; 10.147.17.101 — ZeroTier («наружу —
# только через ZeroTier», тот же ТЗ §2.3); 127.0.0.1 — loopback для
# Uptime Kuma на той же VM (v1.2.7 §2.3, U7-2): монитор целится в
# http://127.0.0.1:8089/api/health и не зависит от LAN/ZT-интерфейса.
# Каждый адрес — свой слушатель, семафор соединений общий. Порт 8089;
# занят -> 8091 (патч ТЗ v1.2.3 §12.3: 8090 занят weather-api этапа B;
# решение установщика — server.py только честно падает с ERROR в лог).
BIND_HOSTS = ("127.0.0.1", "192.168.8.146", "10.147.17.101")
PORT = 8089
PORT_FALLBACK = 8091     # §12.3 (v1.2.3): установщик U7 берёт при занятом 8089

# --- Авторизация (§3) ---
# Файл формата "weather:<pass>", chmod 600, генерируется установщиком (U7):
#   python3 -c "import secrets; print('weather:' + secrets.token_urlsafe(24))" \
#     > ~/.weather-ui-credentials && chmod 600 ~/.weather-ui-credentials
CRED_FILE = "/home/auditbot/.weather-ui-credentials"
REALM = "Weather"
RATE_LIMIT = 10          # неудачных авторизаций в минуту на IP
RATE_WINDOW = 60         # сек

# --- Ограничения (§5.0, §7) ---
MAX_CONCURRENT = 20      # семафор соединений (BoundedSemaphore)
HANDLER_TIMEOUT = 10     # per-op inactivity сокета, сек (НЕ wall-clock дедлайн;
                         # стриминг export.csv легитимен, пока сокет активен)
JSON_MAX_BYTES = 10 * 1024 * 1024   # лимит JSON-ответов; export.csv (§5.9) —
                         # не JSON и живёт своим chunked-стримом

# --- Export CSV (§5.9 v1.2.8, U6-S1) ---
# Pre-COUNT ДО стриминга: оценка байт = строки × поля × 10 (EXPORT_AVG_FIELD_BYTES
# в server.py); оценка больше лимита -> 413 + X-Export-Rows до первого байта CSV.
EXPORT_MAX_BYTES = 300 * 1024 * 1024   # 300 МБ (задание U6-S1)

# --- Пути ---
BASE_DIR = "/home/auditbot/weather-dash/ui"
STATIC_ROOT = BASE_DIR + "/static"

# --- Прочее ---
TZ_FALLBACK = 10800      # если wmeta.tz_offset_seconds недоступен (Europe/Moscow)
